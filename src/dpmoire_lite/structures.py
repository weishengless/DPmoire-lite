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


TOP_LAYER_TAG = 1
BOT_LAYER_TAG = 2


def validate_cartesian_z_slab_cell(atoms: Atoms, label: str) -> None:
    cell = atoms.cell.array
    tilted_in_plane = not np.allclose(cell[:2, 2], 0.0, atol=1e-8)
    tilted_c = not np.allclose(cell[2, :2], 0.0, atol=1e-8)
    invalid_c = float(cell[2, 2]) <= 1e-8
    if tilted_in_plane or tilted_c or invalid_c:
        raise ValueError(
            f"{label} must use a slab cell with in-plane vectors in xy and c aligned to Cartesian z"
        )


def layer_indices_from_tags(atoms: Atoms) -> tuple[list[int], list[int]] | None:
    tags = atoms.get_tags()
    top_idx = [idx for idx, tag in enumerate(tags) if tag == TOP_LAYER_TAG]
    bot_idx = [idx for idx, tag in enumerate(tags) if tag == BOT_LAYER_TAG]
    if top_idx and bot_idx and len(top_idx) + len(bot_idx) == len(atoms):
        return top_idx, bot_idx
    return None


def layer_indices_from_z_gap(atoms: Atoms) -> tuple[list[int], list[int]]:
    if len(atoms) < 2:
        return list(range(len(atoms))), []
    order = np.argsort(atoms.positions[:, 2])
    sorted_z = atoms.positions[order, 2]
    gaps = np.diff(sorted_z)
    if len(gaps) == 0 or np.allclose(gaps, 0.0, atol=1e-8):
        return layer_indices_from_fractional_midplane(atoms)
    split = int(np.argmax(gaps)) + 1
    bot_idx = sorted(int(idx) for idx in order[:split])
    top_idx = sorted(int(idx) for idx in order[split:])
    if not top_idx or not bot_idx:
        return layer_indices_from_fractional_midplane(atoms)
    return top_idx, bot_idx


def layer_indices_from_fractional_midplane(atoms: Atoms) -> tuple[list[int], list[int]]:
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


def z_bounds(atoms: Atoms, indexes: list[int]) -> tuple[float, float]:
    z = atoms.positions[indexes, 2]
    return float(z.min()), float(z.max())


def z_thickness(atoms: Atoms, indexes: list[int]) -> float:
    z_min, z_max = z_bounds(atoms, indexes)
    return z_max - z_min


def reference_indexes(
    atoms: Atoms,
    indexes: list[int],
    selector: str | tuple[str, ...],
    layer_name: str,
) -> list[int]:
    if selector == "all":
        return list(indexes)
    allowed = {selector} if isinstance(selector, str) else set(selector)
    symbols = atoms.get_chemical_symbols()
    selected = [idx for idx in indexes if symbols[idx] in allowed]
    if not selected:
        available = sorted({symbols[idx] for idx in indexes})
        raise ValueError(
            f"No atoms in {layer_name} matched d_reference selector {sorted(allowed)}; "
            f"available symbols: {available}"
        )
    return selected


def mean_z(atoms: Atoms, indexes: list[int]) -> float:
    return float(atoms.positions[indexes, 2].mean())


def translate_indexes_z(atoms: Atoms, indexes: list[int], delta_z: float) -> None:
    positions = atoms.get_positions()
    positions[indexes, 2] += delta_z
    atoms.set_positions(positions)


def _cell_z_length(atoms: Atoms) -> float:
    c_z = float(atoms.cell.array[2, 2])
    if abs(c_z) > 1e-12:
        return abs(c_z)
    return float(atoms.cell.lengths()[2])


def ensure_cell_contains_z_range(atoms: Atoms, indexes: list[int], vacuum_budget: float) -> None:
    z = atoms.positions[indexes, 2]
    z_min = float(z.min())
    z_max = float(z.max())
    current_c = _cell_z_length(atoms)
    target_c = max(current_c, z_max - z_min + vacuum_budget)
    if target_c > current_c:
        new_cell = atoms.cell.array.copy()
        new_cell[2] *= target_c / current_c
        atoms.set_cell(new_cell, scale_atoms=False)
    final_c = _cell_z_length(atoms)
    z_center = float(atoms.positions[indexes, 2].min() + atoms.positions[indexes, 2].max()) / 2
    translate_indexes_z(atoms, indexes, final_c / 2 - z_center)


def apply_interlayer_spacing(
    atoms: Atoms,
    top_idx: list[int],
    bot_idx: list[int],
    d: float,
    d_mode: str,
    d_reference: dict[str, str | tuple[str, ...]] | None,
) -> None:
    validate_cartesian_z_slab_cell(atoms, "stacked structure")
    top_height = z_thickness(atoms, top_idx)
    bot_height = z_thickness(atoms, bot_idx)
    current_c = _cell_z_length(atoms)
    vacuum_budget = max(current_c - top_height - bot_height, 0.0)

    center_z = current_c / 2
    if d_mode == "surface_gap":
        top_anchor, _ = z_bounds(atoms, top_idx)
        _, bot_anchor = z_bounds(atoms, bot_idx)
    elif d_mode == "reference_plane_gap":
        top_selector = (d_reference or {}).get("top", "all")
        bot_selector = (d_reference or {}).get("bot", "all")
        top_ref = reference_indexes(atoms, top_idx, top_selector, "top layer")
        bot_ref = reference_indexes(atoms, bot_idx, bot_selector, "bot layer")
        top_anchor = mean_z(atoms, top_ref)
        bot_anchor = mean_z(atoms, bot_ref)
    else:
        raise ValueError(f"Unsupported d_mode: {d_mode}")

    translate_indexes_z(atoms, bot_idx, center_z - d / 2 - bot_anchor)
    translate_indexes_z(atoms, top_idx, center_z + d / 2 - top_anchor)
    ensure_cell_contains_z_range(atoms, top_idx + bot_idx, vacuum_budget)


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

    def __init__(
        self,
        input_dir: Path,
        work_dir: Path,
        n_sectors: tuple[int, int],
        d: float,
        d_mode: str = "surface_gap",
        d_reference: dict[str, str | tuple[str, ...]] | None = None,
    ):
        self.input_dir = Path(input_dir)
        self.work_dir = Path(work_dir)
        self.n_sectors = n_sectors
        self.d = d
        self.d_mode = d_mode
        self.d_reference = d_reference
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
        validate_cartesian_z_slab_cell(self.top_atoms, "top_layer.poscar")
        validate_cartesian_z_slab_cell(self.bot_atoms, "bot_layer.poscar")

    def find_layer_idx(self, atoms: Atoms) -> tuple[list[int], list[int]]:
        tagged = layer_indices_from_tags(atoms)
        if tagged is not None:
            return tagged

        return layer_indices_from_z_gap(atoms)

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

        top_positions = np.dot(fractional_pos_top, new_cell_mat)
        bot_positions = np.dot(fractional_pos_bot, new_cell_mat)
        top_positions[:, 2] = self.top_atoms.get_positions()[:, 2]
        bot_positions[:, 2] = self.bot_atoms.get_positions()[:, 2]

        top_atoms = Atoms(
            positions=top_positions,
            symbols=self.top_atoms.get_chemical_symbols(),
            cell=new_cell_mat,
            pbc=[True, True, True],
        )
        bot_atoms = Atoms(
            positions=bot_positions,
            symbols=self.bot_atoms.get_chemical_symbols(),
            cell=new_cell_mat,
            pbc=[True, True, True],
        )
        top_atoms.set_tags([TOP_LAYER_TAG] * len(top_atoms))
        bot_atoms.set_tags([BOT_LAYER_TAG] * len(bot_atoms))

        atoms = top_atoms + bot_atoms
        top_idx = list(range(len(top_atoms)))
        bot_idx = list(range(len(top_atoms), len(atoms)))
        apply_interlayer_spacing(atoms, top_idx, bot_idx, d, self.d_mode, self.d_reference)

        atoms = sort(atoms)
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
        from ._find_homo_twist import search_twist

        angle_list, mat_list = search_twist(N_min, N_max)
        out_atoms_list = []
        base_out_dir = Path(out_dir)
        for idx, mat in enumerate(mat_list):
            top_sc = make_supercell(copy.deepcopy(self.top_atoms), P=mat[0])
            bot_sc = make_supercell(copy.deepcopy(self.bot_atoms), P=mat[1])
            out_atoms = stack(bot_sc, top_sc, maxstrain=None, reorder=False)
            bot_idx = list(range(len(bot_sc)))
            top_idx = list(range(len(bot_sc), len(out_atoms)))
            out_atoms.set_tags([BOT_LAYER_TAG] * len(bot_idx) + [TOP_LAYER_TAG] * len(top_idx))
            apply_interlayer_spacing(out_atoms, top_idx, bot_idx, self.d, self.d_mode, self.d_reference)
            out_atoms = sort(out_atoms)
            out_atoms_list.append(out_atoms)
            target_dir = base_out_dir / angle_list[idx]
            target_dir.mkdir(parents=True, exist_ok=True)
            write_vasp(target_dir / "POSCAR", out_atoms)
        return angle_list, out_atoms_list
