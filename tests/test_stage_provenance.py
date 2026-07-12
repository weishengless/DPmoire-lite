import hashlib

from ase import Atoms
from ase.build import make_supercell
from ase.io.vasp import write_vasp
import numpy as np
import pytest

from dpmoire_lite.provenance import (
    infer_inplane_supercell_relation,
    structure_identity,
    structure_identity_differences,
)


def make_primitive(symbols="H"):
    return Atoms(
        symbols,
        positions=[[0.0, 0.0, 0.0]] * len(symbols),
        cell=[[3.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 12.0]],
        pbc=True,
    )


def test_structure_identity_records_hash_count_composition_and_cell(tmp_path):
    path = tmp_path / "POSCAR"
    atoms = Atoms(
        "H2O",
        positions=[[0.0, 0.0, 0.0], [0.5, 0.5, 0.5], [1.0, 1.0, 1.0]],
        cell=[[3.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 12.0]],
        pbc=True,
    )
    write_vasp(path, atoms, direct=True)

    identity = structure_identity(path)

    assert identity.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert identity.atom_count == 3
    assert identity.ordered_elements == ("H", "H", "O")
    assert identity.composition == (("H", 2), ("O", 1))
    np.testing.assert_allclose(identity.cell, atoms.cell.array, atol=1e-8, rtol=1e-8)


def test_structure_identity_reports_specific_changed_component(tmp_path):
    original_path = tmp_path / "original.POSCAR"
    changed_path = tmp_path / "changed.POSCAR"
    original = make_primitive("H2")
    changed = original.copy()
    changed.set_cell([[3.5, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 12.0]], scale_atoms=False)
    write_vasp(original_path, original, direct=True)
    write_vasp(changed_path, changed, direct=True)

    differences = structure_identity_differences(
        structure_identity(original_path),
        structure_identity(changed_path),
    )

    assert "sha256" in differences
    assert "cell" in differences
    assert "atom_count" not in differences
    assert "composition" not in differences


def test_inplane_supercell_relation_detects_diagonal_sc():
    primitive = make_primitive()
    supercell = make_supercell(
        primitive,
        [[2, 0, 0], [0, 3, 0], [0, 0, 1]],
    )

    relation = infer_inplane_supercell_relation(primitive, supercell)

    assert relation.matrix == ((2, 0), (0, 3))
    assert relation.determinant == 6
    assert relation.atom_multiplier == 6


def test_inplane_supercell_relation_distinguishes_2x1_from_1x2():
    primitive = make_primitive()
    x_relation = infer_inplane_supercell_relation(
        primitive,
        make_supercell(primitive, [[2, 0, 0], [0, 1, 0], [0, 0, 1]]),
    )
    y_relation = infer_inplane_supercell_relation(
        primitive,
        make_supercell(primitive, [[1, 0, 0], [0, 2, 0], [0, 0, 1]]),
    )

    assert x_relation.matrix == ((2, 0), (0, 1))
    assert y_relation.matrix == ((1, 0), (0, 2))
    assert x_relation.matrix != y_relation.matrix


def test_inplane_supercell_relation_rejects_noninteger_transform():
    primitive = make_primitive()
    noninteger = primitive.copy()
    noninteger.set_cell(
        [[4.5, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 12.0]],
        scale_atoms=False,
    )

    with pytest.raises(ValueError, match="integer"):
        infer_inplane_supercell_relation(primitive, noninteger)


def test_inplane_supercell_relation_checks_determinant_against_atom_multiplier():
    primitive = make_primitive()
    wrong_count = primitive.copy()
    wrong_count.set_cell(
        [[6.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 12.0]],
        scale_atoms=False,
    )

    with pytest.raises(ValueError, match="determinant|atom"):
        infer_inplane_supercell_relation(primitive, wrong_count)
