import hashlib

from ase.io.vasp import read_vasp
import pytest

from dpmoire_lite.build import run_build
from dpmoire_lite.manifest import read_manifest

from test_build import read_selective_dynamics_flags, write_build_config


def build_stage0(tmp_path, **overrides):
    config = write_build_config(tmp_path, **overrides)
    run_build(config, wait=False)
    work = tmp_path / "work"
    manifest = read_manifest(work, "rlx").manifest
    assert manifest is not None
    return work, manifest.structure_provenance


def test_stage0_manifest_records_two_anchor_indices_per_stacking(tmp_path):
    work, provenance = build_stage0(tmp_path, n_sectors=[2, 1])

    anchors = provenance["grid_shift_anchors"]
    assert set(anchors) == {"rlx/0_0", "rlx/1_0"}
    for relative_path in anchors:
        record = anchors[relative_path]
        assert record["top_index"] != record["bottom_index"]
        assert set(record["fixed_masks"]) == {
            record["top_index"],
            record["bottom_index"],
        }
        assert record["atom_count"] == len(read_vasp(work / relative_path / "POSCAR"))


def test_anchor_fixed_masks_use_true_equals_fixed(tmp_path):
    _, provenance = build_stage0(tmp_path, n_sectors=[1, 1])

    for record in provenance["grid_shift_anchors"].values():
        for index in (record["top_index"], record["bottom_index"]):
            mask = record["fixed_masks"][index]
            assert all(type(value) is bool for value in mask)
            assert mask == [True, True, False]


def test_ff_t_serializes_as_true_true_false(tmp_path):
    work, provenance = build_stage0(tmp_path, n_sectors=[1, 1])

    for relative_path, record in provenance["grid_shift_anchors"].items():
        flags = read_selective_dynamics_flags(work / relative_path / "POSCAR")
        assert flags[record["top_index"]] == ("F", "F", "T")
        assert flags[record["bottom_index"]] == ("F", "F", "T")


def test_anchor_record_is_bound_to_poscar_hash_and_atom_count(tmp_path):
    work, provenance = build_stage0(tmp_path, n_sectors=[1, 1])

    for relative_path, record in provenance["grid_shift_anchors"].items():
        poscar = work / relative_path / "POSCAR"
        identity = provenance["rlx_poscars"][f"{relative_path}/POSCAR"]
        assert record["poscar_sha256"] == identity["sha256"]
        assert record["poscar_sha256"] == hashlib.sha256(poscar.read_bytes()).hexdigest()
        assert record["atom_count"] == identity["atom_count"] == len(read_vasp(poscar))


def test_anchor_indices_follow_final_sorted_poscar_order(tmp_path):
    work_root = tmp_path
    config = write_build_config(work_root, n_sectors=[1, 1], sc_rlx=True, sc=[1, 1])
    input_dir = work_root / "input"
    input_dir.joinpath("top_layer.poscar").write_text(
        """He
1.0
  4.0 0.0 0.0
  0.0 4.0 0.0
  0.0 0.0 12.0
He
1
Direct
  0.0 0.0 0.25
""",
        encoding="utf-8",
    )
    (work_root / "potcars" / "He").mkdir()
    (work_root / "potcars" / "He" / "POTCAR").write_text(" ENMAX = 100; \n", encoding="utf-8")

    run_build(config, wait=False)

    work = work_root / "work"
    manifest = read_manifest(work, "rlx").manifest
    assert manifest is not None
    record = manifest.structure_provenance["grid_shift_anchors"]["rlx/0_0"]
    atoms = read_vasp(work / "rlx/0_0/POSCAR")
    symbols = atoms.get_chemical_symbols()
    assert symbols == ["H", "He"]
    assert record["bottom_index"] == symbols.index("H")
    assert record["top_index"] == symbols.index("He")
