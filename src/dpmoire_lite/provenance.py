from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from ase import Atoms
from ase.io.vasp import read_vasp


CELL_ATOL = 1e-8
CELL_RTOL = 1e-8
TRANSFORM_ATOL = 1e-8


@dataclass(frozen=True)
class StructureIdentity:
    sha256: str
    atom_count: int
    ordered_elements: tuple[str, ...]
    composition: tuple[tuple[str, int], ...]
    cell: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class InplaneSupercellRelation:
    matrix: tuple[tuple[int, int], tuple[int, int]]
    determinant: int
    atom_multiplier: int


def structure_identity(path: Path, atoms: Atoms | None = None) -> StructureIdentity:
    path = Path(path)
    if atoms is None:
        atoms = read_vasp(path)

    ordered_elements = tuple(atoms.get_chemical_symbols())
    counts: dict[str, int] = {}
    for symbol in ordered_elements:
        counts[symbol] = counts.get(symbol, 0) + 1

    cell = tuple(
        tuple(float(value) for value in row)
        for row in np.asarray(atoms.cell.array, dtype=float)
    )
    return StructureIdentity(
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        atom_count=len(atoms),
        ordered_elements=ordered_elements,
        composition=tuple(counts.items()),
        cell=cell,
    )


def structure_identity_differences(
    expected: StructureIdentity,
    actual: StructureIdentity,
) -> tuple[str, ...]:
    differences: list[str] = []
    if expected.sha256 != actual.sha256:
        differences.append("sha256")
    if expected.atom_count != actual.atom_count:
        differences.append("atom_count")
    if expected.ordered_elements != actual.ordered_elements:
        differences.append("ordered_elements")
    if expected.composition != actual.composition:
        differences.append("composition")
    if not np.allclose(
        np.asarray(expected.cell),
        np.asarray(actual.cell),
        atol=CELL_ATOL,
        rtol=CELL_RTOL,
    ):
        differences.append("cell")
    return tuple(differences)


def inplane_supercell_relation(
    primitive: Atoms,
    candidate: Atoms,
    *,
    atol: float = TRANSFORM_ATOL,
    rtol: float = CELL_RTOL,
) -> InplaneSupercellRelation:
    primitive_count = len(primitive)
    candidate_count = len(candidate)
    if primitive_count <= 0:
        raise ValueError("Primitive structure must contain at least one atom")
    if candidate_count % primitive_count:
        raise ValueError(
            "Candidate atom count is not an integer multiple of the primitive atom count"
        )

    primitive_cell = np.asarray(primitive.cell.array[:2, :2], dtype=float)
    candidate_cell = np.asarray(candidate.cell.array[:2, :2], dtype=float)
    try:
        transform = candidate_cell @ np.linalg.inv(primitive_cell)
    except np.linalg.LinAlgError as exc:
        raise ValueError("Primitive in-plane cell is singular") from exc

    rounded = np.rint(transform)
    if not np.allclose(transform, rounded, atol=atol, rtol=0.0):
        raise ValueError(
            f"In-plane cell transform must be integer within atol={atol}; got {transform.tolist()}"
        )

    matrix = (
        (int(rounded[0, 0]), int(rounded[0, 1])),
        (int(rounded[1, 0]), int(rounded[1, 1])),
    )
    reconstructed = rounded @ primitive_cell
    if not np.allclose(reconstructed, candidate_cell, atol=atol, rtol=rtol):
        raise ValueError(
            f"In-plane cell does not match its integer transform within atol={atol}"
        )

    determinant = matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
    if determinant <= 0:
        raise ValueError(f"In-plane cell transform has invalid determinant {determinant}")

    atom_multiplier = candidate_count // primitive_count
    if determinant != atom_multiplier:
        raise ValueError(
            "In-plane cell transform determinant "
            f"{determinant} does not match atom multiplier {atom_multiplier}"
        )

    return InplaneSupercellRelation(
        matrix=matrix,
        determinant=determinant,
        atom_multiplier=atom_multiplier,
    )


infer_inplane_supercell_relation = inplane_supercell_relation
