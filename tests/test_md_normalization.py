import hashlib

import numpy as np
from ase.io.vasp import read_vasp
from ase.io.vasp import write_vasp
from ase.constraints import FixedLine
import pytest

from dpmoire_lite.build import run_build
from dpmoire_lite.manifest import read_manifest

from test_build import read_selective_dynamics_flags, write_build_config
from test_stage_provenance import complete_stage0_relaxations, prepare_stage1_case, switch_to_stage1


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


def _add_momenta(path, *, nonzero):
    atoms = read_vasp(path)
    if nonzero:
        momenta = np.arange(1, 3 * len(atoms) + 1, dtype=float).reshape(len(atoms), 3)
    else:
        momenta = np.zeros((len(atoms), 3), dtype=float)
    atoms.set_momenta(momenta)
    write_vasp(path, atoms=atoms, direct=True, sort=False)


def _prepare_bilayer_stage1(tmp_path, *, sc_rlx, sc, nonzero_momenta):
    config = prepare_stage1_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc_rlx": sc_rlx, "sc": sc},
    )
    _add_momenta(tmp_path / "work" / "rlx" / "0_0" / "CONTCAR", nonzero=nonzero_momenta)
    run_build(config, wait=False)
    return tmp_path / "work" / "md" / "0_0" / "POSCAR"


def _prepare_constrained_monolayer_stage1(tmp_path):
    config = write_build_config(
        tmp_path,
        n_sectors=[1, 1],
        sc_rlx=False,
        sc=[1, 1],
        include_monolayer_md=True,
    )
    for layer_name, value in (("top_layer", 1.0), ("bot_layer", 2.0)):
        path = tmp_path / "input" / f"{layer_name}.poscar"
        atoms = read_vasp(path)
        atoms.set_constraint(FixedLine([0], direction=atoms.cell.array[2] / atoms.cell.lengths()[2]))
        atoms.set_momenta(np.full((len(atoms), 3), value, dtype=float))
        write_vasp(path, atoms=atoms, direct=True, sort=False)

    run_build(config, wait=False)
    complete_stage0_relaxations(tmp_path / "work")
    switch_to_stage1(config, include_monolayer_md=True, sc_rlx=False, sc=[1, 1])
    return config


def test_sc_rlx_true_default_clears_all_constraints_and_momenta(tmp_path):
    poscar = _prepare_bilayer_stage1(
        tmp_path,
        sc_rlx=True,
        sc=[1, 1],
        nonzero_momenta=False,
    )

    atoms = read_vasp(poscar)

    assert not atoms.constraints
    assert "momenta" not in atoms.arrays


def test_sc_rlx_false_default_clears_before_supercell_expansion(tmp_path):
    poscar = _prepare_bilayer_stage1(
        tmp_path,
        sc_rlx=False,
        sc=[2, 1],
        nonzero_momenta=True,
    )

    atoms = read_vasp(poscar)

    assert len(atoms) == 4
    assert not atoms.constraints
    assert "momenta" not in atoms.arrays


def test_monolayer_md_clears_constraints_and_momenta(tmp_path):
    config = _prepare_constrained_monolayer_stage1(tmp_path)

    with pytest.warns(UserWarning):
        run_build(config, wait=False)

    for layer_name in ("top_layer", "bot_layer"):
        atoms = read_vasp(tmp_path / "work" / "md" / layer_name / "POSCAR")
        assert not atoms.constraints
        assert "momenta" not in atoms.arrays


def test_generated_md_poscar_has_no_selective_dynamics_by_default(tmp_path):
    poscar = _prepare_bilayer_stage1(
        tmp_path,
        sc_rlx=True,
        sc=[1, 1],
        nonzero_momenta=True,
    )

    assert "selective dynamics" not in poscar.read_text(encoding="utf-8").lower()
    assert not read_vasp(poscar).constraints


def test_generated_md_poscar_has_no_velocity_block(tmp_path):
    poscar = _prepare_bilayer_stage1(
        tmp_path,
        sc_rlx=False,
        sc=[2, 1],
        nonzero_momenta=True,
    )

    assert "momenta" not in read_vasp(poscar).arrays


def test_reread_md_poscar_has_no_constraints_or_momenta(tmp_path):
    poscar = _prepare_bilayer_stage1(
        tmp_path,
        sc_rlx=True,
        sc=[1, 1],
        nonzero_momenta=True,
    )

    reread = read_vasp(poscar)

    assert not reread.constraints
    assert "momenta" not in reread.arrays


def test_monolayer_constraint_clear_emits_one_warning(tmp_path):
    config = _prepare_constrained_monolayer_stage1(tmp_path)

    with pytest.warns(UserWarning) as caught:
        run_build(config, wait=False)

    assert len(caught) == 1
    assert "constraint" in str(caught[0].message).lower()
