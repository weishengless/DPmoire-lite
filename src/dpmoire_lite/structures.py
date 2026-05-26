from __future__ import annotations

import copy
import re
import tempfile
from pathlib import Path

import numpy as np
from ase import Atoms
from ase.build import make_supercell, sort, stack
from ase.constraints import FixedLine
from ase.io.vasp import read_vasp, write_vasp


def generate_stackings(n_sectors: tuple[int, int]) -> list[tuple[int, int]]:
    nx, ny = n_sectors
    return [(i, j) for i in range(nx) for j in range(ny)]


def supercell_matrix(sc: tuple[int, int]) -> list[list[int]]:
    sc_x, sc_y = sc
    return [[sc_x, 0, 0], [0, sc_y, 0], [0, 0, 1]]


def rewrite_contcar_as_poscar(contcar: Path, poscar: Path) -> None:
    atoms = read_vasp(contcar)
    write_vasp(poscar, atoms=atoms, direct=True, sort=False)


def normalize_symbol_label(label: str) -> str:
    match = re.match(r"^([A-Z][a-z]?)", label.strip())
    if match is None:
        return label.strip()
    return match.group(1)


class StructureHandler:
    """Handle crystal structures during DPmoire-lite preprocessing."""

    def __init__(self, input_dir: Path, work_dir: Path, n_sectors: tuple[int, int], d: float):
        self.input_dir = Path(input_dir)
        self.work_dir = Path(work_dir)
        self.n_sectors = n_sectors
        self.d = d
        self.top_atoms: Atoms | None = None
        self.bot_atoms: Atoms | None = None
        self.top_indexes: list[int] = []
        self.bot_indexes: list[int] = []
        self.new_struct: Atoms | None = None
        self.read_all_layers()
        self.new_struct, self.top_indexes, self.bot_indexes = self.build_new_struct(d=self.d)

    def read_atoms(self, in_file: Path | str) -> Atoms:
        path = Path(in_file)
        try:
            return read_vasp(path)
        except KeyError:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            if len(lines) < 7:
                raise
            symbol_line_idx = 5
            labels = lines[symbol_line_idx].split()
            normalized = [normalize_symbol_label(label) for label in labels]
            if normalized == labels:
                raise
            lines[symbol_line_idx] = "  " + "  ".join(normalized) + "\n"
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".normalized",
                delete=False,
            ) as handle:
                handle.write("".join(lines))
                tmp_file = Path(handle.name)
            try:
                return read_vasp(tmp_file)
            finally:
                tmp_file.unlink(missing_ok=True)

    def read_all_layers(self) -> None:
        self.top_atoms = self.read_atoms(self.input_dir / "top_layer.poscar")
        self.bot_atoms = self.read_atoms(self.input_dir / "bot_layer.poscar")

    def find_layer_idx(self, atoms: Atoms) -> tuple[list[int], list[int]]:
        cell_mat = atoms.get_cell().array
        frac_mat = np.linalg.inv(cell_mat)
        frac_pos = np.dot(atoms.get_positions(), frac_mat)
        top_idx = []
        bot_idx = []
        for idx, pos in enumerate(frac_pos):
            if pos[2] > 0.5:
                top_idx.append(idx)
            else:
                bot_idx.append(idx)
        return top_idx, bot_idx

    def _generate_all_stackings(self):
        yield from generate_stackings(self.n_sectors)

    def _shift_primitive(self, i: int, j: int) -> Atoms:
        atoms = copy.deepcopy(self.new_struct)
        nx, ny = self.n_sectors
        delta = i / nx * atoms.get_cell().array[0] + j / ny * atoms.get_cell().array[1]
        pos = atoms.get_positions()
        for idx in self.top_indexes:
            pos[idx] += delta
        atoms.set_positions(pos)
        return atoms

    def find_sym_reduced_stackings(self, prec: float = 0.0001) -> list[tuple[int, int]]:
        from pymatgen.analysis.structure_matcher import StructureMatcher
        from pymatgen.io.ase import AseAtomsAdaptor

        adaptor = AseAtomsAdaptor()
        matcher = StructureMatcher(ltol=prec, stol=prec, angle_tol=prec)
        unique_structs = []
        unique_stackings: list[tuple[int, int]] = []
        for stacking in self._generate_all_stackings():
            atoms = self._shift_primitive(*stacking)
            structure = adaptor.get_structure(atoms)
            matched = any(matcher.fit(ref, structure) for ref in unique_structs)
            if not matched:
                unique_structs.append(structure)
                unique_stackings.append(stacking)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        np.savetxt(self.work_dir / "sym_reduced_stackings.txt", np.array(unique_stackings), fmt="%d")
        return unique_stackings

    def build_new_struct(self, d: float) -> tuple[Atoms, list[int], list[int]]:
        top_cell_mat = self.top_atoms.get_cell().array
        top_cell_len = self.top_atoms.get_cell().lengths()
        bot_cell_mat = self.bot_atoms.get_cell().array
        bot_cell_len = self.bot_atoms.get_cell().lengths()
        fractional_pos_top = np.dot(self.top_atoms.get_positions(), np.linalg.inv(top_cell_mat))
        fractional_pos_bot = np.dot(self.bot_atoms.get_positions(), np.linalg.inv(bot_cell_mat))
        new_cell_mat = np.array([top_cell_mat[k] * (1 + bot_cell_len[k] / top_cell_len[k]) / 2 for k in range(3)])
        c_top = np.mean([k[2] for k in fractional_pos_top])
        c_bot = np.mean([k[2] for k in fractional_pos_bot])
        total_c = self.top_atoms.get_cell().lengths()[2] + self.bot_atoms.get_cell().lengths()[2]
        for i, _ in enumerate(fractional_pos_top):
            fractional_pos_top[i][2] += 0.5 - c_top + d / total_c
        for i, _ in enumerate(fractional_pos_bot):
            fractional_pos_bot[i][2] += 0.5 - c_bot - d / total_c
        new_pos_top = [[item[0], item[1], item[2]] for item in np.dot(fractional_pos_top, new_cell_mat)]
        new_pos_bot = [[item[0], item[1], item[2]] for item in np.dot(fractional_pos_bot, new_cell_mat)]
        new_symbols = self.top_atoms.get_chemical_symbols() + self.bot_atoms.get_chemical_symbols()
        atoms = sort(Atoms(positions=new_pos_top + new_pos_bot, symbols=new_symbols, cell=new_cell_mat, pbc=[True, True, True]))
        top_idx, bot_idx = self.find_layer_idx(atoms)
        return atoms, top_idx, bot_idx

    def shift_atoms(self, i: int, j: int, c_constrain: bool = True, sc: tuple[int, int] = (2, 2)) -> Atoms:
        atoms = self._shift_primitive(i, j)
        atoms_sc = sort(make_supercell(prim=atoms, P=supercell_matrix(sc)))
        top_idx, bot_idx = self.find_layer_idx(atoms_sc)
        if c_constrain:
            cons = FixedLine([top_idx[0], bot_idx[0]], direction=atoms_sc.cell.array[2] / atoms_sc.cell.lengths()[2])
            atoms_sc.set_constraint(cons)
        return atoms_sc

    def shift_primitive_atoms(self, i: int, j: int, c_constrain: bool = True) -> Atoms:
        atoms = self._shift_primitive(i, j)
        if c_constrain:
            top_idx, bot_idx = self.find_layer_idx(atoms)
            cons = FixedLine([top_idx[0], bot_idx[0]], direction=atoms.cell.array[2] / atoms.cell.lengths()[2])
            atoms.set_constraint(cons)
        return atoms

    def shift(self, i: int, j: int, out_dir: Path | str, c_constrain: bool = True, sc: tuple[int, int] = (2, 2)) -> None:
        target_dir = Path(out_dir) / f"{i}_{j}"
        target_dir.mkdir(parents=True, exist_ok=True)
        atoms_sc = self.shift_atoms(i, j, c_constrain, sc)
        write_vasp(target_dir / "POSCAR", atoms=atoms_sc)

    def shift_primitive(self, i: int, j: int, out_dir: Path | str, c_constrain: bool = True) -> None:
        target_dir = Path(out_dir) / f"{i}_{j}"
        target_dir.mkdir(parents=True, exist_ok=True)
        atoms = self.shift_primitive_atoms(i, j, c_constrain)
        write_vasp(target_dir / "POSCAR", atoms=atoms)

    def shift_all(
        self,
        out_dir: Path | str,
        c_constrain: bool = True,
        sc: tuple[int, int] = (2, 2),
        stackings: list[tuple[int, int]] | None = None,
    ) -> None:
        for i, j in stackings if stackings is not None else self._generate_all_stackings():
            self.shift(i, j, out_dir, c_constrain=c_constrain, sc=sc)

    def shift_all_primitive(
        self,
        out_dir: Path | str,
        c_constrain: bool = True,
        stackings: list[tuple[int, int]] | None = None,
    ) -> None:
        for i, j in stackings if stackings is not None else self._generate_all_stackings():
            self.shift_primitive(i, j, out_dir, c_constrain=c_constrain)

    def expand_structure_file(self, infile: Path | str, outfile: Path | str, sc: tuple[int, int], sort_atoms: bool = True) -> None:
        atoms = self.read_atoms(infile)
        atoms_sc = make_supercell(prim=atoms, P=supercell_matrix(sc))
        if sort_atoms:
            atoms_sc = sort(atoms_sc)
        write_vasp(outfile, atoms_sc)

    def make_twist_struct(self, N_min: int, N_max: int, out_dir: Path | str):
        from ._find_homo_twist import adjust_atoms_d, search_twist

        top_atoms = copy.deepcopy(self.top_atoms)
        bot_atoms = copy.deepcopy(self.bot_atoms)
        top_atoms, bot_atoms = adjust_atoms_d(top_atoms, bot_atoms, self.d)
        angle_list, mat_list = search_twist(N_min, N_max)
        out_atoms_list = []
        base_out_dir = Path(out_dir)
        for idx, mat in enumerate(mat_list):
            top_sc = make_supercell(top_atoms, P=mat[0])
            bot_sc = make_supercell(bot_atoms, P=mat[1])
            out_atoms = stack(bot_sc, top_sc, maxstrain=None, reorder=True)
            out_atoms_list.append(out_atoms)
            target_dir = base_out_dir / angle_list[idx]
            target_dir.mkdir(parents=True, exist_ok=True)
            write_vasp(target_dir / "POSCAR", out_atoms)
        return angle_list, out_atoms_list
