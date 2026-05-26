from __future__ import annotations

import copy
import re
from pathlib import Path

import numpy as np
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator
from ase.io import read as ase_read
from ase.io import write as ase_write
from ase.units import GPa

from .outcar import read_outcar_frames


def count_ml_ab_configs(path: Path) -> int:
    with Path(path).open(encoding="utf-8") as infile:
        for line_number, line in enumerate(infile):
            if line_number == 4:
                return int(line.split()[0])
    raise ValueError(f"Could not read ML_AB configuration count from {path}")


class Dataset:
    def __init__(self):
        self.n_configs = 0
        self.data: list[Atoms] = []

    def __getitem__(self, index: int) -> Atoms:
        return self.data[index]

    def __setitem__(self, index: int, value: Atoms) -> int:
        self.data[index] = copy.deepcopy(value)
        return index

    def __iter__(self):
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        return str(list(self))

    def load_ml_ab(self, path: Path, skip_configs: int = 0) -> None:
        with Path(path).open(encoding="utf-8") as infile:
            for line_number, _line in enumerate(infile):
                if line_number == 4:
                    break
            dataset = infile.read()

        structures = re.split(r"\n+\s*Configuration num.\s*\d+\n", dataset)
        structures.pop(0)

        for structure in structures[skip_configs:]:
            parts = re.split(r"=+\n", structure)
            n_type = int(parts[2].split("\n")[2])
            n_atom = int(parts[3].split("\n")[2])

            elems = []
            n_elem = []
            for line in parts[3].split("\n")[6 : 6 + n_type]:
                fields = line.split()
                elems.append(fields[0])
                n_elem.append(int(fields[1]))

            lattice_vectors = []
            for line in parts[5].split("\n")[2:5]:
                lattice_vectors.append([float(value) for value in line.split()])

            positions = []
            symbols = []
            elem_index = 0
            for line in parts[6].split("\n")[2 : 2 + n_atom]:
                positions.append([float(value) for value in line.split()])
                if n_elem[elem_index] > 0:
                    n_elem[elem_index] -= 1
                else:
                    elem_index += 1
                    n_elem[elem_index] -= 1
                symbols.append(elems[elem_index])

            energy = float(parts[7].split("\n")[2])

            forces = []
            for line in parts[8].split("\n")[2 : 2 + n_atom]:
                forces.append([float(value) for value in line.split()])

            stress_lines = parts[9].split("\n")
            xx_yy_zz = stress_lines[4].split()
            xy_yz_zx = stress_lines[8].split()

            kbar = 0.1 * GPa
            stress_tensor = -np.array(
                [
                    [float(xx_yy_zz[0]), float(xy_yz_zx[0]), float(xy_yz_zx[2])],
                    [float(xy_yz_zx[0]), float(xx_yy_zz[1]), float(xy_yz_zx[1])],
                    [float(xy_yz_zx[2]), float(xy_yz_zx[1]), float(xx_yy_zz[2])],
                ]
            ) * kbar

            atoms = Atoms(symbols=symbols, cell=lattice_vectors, positions=positions, pbc=True)
            atoms.calc = SinglePointCalculator(
                atoms,
                energy=energy,
                forces=forces,
                stress=stress_tensor,
            )
            self.data.append(atoms)
            self.n_configs += 1

    def load_outcar(self, path: Path, freq: int) -> None:
        for index, structure in enumerate(read_outcar_frames(Path(path))):
            if index % freq == 0:
                self.add_atoms(structure)

    def load_extxyz(self, path: Path) -> None:
        for structure in ase_read(Path(path), format="extxyz", index=":"):
            self.add_atoms(structure)

    def add_atoms(self, structure: Atoms) -> None:
        if not isinstance(structure, Atoms):
            raise TypeError("Structure is not Atoms object")

        stored_structure = Atoms(
            positions=structure.get_positions(),
            symbols=structure.get_chemical_symbols(),
            cell=structure.get_cell(),
            pbc=structure.get_pbc(),
        )
        stored_structure.calc = SinglePointCalculator(
            stored_structure,
            energy=structure.get_potential_energy(apply_constraint=False),
            forces=structure.get_forces(apply_constraint=False),
            stress=structure.get_stress(apply_constraint=False),
        )
        self.data.append(stored_structure)
        self.n_configs += 1

    def save_extxyz(self, path: Path) -> None:
        ase_write(Path(path), self.data, format="extxyz")
