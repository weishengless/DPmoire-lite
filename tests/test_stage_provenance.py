import hashlib
from pathlib import Path
import warnings
from types import SimpleNamespace

from ase import Atoms
from ase.build import make_supercell
from ase.io.vasp import read_vasp, write_vasp
import numpy as np
import pytest
import yaml

import dpmoire_lite.build_preflight as build_preflight_module
import dpmoire_lite.provenance as provenance_module
from dpmoire_lite.build import run_build
from dpmoire_lite.config import load_config
from dpmoire_lite.manifest import read_manifest
from dpmoire_lite.provenance import (
    infer_inplane_supercell_relation,
    structure_identity,
    structure_identity_differences,
)
from dpmoire_lite.structures import StructureHandler

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


def complete_stage0_relaxations(work):
    for poscar in sorted((work / "rlx").glob("*/POSCAR")):
        target = poscar.parent
        (target / "CONTCAR").write_bytes(poscar.read_bytes())
        (target / "OUTCAR").write_text(
            "reached required accuracy - stopping structural energy minimisation\n",
            encoding="utf-8",
        )


def switch_to_stage1(config_path, **overrides):
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data.update(
        {
            "stage": 1,
            "init_mlff": False,
            "do_relaxation": True,
            "twist_val": False,
            "vasp_ml": False,
            "include_monolayer_md": False,
        }
    )
    data.update(overrides)
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return config_path


def prepare_stage1_case(tmp_path, *, stage0_overrides=None, stage1_overrides=None):
    config = make_stage0_config(tmp_path, **(stage0_overrides or {}))
    run_build(config, wait=False)
    complete_stage0_relaxations(tmp_path / "work")
    return switch_to_stage1(config, **(stage1_overrides or {}))


def write_legacy_relaxation_manifest(work, stackings):
    path = work / "rlx" / "manifest.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "stage": "rlx",
                "generated_at": "legacy-test",
                "directories": [f"rlx/{i}_{j}" for i, j in stackings],
                "stackings": [list(stacking) for stacking in stackings],
            }
        ),
        encoding="utf-8",
    )


def prepare_legacy_case(tmp_path, *, stage0_overrides=None, stage1_overrides=None):
    config = make_stage0_config(tmp_path, **(stage0_overrides or {}))
    run_build(config, wait=False)
    work = tmp_path / "work"
    complete_stage0_relaxations(work)
    stackings = read_rlx_manifest(work).stackings
    write_legacy_relaxation_manifest(work, stackings)
    return switch_to_stage1(config, **(stage1_overrides or {}))


def _stage1_provenance_inputs(config_path):
    config = load_config(config_path)
    structures = StructureHandler(
        config.input_dir,
        config.work_dir,
        config.n_sectors,
        config.d,
        config.d_mode,
        config.d_reference,
    )
    manifest_result = read_manifest(config.work_dir, "rlx")
    if manifest_result.manifest is not None:
        raw_stackings = manifest_result.manifest.stackings
    else:
        assert manifest_result.raw_data is not None
        raw_stackings = manifest_result.raw_data["stackings"]
    stackings = tuple((int(i), int(j)) for i, j in raw_stackings)
    relaxation_atoms = {
        stacking: read_vasp(
            config.work_dir
            / "rlx"
            / f"{stacking[0]}_{stacking[1]}"
            / "CONTCAR"
        )
        for stacking in stackings
    }
    return config, manifest_result, stackings, structures, relaxation_atoms


def rewrite_poscar_cell(path, cell):
    atoms = read_vasp(path)
    atoms.set_cell(cell, scale_atoms=False)
    write_vasp(path, atoms=atoms, direct=True)


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


def test_stage1_uses_manifest_sc_rlx_instead_of_current_config(tmp_path):
    config = prepare_stage1_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc": [2, 1], "sc_rlx": True},
        stage1_overrides={"sc_rlx": False},
    )

    with pytest.raises(RuntimeError, match="sc_rlx"):
        run_build(config, wait=False)

    assert not (tmp_path / "work" / "md").exists()


def test_stage1_rejects_sc_rlx_change_before_md_mutation(tmp_path):
    config = prepare_stage1_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc_rlx": False},
        stage1_overrides={"sc_rlx": True},
    )

    with pytest.raises(RuntimeError, match="sc_rlx"):
        run_build(config, wait=False)

    assert not (tmp_path / "work" / "md").exists()


def test_stage1_rejects_sc_change_when_stage0_was_supercell(tmp_path):
    config = prepare_stage1_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc": [2, 1], "sc_rlx": True},
        stage1_overrides={"sc": [1, 1]},
    )

    with pytest.raises(RuntimeError, match="sc"):
        run_build(config, wait=False)

    assert not (tmp_path / "work" / "md").exists()


def test_stage1_allows_md_sc_change_when_stage0_was_primitive(tmp_path):
    config = prepare_stage1_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc": [1, 1], "sc_rlx": False},
        stage1_overrides={"sc": [2, 3], "sc_rlx": False},
    )

    run_build(config, wait=False)

    atoms = read_vasp(tmp_path / "work" / "md" / "0_0" / "POSCAR")
    assert len(atoms) == 12


@pytest.mark.parametrize("name", ["top_layer.poscar", "bot_layer.poscar"])
def test_stage1_rejects_changed_top_or_bottom_input(tmp_path, name):
    config = prepare_stage1_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1]},
    )
    path = tmp_path / "input" / name
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match=name):
        run_build(config, wait=False)

    assert not (tmp_path / "work" / "md").exists()


def test_stage1_rejects_changed_rlx_poscar(tmp_path):
    config = prepare_stage1_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1]},
    )
    path = tmp_path / "work" / "rlx" / "0_0" / "POSCAR"
    path.write_bytes(path.read_bytes() + b"\n")

    with pytest.raises(RuntimeError, match="rlx/0_0/POSCAR"):
        run_build(config, wait=False)

    assert not (tmp_path / "work" / "md").exists()


def test_stage1_rejects_contcar_atom_or_composition_mismatch(tmp_path):
    config = prepare_stage1_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1]},
    )
    write_vasp(
        tmp_path / "work" / "rlx" / "0_0" / "CONTCAR",
        atoms=Atoms("H", positions=[[0, 0, 0]], cell=[4, 4, 12], pbc=True),
        direct=True,
    )

    with pytest.raises(RuntimeError, match="CONTCAR"):
        run_build(config, wait=False)

    assert not (tmp_path / "work" / "md").exists()


def test_stage1_aggregates_provenance_failures_across_stackings(tmp_path):
    config = prepare_stage1_case(
        tmp_path,
        stage0_overrides={"n_sectors": [2, 1]},
    )
    input_path = tmp_path / "input" / "top_layer.poscar"
    input_path.write_text(input_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    poscar = tmp_path / "work" / "rlx" / "1_0" / "POSCAR"
    poscar.write_bytes(poscar.read_bytes() + b"\n")

    with pytest.raises(RuntimeError) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value)
    assert "input/top_layer.poscar" in message
    assert "rlx/1_0/POSCAR" in message
    assert not (tmp_path / "work" / "md").exists()


def test_legacy_manifest_infers_primitive_from_count_composition_and_cell(tmp_path):
    config = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc": [1, 1], "sc_rlx": False},
    )

    run_build(config, wait=False)

    atoms = read_vasp(tmp_path / "work" / "md" / "0_0" / "POSCAR")
    assert len(atoms) == 2


def test_successful_legacy_inference_warns_exactly_once_per_build(tmp_path):
    config = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [2, 1], "sc": [1, 1], "sc_rlx": False},
    )

    with pytest.warns(UserWarning) as caught:
        run_build(config, wait=False)

    matching = [
        warning
        for warning in caught
        if "Legacy relaxation provenance was inferred" in str(warning.message)
    ]
    assert len(matching) == 1


def test_legacy_manifest_infers_supercell_and_sc(tmp_path):
    config = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc": [2, 1], "sc_rlx": True},
    )

    run_build(config, wait=False)

    atoms = read_vasp(tmp_path / "work" / "md" / "0_0" / "POSCAR")
    assert len(atoms) == 4
    assert atoms.cell.lengths()[0] == pytest.approx(8.0)
    assert atoms.cell.lengths()[1] == pytest.approx(4.0)


@pytest.mark.parametrize("sc", [[2, 1], [1, 2]])
def test_legacy_manifest_distinguishes_same_determinant_directions(tmp_path, sc):
    config = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc": sc, "sc_rlx": True},
    )

    run_build(config, wait=False)

    atoms = read_vasp(tmp_path / "work" / "md" / "0_0" / "POSCAR")
    assert len(atoms) == 4
    assert atoms.cell.lengths()[:2] == pytest.approx([4.0 * sc[0], 4.0 * sc[1]])


def test_legacy_manifest_rejects_count_cell_conflict(tmp_path):
    config = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc": [2, 1], "sc_rlx": True},
    )
    poscar = tmp_path / "work" / "rlx" / "0_0" / "POSCAR"
    atoms = read_vasp(poscar)
    cell = atoms.cell.array.copy()
    cell[0, 0] *= 0.75
    rewrite_poscar_cell(poscar, cell)

    with pytest.raises(RuntimeError) as exc_info:
        run_build(config, wait=False)

    assert "cell" in str(exc_info.value).lower()
    assert not (tmp_path / "work" / "md").exists()


def test_failed_legacy_inference_emits_no_success_warning(tmp_path):
    config = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc": [2, 1], "sc_rlx": True},
    )
    poscar = tmp_path / "work" / "rlx" / "0_0" / "POSCAR"
    atoms = read_vasp(poscar)
    cell = atoms.cell.array.copy()
    cell[0, 0] *= 0.75
    rewrite_poscar_cell(poscar, cell)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(RuntimeError, match="cell"):
            run_build(config, wait=False)

    assert not any(
        "Legacy relaxation provenance was inferred" in str(warning.message)
        for warning in caught
    )


def test_legacy_manifest_rejects_inconsistent_stackings(tmp_path):
    config = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [2, 1], "sc": [1, 1], "sc_rlx": False},
    )
    poscar = tmp_path / "work" / "rlx" / "1_0" / "POSCAR"
    expanded = make_supercell(read_vasp(poscar), [[2, 0, 0], [0, 1, 0], [0, 0, 1]])
    write_vasp(poscar, atoms=expanded, direct=True)
    (poscar.parent / "CONTCAR").write_bytes(poscar.read_bytes())

    with pytest.raises(RuntimeError) as exc_info:
        run_build(config, wait=False)

    assert "consistent" in str(exc_info.value).lower()
    assert not (tmp_path / "work" / "md").exists()


@pytest.mark.parametrize("missing", ["POSCAR", "CONTCAR"])
def test_legacy_manifest_rejects_missing_poscar_or_contcar(tmp_path, missing):
    config = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1]},
    )
    (tmp_path / "work" / "rlx" / "0_0" / missing).unlink()

    with pytest.raises(RuntimeError) as exc_info:
        run_build(config, wait=False)

    assert missing in str(exc_info.value)
    assert not (tmp_path / "work" / "md").exists()


def test_legacy_manifest_rejects_changed_current_inputs(tmp_path):
    config = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc_rlx": False},
    )
    input_path = tmp_path / "input" / "top_layer.poscar"
    atoms = read_vasp(input_path)
    cell = atoms.cell.array.copy()
    cell[0, 0] = 5.0
    rewrite_poscar_cell(input_path, cell)

    with pytest.raises(RuntimeError) as exc_info:
        run_build(config, wait=False)

    assert "cell" in str(exc_info.value).lower()
    assert not (tmp_path / "work" / "md").exists()


def test_legacy_inference_conflict_with_current_config_fails_before_md(tmp_path):
    config = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc": [2, 1], "sc_rlx": True},
        stage1_overrides={"sc_rlx": False},
    )

    with pytest.raises(RuntimeError) as exc_info:
        run_build(config, wait=False)

    assert "sc_rlx" in str(exc_info.value)
    assert not (tmp_path / "work" / "md").exists()


def test_md_manifest_records_strict_provenance_evidence(tmp_path):
    config = prepare_stage1_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc": [2, 1], "sc_rlx": True},
    )

    run_build(config, wait=False)

    manifest = read_manifest(tmp_path / "work", "md").manifest
    assert manifest is not None
    provenance = manifest.structure_provenance
    assert provenance["mode"] == "strict"
    assert provenance["sc_rlx"] is True
    assert provenance["sc"] == [2, 1]
    assert provenance["evidence"] == {
        "manifest": "rlx/manifest.yaml",
        "schema": "dpmoire-lite.structure-provenance.v1",
        "stackings": [[0, 0]],
    }


def test_strict_stage1_emits_no_legacy_inference_warning(tmp_path):
    config = prepare_stage1_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc": [1, 1], "sc_rlx": False},
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run_build(config, wait=False)

    assert not any(
        "Legacy relaxation provenance was inferred" in str(warning.message)
        for warning in caught
    )


def test_md_manifest_records_legacy_inference_evidence(tmp_path):
    config = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc": [2, 1], "sc_rlx": True},
    )

    run_build(config, wait=False)

    manifest = read_manifest(tmp_path / "work", "md").manifest
    assert manifest is not None
    provenance = manifest.structure_provenance
    assert provenance["mode"] == "legacy_inference"
    assert provenance["sc_rlx"] is True
    assert provenance["sc"] == [2, 1]
    assert provenance["evidence"] == {
        "manifest": "rlx/manifest.yaml",
        "method": "atom_count_composition_cell",
        "stackings": [[0, 0]],
    }


def test_legacy_inference_does_not_rewrite_relaxation_manifest(tmp_path):
    config = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc": [1, 1], "sc_rlx": False},
    )
    relaxation_manifest = tmp_path / "work" / "rlx" / "manifest.yaml"
    before = relaxation_manifest.read_bytes()

    run_build(config, wait=False)

    assert relaxation_manifest.read_bytes() == before
    manifest = read_manifest(tmp_path / "work", "md").manifest
    assert manifest is not None
    assert manifest.structure_provenance["mode"] == "legacy_inference"


def test_md_manifest_uses_relaxation_manifest_stackings_only(tmp_path):
    config = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [2, 1], "sc": [1, 1], "sc_rlx": False},
        stage1_overrides={"n_sectors": [1, 1]},
    )

    run_build(config, wait=False)

    manifest = read_manifest(tmp_path / "work", "md").manifest
    assert manifest is not None
    assert manifest.stackings == [[0, 0], [1, 0]]
    assert manifest.directories == ["md/0_0", "md/1_0"]
    assert manifest.structure_provenance["mode"] == "legacy_inference"
    assert manifest.structure_provenance["evidence"]["stackings"] == [[0, 0], [1, 0]]


def test_provenance_module_builds_stage0_structure_and_anchor_records(tmp_path):
    identity_builder = getattr(provenance_module, "structure_identity_record", None)
    anchor_builder = getattr(
        provenance_module, "stage0_grid_shift_anchor_record", None
    )
    provenance_builder = getattr(
        provenance_module, "stage0_structure_provenance", None
    )
    assert callable(identity_builder)
    assert callable(anchor_builder)
    assert callable(provenance_builder)

    config_path = make_stage0_config(tmp_path, n_sectors=[1, 1])
    config = load_config(config_path)
    structures = StructureHandler(
        config.input_dir,
        config.work_dir,
        config.n_sectors,
        config.d,
        config.d_mode,
        config.d_reference,
    )
    run_build(config_path, wait=False)

    manifest = read_rlx_manifest(tmp_path / "work")
    relative_path = "rlx/0_0/POSCAR"
    poscar = tmp_path / "work" / relative_path
    identity = identity_builder(poscar, relative_path)
    assert identity == manifest.structure_provenance["rlx_poscars"][relative_path]

    manifest_anchor = manifest.grid_shift_anchors["rlx/0_0"]
    anchor = anchor_builder(
        poscar,
        identity,
        [manifest_anchor["top_index"]],
        [manifest_anchor["bottom_index"]],
    )
    assert anchor == manifest_anchor

    provenance = provenance_builder(
        config,
        structures,
        ((0, 0),),
        {relative_path: identity},
    )
    assert provenance == manifest.structure_provenance


def test_provenance_module_returns_strict_stage1_result_and_issues(tmp_path):
    result_type = getattr(provenance_module, "Stage1ProvenanceResult", None)
    validator = getattr(provenance_module, "validate_stage1_provenance", None)
    assert result_type is not None
    assert callable(validator)

    config_path = prepare_stage1_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc_rlx": True, "sc": [1, 1]},
        stage1_overrides={"preserve_grid_shift_md": True},
    )
    config, manifest_result, stackings, structures, relaxation_atoms = (
        _stage1_provenance_inputs(config_path)
    )
    result = validator(
        config,
        manifest_result,
        stackings,
        structures,
        relaxation_atoms,
    )

    assert isinstance(result, result_type)
    assert result.kind == "strict"
    assert result.trusted_sc_rlx is True
    assert result.trusted_sc == (1, 1)
    assert result.evidence == (
        ("manifest", "rlx/manifest.yaml"),
        ("schema", "dpmoire-lite.structure-provenance.v1"),
        ("stackings", stackings),
    )
    assert result.issues == ()
    record = manifest_result.manifest.grid_shift_anchors["rlx/0_0"]
    assert result.validated_anchor_indices == (
        (record["top_index"], record["bottom_index"]),
    )


def test_provenance_module_returns_legacy_stage1_result_and_issues(tmp_path):
    result_type = getattr(provenance_module, "Stage1ProvenanceResult", None)
    issue_type = getattr(provenance_module, "ProvenanceIssue", None)
    validator = getattr(provenance_module, "validate_stage1_provenance", None)
    assert result_type is not None
    assert issue_type is not None
    assert callable(validator)

    config_path = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc_rlx": False, "sc": [1, 1]},
    )
    config, manifest_result, stackings, structures, relaxation_atoms = (
        _stage1_provenance_inputs(config_path)
    )
    result = validator(
        config,
        manifest_result,
        stackings,
        structures,
        relaxation_atoms,
    )

    assert isinstance(result, result_type)
    assert result.kind == "legacy_inference"
    assert result.trusted_sc_rlx is False
    assert result.trusted_sc == (1, 1)
    assert result.evidence == (
        ("manifest", "rlx/manifest.yaml"),
        ("method", "atom_count_composition_cell"),
        ("stackings", stackings),
    )
    assert result.validated_anchor_indices == ()
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert isinstance(issue, issue_type)
    assert issue.severity == "warning"
    assert issue.path == Path("rlx") / "manifest.yaml"
    assert issue.reason == (
        "Legacy relaxation provenance was inferred from atom count, composition, "
        "and in-plane cell; Stage1 is proceeding without strict Stage0 provenance."
    )


def test_build_preflight_delegates_stage1_provenance_validation(tmp_path, monkeypatch):
    config_path = prepare_stage1_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc_rlx": False, "sc": [1, 1]},
    )
    config = load_config(config_path)
    fake_result = SimpleNamespace(
        kind="strict",
        trusted_sc_rlx=False,
        trusted_sc=(1, 1),
        evidence=(),
        validated_anchor_indices=(),
        issues=(),
    )
    calls = []

    def fake_validate(*args, **kwargs):
        calls.append((args, kwargs))
        return fake_result

    monkeypatch.setattr(
        provenance_module,
        "validate_stage1_provenance",
        fake_validate,
        raising=False,
    )

    result = build_preflight_module.preflight_stage1(config)

    assert len(calls) == 1
    assert getattr(result, "provenance", None) is fake_result


def test_build_uses_provenance_module_for_stage0_manifest_evidence(
    tmp_path, monkeypatch
):
    builders = {
        "structure_identity_record": getattr(
            provenance_module, "structure_identity_record", None
        ),
        "stage0_grid_shift_anchor_record": getattr(
            provenance_module, "stage0_grid_shift_anchor_record", None
        ),
        "stage0_structure_provenance": getattr(
            provenance_module, "stage0_structure_provenance", None
        ),
    }
    assert all(callable(builder) for builder in builders.values())

    calls = {name: 0 for name in builders}
    for name, builder in builders.items():
        def spy(*args, _name=name, _builder=builder, **kwargs):
            calls[_name] += 1
            return _builder(*args, **kwargs)

        monkeypatch.setattr(provenance_module, name, spy, raising=False)

    config_path = make_stage0_config(tmp_path, n_sectors=[1, 1])
    run_build(config_path, wait=False)

    assert calls["structure_identity_record"] > 0
    assert calls["stage0_grid_shift_anchor_record"] == 1
    assert calls["stage0_structure_provenance"] == 1
    manifest = read_rlx_manifest(tmp_path / "work")
    assert manifest.structure_provenance["stackings"] == [[0, 0]]
    assert manifest.grid_shift_anchors["rlx/0_0"]
