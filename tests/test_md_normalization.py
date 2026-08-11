import hashlib
from pathlib import Path
import shutil

import numpy as np
from ase.io.vasp import read_vasp
from ase.io.vasp import write_vasp
from ase.constraints import FixAtoms
from ase.constraints import FixScaled
from ase.constraints import FixedLine
import pytest

import dpmoire_lite.build as build_module
from dpmoire_lite.build import run_build
from dpmoire_lite.manifest import read_manifest
from dpmoire_lite.manifest import write_manifest
from dpmoire_lite.mlab import parse_mlab
from dpmoire_lite.mlab import seed_prefix_identity

from test_build import read_selective_dynamics_flags, write_build_config
from test_stage_provenance import complete_stage0_relaxations, prepare_legacy_case, prepare_stage1_case, switch_to_stage1


def build_stage0(tmp_path, **overrides):
    config = write_build_config(tmp_path, **overrides)
    run_build(config, wait=False)
    work = tmp_path / "work"
    manifest = read_manifest(work, "rlx").manifest
    assert manifest is not None
    return work, manifest


def test_stage0_manifest_writes_grid_shift_anchors_at_top_level_only(tmp_path):
    config = write_build_config(tmp_path, n_sectors=[1, 1])

    run_build(config, wait=False)

    manifest = read_manifest(tmp_path / "work", "rlx").manifest
    assert manifest is not None
    assert manifest.grid_shift_anchors
    assert "grid_shift_anchors" not in manifest.structure_provenance


def test_stage0_manifest_records_two_anchor_indices_per_stacking(tmp_path):
    work, manifest = build_stage0(tmp_path, n_sectors=[2, 1])
    anchors = manifest.grid_shift_anchors

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
    _, manifest = build_stage0(tmp_path, n_sectors=[1, 1])
    anchors = manifest.grid_shift_anchors

    for record in anchors.values():
        for index in (record["top_index"], record["bottom_index"]):
            mask = record["fixed_masks"][index]
            assert all(type(value) is bool for value in mask)
            assert mask == [True, True, False]


def test_ff_t_serializes_as_true_true_false(tmp_path):
    work, manifest = build_stage0(tmp_path, n_sectors=[1, 1])
    anchors = manifest.grid_shift_anchors

    for relative_path, record in anchors.items():
        flags = read_selective_dynamics_flags(work / relative_path / "POSCAR")
        assert flags[record["top_index"]] == ("F", "F", "T")
        assert flags[record["bottom_index"]] == ("F", "F", "T")


def test_anchor_record_is_bound_to_poscar_hash_and_atom_count(tmp_path):
    work, manifest = build_stage0(tmp_path, n_sectors=[1, 1])
    anchors = manifest.grid_shift_anchors

    for relative_path, record in anchors.items():
        poscar = work / relative_path / "POSCAR"
        identity = manifest.structure_provenance["rlx_poscars"][
            f"{relative_path}/POSCAR"
        ]
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
    record = manifest.grid_shift_anchors["rlx/0_0"]
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


def _prepare_three_atom_preserve_stage1(tmp_path):
    config = write_build_config(
        tmp_path,
        n_sectors=[1, 1],
        sc_rlx=True,
        sc=[1, 1],
        include_monolayer_md=False,
    )
    (tmp_path / "input" / "top_layer.poscar").write_text(
        """H
1.0
  4.0 0.0 0.0
  0.0 4.0 0.0
  0.0 0.0 12.0
H
2
Direct
  0.0 0.0 0.25
  0.5 0.5 0.25
""",
        encoding="utf-8",
    )

    run_build(config, wait=False)
    complete_stage0_relaxations(tmp_path / "work")
    switch_to_stage1(config, preserve_grid_shift_md=True)
    return config


def _preserve_anchor_record(tmp_path):
    manifest = read_manifest(tmp_path / "work", "rlx").manifest
    assert manifest is not None
    return manifest, manifest.grid_shift_anchors["rlx/0_0"]


def _rewrite_rlx_anchor_location(tmp_path, *, top_level):
    manifest = read_manifest(tmp_path / "work", "rlx").manifest
    assert manifest is not None
    anchors = manifest.grid_shift_anchors or manifest.structure_provenance.get(
        "grid_shift_anchors"
    )
    assert anchors

    if top_level:
        manifest.grid_shift_anchors = anchors
        manifest.structure_provenance.pop("grid_shift_anchors", None)
    else:
        manifest.grid_shift_anchors = {}
        manifest.structure_provenance["grid_shift_anchors"] = anchors
    write_manifest(tmp_path / "work", manifest)


def _rewrite_constraints(path, constraints):
    atoms = read_vasp(path)
    atoms.set_constraint(constraints)
    write_vasp(path, atoms=atoms, direct=True, sort=False)


def _replace_constraint(path, target_index, replacement):
    atoms = read_vasp(path)
    constraints = []
    for constraint in atoms.constraints:
        indices = np.asarray(constraint.index, dtype=int).reshape(-1)
        if target_index in indices:
            constraints.append(replacement)
        else:
            constraints.append(constraint)
    _rewrite_constraints(path, constraints)


def test_stage1_preservation_reads_top_level_anchor_records(tmp_path):
    config = _prepare_three_atom_preserve_stage1(tmp_path)
    _rewrite_rlx_anchor_location(tmp_path, top_level=True)

    run_build(config, wait=False)

    atoms = read_vasp(tmp_path / "work" / "md" / "0_0" / "POSCAR")
    assert len(atoms.constraints) == 2


def test_nested_only_development_anchors_fail_with_regeneration_diagnostic(tmp_path):
    config = _prepare_three_atom_preserve_stage1(tmp_path)
    _rewrite_rlx_anchor_location(tmp_path, top_level=False)

    with pytest.raises(RuntimeError, match="regenerate Stage0"):
        run_build(config, wait=False)

    assert not (tmp_path / "work" / "md").exists()


def test_nested_only_development_anchors_do_not_block_default_clearing(tmp_path):
    config = _prepare_three_atom_preserve_stage1(tmp_path)
    switch_to_stage1(config, preserve_grid_shift_md=False)
    _rewrite_rlx_anchor_location(tmp_path, top_level=False)

    run_build(config, wait=False)

    atoms = read_vasp(tmp_path / "work" / "md" / "0_0" / "POSCAR")
    assert not atoms.constraints


def test_preserve_true_accepts_exact_two_manifest_anchors(tmp_path):
    config = _prepare_three_atom_preserve_stage1(tmp_path)
    _, record = _preserve_anchor_record(tmp_path)

    run_build(config, wait=False)

    atoms = read_vasp(tmp_path / "work" / "md" / "0_0" / "POSCAR")
    assert len(atoms.constraints) == 2
    assert {
        int(index)
        for constraint in atoms.constraints
        for index in np.asarray(constraint.index, dtype=int).reshape(-1)
    } == {record["top_index"], record["bottom_index"]}
    assert all(
        list(constraint.mask) == [True, True, False]
        for constraint in atoms.constraints
    )


def test_preserve_true_rejects_missing_anchor(tmp_path):
    config = _prepare_three_atom_preserve_stage1(tmp_path)
    contcar = tmp_path / "work" / "rlx" / "0_0" / "CONTCAR"
    atoms = read_vasp(contcar)
    _rewrite_constraints(contcar, list(atoms.constraints)[:1])

    with pytest.raises(RuntimeError, match="anchor|constraint|preserve"):
        run_build(config, wait=False)


def test_preserve_true_rejects_extra_constraint(tmp_path):
    config = _prepare_three_atom_preserve_stage1(tmp_path)
    contcar = tmp_path / "work" / "rlx" / "0_0" / "CONTCAR"
    _, record = _preserve_anchor_record(tmp_path)
    atoms = read_vasp(contcar)
    extra_index = next(
        index
        for index in range(len(atoms))
        if index not in {record["top_index"], record["bottom_index"]}
    )
    _rewrite_constraints(
        contcar,
        [*atoms.constraints, FixScaled([extra_index], [True, True, False])],
    )

    with pytest.raises(RuntimeError, match="anchor|constraint|preserve"):
        run_build(config, wait=False)


def test_preserve_true_rejects_changed_fixed_mask(tmp_path):
    config = _prepare_three_atom_preserve_stage1(tmp_path)
    _, record = _preserve_anchor_record(tmp_path)
    contcar = tmp_path / "work" / "rlx" / "0_0" / "CONTCAR"
    _replace_constraint(
        contcar,
        record["top_index"],
        FixScaled([record["top_index"]], [False, True, False]),
    )

    with pytest.raises(RuntimeError, match="anchor|constraint|preserve"):
        run_build(config, wait=False)


def test_preserve_true_rejects_other_constraint_type(tmp_path):
    config = _prepare_three_atom_preserve_stage1(tmp_path)
    _, record = _preserve_anchor_record(tmp_path)
    contcar = tmp_path / "work" / "rlx" / "0_0" / "CONTCAR"
    _replace_constraint(
        contcar,
        record["top_index"],
        FixAtoms([record["top_index"]]),
    )

    with pytest.raises(RuntimeError, match="anchor|constraint|preserve"):
        run_build(config, wait=False)


def test_preserve_true_rejects_atom_order_or_hash_mismatch(tmp_path):
    config = _prepare_three_atom_preserve_stage1(tmp_path)
    manifest, _ = _preserve_anchor_record(tmp_path)
    manifest.grid_shift_anchors["rlx/0_0"]["poscar_sha256"] = "0" * 64
    write_manifest(tmp_path / "work", manifest)

    with pytest.raises(RuntimeError, match="anchor|hash|constraint|preserve"):
        run_build(config, wait=False)


def test_preserve_true_rejects_legacy_manifest(tmp_path):
    config = prepare_legacy_case(
        tmp_path,
        stage0_overrides={"n_sectors": [1, 1], "sc_rlx": False, "sc": [1, 1]},
        stage1_overrides={
            "preserve_grid_shift_md": True,
            "sc_rlx": False,
            "sc": [1, 1],
        },
    )

    with pytest.raises(RuntimeError, match="legacy|anchor|preserve"):
        run_build(config, wait=False)


def test_preserve_validation_failure_leaves_md_absent(tmp_path):
    config = _prepare_three_atom_preserve_stage1(tmp_path)
    contcar = tmp_path / "work" / "rlx" / "0_0" / "CONTCAR"
    atoms = read_vasp(contcar)
    _rewrite_constraints(contcar, list(atoms.constraints)[:1])

    with pytest.raises(RuntimeError, match="anchor|constraint|preserve"):
        run_build(config, wait=False)

    assert not (tmp_path / "work" / "md").exists()


def _prepare_preserve_expansion_stage1(
    tmp_path,
    *,
    sc_rlx,
    md_sc,
    mixed_elements=False,
):
    stage0_sc = list(md_sc) if sc_rlx else [1, 1]
    config = write_build_config(
        tmp_path,
        n_sectors=[1, 1],
        sc_rlx=sc_rlx,
        sc=stage0_sc,
        include_monolayer_md=False,
    )
    if mixed_elements:
        (tmp_path / "input" / "top_layer.poscar").write_text(
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
        (tmp_path / "potcars" / "He").mkdir()
        (tmp_path / "potcars" / "He" / "POTCAR").write_text(
            " ENMAX = 100; \n",
            encoding="utf-8",
        )

    run_build(config, wait=False)
    complete_stage0_relaxations(tmp_path / "work")
    switch_to_stage1(
        config,
        preserve_grid_shift_md=True,
        sc_rlx=sc_rlx,
        sc=list(md_sc),
    )
    return config


def _md_anchor_records(tmp_path):
    manifest = read_manifest(tmp_path / "work", "md").manifest
    assert manifest is not None
    return manifest, manifest.grid_shift_anchors["md/0_0"]


def _md_anchor_indices(atoms):
    return {
        int(index)
        for constraint in atoms.constraints
        for index in np.asarray(constraint.index, dtype=int).reshape(-1)
    }


def _assert_zero_translation_positions(tmp_path, sc, records):
    source = read_vasp(tmp_path / "work" / "rlx" / "0_0" / "CONTCAR")
    md = read_vasp(tmp_path / "work" / "md" / "0_0" / "POSCAR")
    source_scaled = source.get_scaled_positions(wrap=True)
    md_scaled = md.get_scaled_positions(wrap=True)
    for record in records:
        source_position = source_scaled[record["source_index"]]
        expected = np.array(
            [source_position[0] / sc[0], source_position[1] / sc[1], source_position[2]]
        )
        np.testing.assert_allclose(
            md_scaled[record["index"]],
            expected,
            atol=1e-8,
            rtol=1e-8,
        )


class TestPreserveExpansion:
    def test_primitive_expansion_keeps_exactly_two_anchors(self, tmp_path):
        config = _prepare_preserve_expansion_stage1(
            tmp_path,
            sc_rlx=False,
            md_sc=(2, 3),
        )

        run_build(config, wait=False)

        atoms = read_vasp(tmp_path / "work" / "md" / "0_0" / "POSCAR")
        assert len(atoms) == 12
        assert len(_md_anchor_indices(atoms)) == 2

    @pytest.mark.parametrize("md_sc", [(2, 1), (1, 2), (2, 3)])
    def test_anchor_count_is_independent_of_supercell_area(self, tmp_path, md_sc):
        config = _prepare_preserve_expansion_stage1(
            tmp_path,
            sc_rlx=False,
            md_sc=md_sc,
        )

        run_build(config, wait=False)

        atoms = read_vasp(tmp_path / "work" / "md" / "0_0" / "POSCAR")
        assert len(_md_anchor_indices(atoms)) == 2

    def test_rectangular_expansion_selects_zero_translation_image(self, tmp_path):
        sc = (1, 2)
        config = _prepare_preserve_expansion_stage1(
            tmp_path,
            sc_rlx=False,
            md_sc=sc,
        )

        run_build(config, wait=False)

        _, records = _md_anchor_records(tmp_path)
        assert {tuple(record["image_translation"]) for record in records} == {
            (0, 0, 0)
        }
        _assert_zero_translation_positions(tmp_path, sc, records)

    def test_source_index_alone_is_not_used_as_unique_identity(self, tmp_path):
        sc = (2, 3)
        config = _prepare_preserve_expansion_stage1(
            tmp_path,
            sc_rlx=False,
            md_sc=sc,
            mixed_elements=True,
        )

        run_build(config, wait=False)

        _, records = _md_anchor_records(tmp_path)
        assert len({(record["source_index"], tuple(record["image_translation"])) for record in records}) == 2
        assert {tuple(record["image_translation"]) for record in records} == {
            (0, 0, 0)
        }
        _assert_zero_translation_positions(tmp_path, sc, records)

    def test_sort_recovers_anchor_by_source_and_translation(self, tmp_path):
        sc = (2, 1)
        config = _prepare_preserve_expansion_stage1(
            tmp_path,
            sc_rlx=False,
            md_sc=sc,
            mixed_elements=True,
        )

        run_build(config, wait=False)

        manifest, records = _md_anchor_records(tmp_path)
        atoms = read_vasp(tmp_path / "work" / "md" / "0_0" / "POSCAR")
        assert {record["index"] for record in records} == _md_anchor_indices(atoms)
        assert manifest.grid_shift_anchors["md/0_0"] == records
        _assert_zero_translation_positions(tmp_path, sc, records)

    def test_md_manifest_records_final_source_translation_identity(self, tmp_path):
        config = _prepare_preserve_expansion_stage1(
            tmp_path,
            sc_rlx=False,
            md_sc=(2, 3),
        )

        run_build(config, wait=False)

        _, records = _md_anchor_records(tmp_path)
        assert all(set(record) == {"index", "source_index", "image_translation"} for record in records)
        assert all(record["image_translation"] == [0, 0, 0] for record in records)
        assert {record["index"] for record in records} == _md_anchor_indices(
            read_vasp(tmp_path / "work" / "md" / "0_0" / "POSCAR")
        )

    def test_sc_rlx_true_and_false_paths_have_same_anchor_count(self, tmp_path):
        true_root = tmp_path / "sc-rlx-true"
        false_root = tmp_path / "sc-rlx-false"
        true_config = _prepare_preserve_expansion_stage1(
            true_root,
            sc_rlx=True,
            md_sc=(2, 3),
        )
        false_config = _prepare_preserve_expansion_stage1(
            false_root,
            sc_rlx=False,
            md_sc=(2, 3),
        )

        run_build(true_config, wait=False)
        run_build(false_config, wait=False)

        true_atoms = read_vasp(true_root / "work" / "md" / "0_0" / "POSCAR")
        false_atoms = read_vasp(false_root / "work" / "md" / "0_0" / "POSCAR")
        assert len(_md_anchor_indices(true_atoms)) == len(_md_anchor_indices(false_atoms)) == 2


def _prepare_seed_stage1(tmp_path, *, seed_name="complete_vasp_651.mlab", n_sectors=(1, 1)):
    config = prepare_stage1_case(
        tmp_path,
        stage0_overrides={"n_sectors": list(n_sectors)},
        stage1_overrides={"vasp_ml": True},
    )
    init_mlff = tmp_path / "work" / "init_mlff"
    init_mlff.mkdir(parents=True)
    shutil.copy2(
        Path(__file__).parent / "data" / "mlab" / seed_name,
        init_mlff / "ML_ABN",
    )
    (init_mlff / "ML_FFN").write_text("initial-force-field\n", encoding="utf-8")
    return config, init_mlff


class TestSeedStaging:
    def test_stage1_seed_preflight_requires_complete_initial_mlab(self, tmp_path):
        config, _ = _prepare_seed_stage1(
            tmp_path,
            seed_name="tail_position_crop.mlab",
        )

        with pytest.raises(RuntimeError, match="complete|partial|ML_ABN|seed"):
            run_build(config, wait=False)

        assert not (tmp_path / "work" / "md" / "manifest.yaml").exists()

    def test_stage1_seed_manifest_records_count_schema_and_prefix_digest(self, tmp_path):
        config, init_mlff = _prepare_seed_stage1(tmp_path)
        parsed = parse_mlab(init_mlff / "ML_ABN")
        identity = seed_prefix_identity(parsed)

        run_build(config, wait=False)

        manifest = read_manifest(tmp_path / "work", "md").manifest
        assert manifest is not None
        seed = manifest.mlff_seed
        assert seed["source"] == "init_mlff/ML_ABN"
        assert seed["configurations"] == parsed.complete_count
        assert seed["digest_schema"] == identity.schema == "mlab-seed-v1"
        assert seed["seed_prefix_sha256"] == identity.sha256

    def test_stage1_seed_manifest_records_raw_ml_ab_and_ml_ff_hashes(self, tmp_path):
        config, init_mlff = _prepare_seed_stage1(tmp_path)

        run_build(config, wait=False)

        manifest = read_manifest(tmp_path / "work", "md").manifest
        assert manifest is not None
        seed = manifest.mlff_seed
        assert seed["ml_ab_sha256"] == hashlib.sha256(
            (init_mlff / "ML_ABN").read_bytes()
        ).hexdigest()
        assert seed["ml_ff_sha256"] == hashlib.sha256(
            (init_mlff / "ML_FFN").read_bytes()
        ).hexdigest()

    def test_stage1_verifies_copied_size_and_hash(self, tmp_path, monkeypatch):
        config, _ = _prepare_seed_stage1(tmp_path)
        real_copy = build_module.copy_prepared_source

        def corrupt_copy(source, destination):
            result = real_copy(source, destination)
            if Path(destination).name == "ML_FF":
                Path(destination).write_bytes(b"corrupted-seed-copy")
            return result

        monkeypatch.setattr(build_module, "copy_prepared_source", corrupt_copy)

        with pytest.raises(RuntimeError, match="hash|size|ML_FF"):
            run_build(config, wait=False)

    def test_stage1_seed_copy_failure_prevents_complete_md_manifest(
        self, tmp_path, monkeypatch
    ):
        config, _ = _prepare_seed_stage1(tmp_path, n_sectors=(2, 1))
        real_copy = build_module.copy_prepared_source

        def skip_second_seed_copy(source, destination):
            if (
                Path(destination).name == "ML_AB"
                and Path(destination).parent.name == "1_0"
            ):
                return None
            return real_copy(source, destination)

        monkeypatch.setattr(build_module, "copy_prepared_source", skip_second_seed_copy)

        with pytest.raises(RuntimeError, match="hash|size|ML_AB"):
            run_build(config, wait=False)

        assert not (tmp_path / "work" / "md" / "manifest.yaml").exists()

    def test_each_md_directory_receives_identical_initial_seed_identity(self, tmp_path):
        config, init_mlff = _prepare_seed_stage1(tmp_path, n_sectors=(2, 1))

        run_build(config, wait=False)

        manifest = read_manifest(tmp_path / "work", "md").manifest
        assert manifest is not None
        seed = manifest.mlff_seed
        for relative_directory in ("md/0_0", "md/1_0"):
            directory = tmp_path / "work" / relative_directory
            assert (directory / "ML_AB").stat().st_size == (init_mlff / "ML_ABN").stat().st_size
            assert (directory / "ML_FF").stat().st_size == (init_mlff / "ML_FFN").stat().st_size
            assert hashlib.sha256((directory / "ML_AB").read_bytes()).hexdigest() == seed["ml_ab_sha256"]
            assert hashlib.sha256((directory / "ML_FF").read_bytes()).hexdigest() == seed["ml_ff_sha256"]

    def test_later_md_ml_ab_changes_do_not_modify_manifest_seed_identity(self, tmp_path):
        config, _ = _prepare_seed_stage1(tmp_path)

        run_build(config, wait=False)

        manifest_path = tmp_path / "work" / "md" / "manifest.yaml"
        before = read_manifest(tmp_path / "work", "md").manifest
        assert before is not None
        before_seed = dict(before.mlff_seed)
        assert before_seed
        with (tmp_path / "work" / "md" / "0_0" / "ML_AB").open("ab") as handle:
            handle.write(b"restart-change")

        after = read_manifest(tmp_path / "work", "md").manifest
        assert after is not None
        assert after.mlff_seed == before_seed
        assert manifest_path.exists()
