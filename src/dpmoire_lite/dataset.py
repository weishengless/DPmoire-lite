from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator
from ase.io import read as ase_read
from ase.io import write as ase_write
from ase.units import GPa

from .mlab import MlabConfiguration, MlabParseError, parse_mlab
from .outcar import open_outcar_frames


def count_ml_ab_configs(path: Path) -> int:
    source_path = Path(path)
    result = parse_mlab(source_path)
    if result.status != "complete":
        raise MlabParseError(
            source_path,
            configuration_number=None,
            block="header",
            reason="ML_AB configuration count requires a complete source",
        )
    return result.declared_count


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
        source_path = Path(path)
        if skip_configs < 0:
            raise ValueError("skip_configs must be non-negative")

        result = parse_mlab(source_path)
        if result.status != "complete":
            raise MlabParseError(
                source_path,
                configuration_number=result.discarded_configuration_number,
                block=result.discarded_block or "configuration",
                reason="Dataset compatibility loader rejects partial ML_AB sources",
            )
        if skip_configs > len(result.configurations):
            raise MlabParseError(
                source_path,
                configuration_number=None,
                block="dataset",
                reason=(
                    f"skip_configs {skip_configs} exceeds complete configuration count "
                    f"{len(result.configurations)}"
                ),
            )

        converted = [
            atoms_from_mlab_configuration(configuration)
            for configuration in result.configurations[skip_configs:]
        ]
        self.data.extend(converted)
        self.n_configs += len(converted)

    def load_outcar(self, path: Path, freq: int) -> None:
        if freq <= 0:
            raise ValueError("freq must be a positive integer")

        with open_outcar_frames(Path(path)) as frames:
            for index, structure in enumerate(frames):
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


def atoms_from_mlab_configuration(configuration: MlabConfiguration) -> Atoms:
    symbols = [
        element
        for element, count in zip(configuration.elements, configuration.counts)
        for _ in range(count)
    ]
    xx, yy, zz, xy, yz, zx = configuration.stress_kbar
    stress_tensor = -np.array(
        [
            [xx, xy, zx],
            [xy, yy, yz],
            [zx, yz, zz],
        ]
    ) * (0.1 * GPa)

    atoms = Atoms(
        symbols=symbols,
        cell=configuration.lattice,
        positions=configuration.positions,
        pbc=True,
    )
    atoms.calc = SinglePointCalculator(
        atoms,
        energy=configuration.energy,
        forces=configuration.forces,
        stress=stress_tensor,
    )
    return atoms
