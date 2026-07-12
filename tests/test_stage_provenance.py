import hashlib

from ase import Atoms
from ase.build import make_supercell
from ase.io.vasp import read_vasp, write_vasp
import numpy as np
import pytest

from dpmoire_lite.build import run_build
from dpmoire_lite.manifest import read_manifest
from dpmoire_lite.provenance import (
    infer_inplane_supercell_relation,
    structure_identity,
    structure_identity_differences,
)

from test_build import write_build_config


def make_primitive(symbols="H"):
    return Atoms(
        symbols,
        positions=[[0.0, 0.0, 0.0]] * len(symbols),
        cell=[[3.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 12.0]],
        pbc=True,
    )


def make_stage0_config(tmp_path, **overrides):
    data = {
        "stage": 0,
        "init_mlff": False,
        "do_relaxation": True,
        "twist_val": False,
        "n_sectors": [2, 1],
        "vasp_ml": False,
    }
    data.update(overrides)
    return write_build_config(tmp_path, **data)


def read_rlx_manifest(work):
    result = read_manifest(work, "rlx")
    assert result.manifest is not None
    return result.manifest


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


def test_stage0_manifest_v2_records_structure_source_fields(tmp_path):
    config = make_stage0_config(
        tmp_path,
        n_sectors=[2, 1],
        sc=[2, 1],
        sc_rlx=True,
        d=4.5,
        d_mode="surface_gap",
        symm_reduce=False,
    )

    run_build(config, wait=False)

    manifest = read_rlx_manifest(tmp_path / "work")
    provenance = manifest.structure_provenance
    assert provenance["schema"] == "dpmoire-lite.structure-provenance.v1"
    assert provenance["stage"] == 0
    assert provenance["sc_rlx"] is True
    assert provenance["sc"] == [2, 1]
    assert provenance["n_sectors"] == [2, 1]
    assert provenance["symm_reduce"] is False
    assert provenance["d"] == pytest.approx(4.5)
    assert provenance["d_mode"] == "surface_gap"
    assert provenance["stackings"] == [[0, 0], [1, 0]]


def test_stage0_manifest_records_input_hashes(tmp_path):
    config = make_stage0_config(tmp_path, n_sectors=[1, 1])

    run_build(config, wait=False)

    provenance = read_rlx_manifest(tmp_path / "work").structure_provenance
    for name in ("top_layer.poscar", "bot_layer.poscar"):
        path = tmp_path / "input" / name
        record = provenance["inputs"][f"input/{name}"]
        assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_stage0_manifest_records_every_generated_rlx_poscar_identity(tmp_path):
    config = make_stage0_config(tmp_path, n_sectors=[2, 1])

    run_build(config, wait=False)

    work = tmp_path / "work"
    provenance = read_rlx_manifest(work).structure_provenance
    expected_paths = {"rlx/0_0/POSCAR", "rlx/1_0/POSCAR"}
    assert set(provenance["rlx_poscars"]) == expected_paths
    for relative_path in expected_paths:
        path = work / relative_path
        record = provenance["rlx_poscars"][relative_path]
        assert record["path"] == relative_path
        assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert record["atom_count"] == len(read_vasp(path))
        assert record["composition"]
        assert record["cell"]


def test_stage0_manifest_stackings_match_generated_directories(tmp_path):
    config = make_stage0_config(tmp_path, n_sectors=[2, 1])

    run_build(config, wait=False)

    manifest = read_rlx_manifest(tmp_path / "work")
    generated_stackings = [
        [int(part) for part in directory.removeprefix("rlx/").split("_")]
        for directory in manifest.directories
    ]
    assert manifest.stackings == generated_stackings
    assert manifest.structure_provenance["stackings"] == generated_stackings


def test_stage0_manifest_records_sc_only_with_clear_stage0_semantics(tmp_path):
    config = make_stage0_config(
        tmp_path,
        n_sectors=[1, 1],
        sc=[2, 3],
        sc_rlx=True,
    )

    run_build(config, wait=False)

    provenance = read_rlx_manifest(tmp_path / "work").structure_provenance
    assert provenance["sc"] == [2, 3]
    assert provenance["sc_rlx"] is True
    assert provenance["sc_semantics"] == "stage0_relaxation_supercell"
