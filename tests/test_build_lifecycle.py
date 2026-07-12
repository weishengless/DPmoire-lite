import shutil
import warnings
from pathlib import Path

import pytest
from ase import Atoms

import dpmoire_lite.build as build_module
from dpmoire_lite.build import run_build
from dpmoire_lite.config import load_config
from dpmoire_lite.manifest import read_manifest

from test_build import write_build_config, write_converged_relaxation, write_relaxation_manifest


def _mark_stage(work, stage: str, text: str = "keep me"):
    stage_dir = work / stage
    stage_dir.mkdir(parents=True, exist_ok=True)
    marker = stage_dir / "marker.txt"
    marker.write_text(text, encoding="utf-8")
    return marker


def _assert_explicit_delete_message(error: Exception, *stages: str) -> None:
    message = str(error).lower()
    for stage in stages:
        assert stage in message
    assert "delete" in message or "remove" in message
    assert "rerun" in message or "run again" in message


def _stage0_relaxation_config(tmp_path):
    return write_build_config(
        tmp_path,
        stage=0,
        init_mlff=False,
        do_relaxation=True,
        twist_val=False,
        n_sectors=[2, 1],
        vasp_ml=False,
    )


def _fail_once_on_second_relaxation_target(monkeypatch):
    original = build_module._write_vasp_inputs
    failed = False

    def fail_once(config, output_dir, atoms, incar_template, rcut):
        nonlocal failed
        if output_dir.name == "1_0" and not failed:
            failed = True
            raise RuntimeError("synthetic second-target failure")
        return original(config, output_dir, atoms, incar_template, rcut)

    monkeypatch.setattr(build_module, "_write_vasp_inputs", fail_once)


def _snapshot_tree(root: Path):
    root = Path(root)
    if not root.exists():
        return None
    snapshot = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        snapshot.append(
            (relative, None if path.is_dir() else path.read_bytes())
        )
    return tuple(snapshot)


def _prepare_stage1_lifecycle_case(tmp_path, stackings=((0, 0),)):
    config = write_build_config(
        tmp_path,
        stage=1,
        n_sectors=[2, 1],
        vasp_ml=False,
        include_monolayer_md=False,
    )
    work = tmp_path / "work"
    for i, j in stackings:
        write_converged_relaxation(work, name=f"{i}_{j}")
    write_relaxation_manifest(work, stackings)
    return config, work


def test_stage0_existing_empty_init_mlff_blocks_every_target(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        n_sectors=[1, 1],
        twist_val=True,
        min_val_n=2,
        max_val_n=2,
    )
    work = tmp_path / "work"
    (work / "init_mlff").mkdir(parents=True)

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "init_mlff")
    assert (work / "init_mlff").is_dir()
    assert not (work / "rlx").exists()
    assert not (work / "validation").exists()
    assert not (work / "backups").exists()


def test_stage0_existing_rlx_blocks_init_and_validation_generation(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        n_sectors=[1, 1],
        twist_val=True,
        min_val_n=2,
        max_val_n=2,
    )
    work = tmp_path / "work"
    marker = _mark_stage(work, "rlx")

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "rlx")
    assert marker.read_text(encoding="utf-8") == "keep me"
    assert not (work / "init_mlff").exists()
    assert not (work / "validation").exists()
    assert not (work / "backups").exists()


def test_stage0_existing_validation_blocks_init_and_rlx_generation(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        n_sectors=[1, 1],
        twist_val=True,
        min_val_n=2,
        max_val_n=2,
    )
    work = tmp_path / "work"
    marker = _mark_stage(work, "validation")

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "validation")
    assert marker.read_text(encoding="utf-8") == "keep me"
    assert not (work / "init_mlff").exists()
    assert not (work / "rlx").exists()
    assert not (work / "backups").exists()


def test_stage0_reports_all_existing_target_stages(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        n_sectors=[1, 1],
        twist_val=True,
        min_val_n=2,
        max_val_n=2,
    )
    work = tmp_path / "work"
    markers = {stage: _mark_stage(work, stage, f"{stage} sentinel") for stage in ("init_mlff", "rlx", "validation")}

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "init_mlff", "rlx", "validation")
    for stage, marker in markers.items():
        assert marker.read_text(encoding="utf-8") == f"{stage} sentinel"
    assert not (work / "backups").exists()


def test_stage1_existing_empty_md_fails_without_backup(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=1,
        n_sectors=[1, 1],
        vasp_ml=False,
        include_monolayer_md=False,
    )
    work = tmp_path / "work"
    write_converged_relaxation(work)
    md_dir = work / "md"
    md_dir.mkdir(parents=True)

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "md")
    assert list(md_dir.iterdir()) == []
    assert not (work / "backups").exists()


def test_target_conflict_runs_before_input_preflight(tmp_path):
    config = write_build_config(tmp_path, stage=0, n_sectors=[1, 1], twist_val=False)
    work = tmp_path / "work"
    _mark_stage(work, "rlx")
    (tmp_path / "input" / "top_layer.poscar").unlink()

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value).lower()
    _assert_explicit_delete_message(exc_info.value, "rlx")
    assert "top_layer.poscar" not in message
    assert not (work / "init_mlff").exists()


def test_target_conflict_does_not_write_root_artifacts(monkeypatch, tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        n_sectors=[1, 1],
        symm_reduce=True,
        twist_val=False,
    )
    work = tmp_path / "work"
    _mark_stage(work, "rlx")
    artifact = work / "sym_reduced_stackings.txt"

    def write_root_artifact(self):
        artifact.write_text("must not be written", encoding="utf-8")
        return [(0, 0)]

    monkeypatch.setattr(build_module.StructureHandler, "find_sym_reduced_stackings", write_root_artifact)

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "rlx")
    assert not artifact.exists()


def test_target_conflict_does_not_construct_runner(monkeypatch, tmp_path):
    config = write_build_config(tmp_path, stage=0, n_sectors=[1, 1], submit=True, twist_val=False)
    work = tmp_path / "work"
    _mark_stage(work, "rlx")
    runner_constructed = False

    def fail_if_constructed(*_args, **_kwargs):
        nonlocal runner_constructed
        runner_constructed = True
        raise AssertionError("SlurmRunner was constructed before target conflict validation")

    monkeypatch.setattr(build_module, "SlurmRunner", fail_if_constructed)

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "rlx")
    assert runner_constructed is False


def test_stage0_preflight_aggregates_incar_potcar_and_script_errors(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        do_relaxation=False,
        twist_val=False,
    )
    input_dir = tmp_path / "input"
    (input_dir / "init_INCAR").write_text(
        'ENCUT=400\nSYSTEM = "unfinished\n',
        encoding="utf-8",
    )
    (tmp_path / "potcars" / "H" / "POTCAR").unlink()
    (tmp_path / "scripts" / "DFT_script.sh").unlink()

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value)
    assert "init_INCAR" in message
    assert "POTCAR" in message
    assert "DFT_script.sh" in message
    assert not (tmp_path / "work").exists()


def test_stage0_preflight_aggregates_input_structure_and_vdw_errors(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        do_relaxation=False,
        twist_val=False,
    )
    input_dir = tmp_path / "input"
    (input_dir / "init_INCAR").write_text(
        "ENCUT=400\nLUSE_VDW=T\nML_RCUT1=6\nML_RCUT2=6\n",
        encoding="utf-8",
    )
    (input_dir / "top_layer.poscar").write_text("not a POSCAR\n", encoding="utf-8")

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value)
    assert "top_layer.poscar" in message
    assert "vdw_kernel.bindat" in message
    assert not (tmp_path / "work").exists()


def test_stage1_preflight_aggregates_all_relaxation_failures(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=1,
        n_sectors=[2, 1],
        vasp_ml=False,
        include_monolayer_md=False,
    )
    work = tmp_path / "work"
    write_converged_relaxation(work, name="0_0")
    (work / "rlx" / "0_0" / "OUTCAR").unlink()
    second = work / "rlx" / "1_0"
    second.mkdir(parents=True)
    (second / "CONTCAR").write_text("not a POSCAR\n", encoding="utf-8")
    (second / "OUTCAR").write_text(
        "reached required accuracy - stopping structural energy minimisation\n",
        encoding="utf-8",
    )
    (tmp_path / "input" / "MD_INCAR").unlink()
    write_relaxation_manifest(work, [(0, 0), (1, 0)])

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value)
    assert "rlx/0_0" in message
    assert "Missing OUTCAR" in message
    assert "rlx/1_0" in message
    assert "CONTCAR" in message
    assert "MD_INCAR" in message
    assert not (work / "md").exists()


def test_stage1_preflight_fully_parses_initial_seed(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=1,
        n_sectors=[1, 1],
        vasp_ml=True,
        include_monolayer_md=False,
    )
    work = tmp_path / "work"
    write_converged_relaxation(work)
    write_relaxation_manifest(work, [(0, 0)])
    init_mlff = work / "init_mlff"
    init_mlff.mkdir(parents=True)
    (init_mlff / "ML_ABN").write_text(
        "1.0 Version\n"
        "**************************************************\n"
        "The number of configurations\n"
        "--------------------------------------------------\n"
        "1\n",
        encoding="utf-8",
    )
    (init_mlff / "ML_FFN").write_text("ffn", encoding="utf-8")

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value).lower()
    assert "ml_abn" in message
    assert "configuration marker" in message
    assert not (work / "md").exists()


def test_preflight_failure_does_not_create_workdir_when_absent(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        do_relaxation=False,
        twist_val=False,
    )
    (tmp_path / "potcars" / "H" / "POTCAR").unlink()
    (tmp_path / "scripts" / "DFT_script.sh").unlink()

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value)
    assert "POTCAR" in message
    assert "DFT_script.sh" in message
    assert not (tmp_path / "work").exists()


def test_preflight_failure_does_not_write_normalized_input_sibling(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        do_relaxation=False,
        twist_val=False,
    )
    input_dir = tmp_path / "input"
    top_layer = input_dir / "top_layer.poscar"
    top_layer.write_text(
        top_layer.read_text(encoding="utf-8").replace("\nH\n1\n", "\nI1\n1\n"),
        encoding="utf-8",
    )
    (tmp_path / "scripts" / "DFT_script.sh").unlink()

    with pytest.raises(Exception):
        run_build(config, wait=False)

    assert not any(path.name.endswith(".normalized") for path in input_dir.iterdir())
    assert not (tmp_path / "work").exists()


def test_preflight_warnings_are_deduplicated_per_template_and_tag(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        init_mlff=False,
        do_relaxation=True,
        twist_val=False,
        n_sectors=[2, 1],
    )
    (tmp_path / "input" / "rlx_INCAR").write_text(
        "ENCUT=400\nENCUT=500\nML_RCUT1=6\nML_RCUT2=6\n",
        encoding="utf-8",
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run_build(config, wait=False)

    matching = [
        item
        for item in caught
        if "ENCUT" in str(item.message) and "rlx_INCAR" in str(item.message)
    ]
    assert len(matching) == 1
    assert (tmp_path / "work" / "rlx" / "0_0").is_dir()
    assert (tmp_path / "work" / "rlx" / "1_0").is_dir()


def test_successful_stage0_preflight_leaves_input_and_absent_work_tree_unchanged(
    tmp_path,
):
    config_path = write_build_config(
        tmp_path,
        stage=0,
        init_mlff=False,
        do_relaxation=True,
        twist_val=False,
        symm_reduce=False,
        n_sectors=[1, 1],
        vasp_ml=False,
    )
    config = load_config(config_path)
    input_before = _snapshot_tree(config.input_dir)
    work_before = _snapshot_tree(config.work_dir)

    result = build_module.preflight_stage0(config)

    assert result.structures is not None
    assert result.rcut is not None
    assert result.stackings == ((0, 0),)
    assert _snapshot_tree(config.input_dir) == input_before
    assert _snapshot_tree(config.work_dir) == work_before is None


def test_successful_stage1_preflight_leaves_existing_work_tree_byte_identical(
    monkeypatch, tmp_path
):
    config, work = _prepare_stage1_lifecycle_case(tmp_path)
    snapshots = []
    real_preflight = build_module.preflight_stage1

    def observe_preflight(*args, **kwargs):
        before = _snapshot_tree(work)
        result = real_preflight(*args, **kwargs)
        snapshots.append((before, _snapshot_tree(work)))
        return result

    monkeypatch.setattr(build_module, "preflight_stage1", observe_preflight)

    run_build(config, wait=False)

    assert len(snapshots) == 1
    assert snapshots[0][0] == snapshots[0][1]


def test_stage0_generation_consumes_preflight_structure_rcut_and_stackings(
    monkeypatch, tmp_path
):
    config = write_build_config(
        tmp_path,
        stage=0,
        twist_val=False,
        symm_reduce=False,
        n_sectors=[2, 1],
    )
    prepared = {}
    real_preflight = build_module.preflight_stage0

    def observe_preflight(config_value):
        result = real_preflight(config_value)
        prepared["result"] = result

        def forbid_read(*_args, **_kwargs):
            raise AssertionError("generation reparsed a prepared structure")

        result.structures.read_atoms = forbid_read
        return result

    def forbid_reinterpretation(*_args, **_kwargs):
        raise AssertionError("generation reconstructed a preflight value")

    monkeypatch.setattr(build_module, "preflight_stage0", observe_preflight)
    monkeypatch.setattr(build_module, "StructureHandler", forbid_reinterpretation)
    monkeypatch.setattr(build_module, "_resolve_rcut", forbid_reinterpretation)
    monkeypatch.setattr(build_module, "generate_stackings", forbid_reinterpretation)

    run_build(config, wait=False)

    result = prepared["result"]
    manifest = read_manifest(tmp_path / "work", "rlx").manifest
    assert manifest is not None
    assert manifest.stackings == [list(stacking) for stacking in result.stackings]
    assert (tmp_path / "work" / "init_mlff" / "POSCAR").is_file()


def test_stage1_preflight_owns_relaxation_manifest_stackings(monkeypatch, tmp_path):
    config, work = _prepare_stage1_lifecycle_case(
        tmp_path,
        stackings=((0, 0), (1, 0)),
    )
    observed_extra_args = []
    observed_stackings = []
    real_preflight = build_module.preflight_stage1

    def observe_preflight(config_value, *extra_args):
        observed_extra_args.append(extra_args)
        result = real_preflight(config_value, *extra_args)
        observed_stackings.append(result.stackings)
        return result

    monkeypatch.setattr(build_module, "preflight_stage1", observe_preflight)

    run_build(config, wait=False)

    manifest = read_manifest(work, "md").manifest
    assert manifest is not None
    assert observed_extra_args == [()]
    assert observed_stackings == [((0, 0), (1, 0))]
    assert manifest.stackings == [[0, 0], [1, 0]]


def test_stage1_generation_consumes_preflight_structures_relaxations_and_rcut(
    monkeypatch, tmp_path
):
    config, work = _prepare_stage1_lifecycle_case(tmp_path)
    prepared = {}
    writer_sources = []
    observed_rcuts = []
    real_preflight = build_module.preflight_stage1
    real_writer = build_module._write_md_poscar
    real_inputs = build_module._write_vasp_inputs

    def observe_preflight(*args, **kwargs):
        result = real_preflight(*args, **kwargs)
        prepared["result"] = result
        return result

    def forbid_reinterpretation(*_args, **_kwargs):
        raise AssertionError("generation reconstructed a preflight value")

    def observe_writer(source_atoms, *args, **kwargs):
        assert isinstance(source_atoms, Atoms)
        writer_sources.append(source_atoms)
        return real_writer(source_atoms, *args, **kwargs)

    def observe_inputs(config_value, output_dir, atoms, incar_template, rcut):
        observed_rcuts.append(rcut)
        return real_inputs(
            config_value,
            output_dir,
            atoms,
            incar_template,
            rcut,
        )

    monkeypatch.setattr(build_module, "preflight_stage1", observe_preflight)
    monkeypatch.setattr(build_module, "StructureHandler", forbid_reinterpretation)
    monkeypatch.setattr(build_module, "_resolve_rcut", forbid_reinterpretation)
    monkeypatch.setattr(build_module, "_write_md_poscar", observe_writer)
    monkeypatch.setattr(build_module, "_write_vasp_inputs", observe_inputs)

    run_build(config, wait=False)

    result = prepared["result"]
    assert isinstance(result.relaxations, tuple)
    assert [record.stacking for record in result.relaxations] == [(0, 0)]
    assert len(writer_sources) == 1
    assert writer_sources[0] is not result.relaxations[0].atoms
    assert observed_rcuts == [result.rcut]
    assert read_manifest(work, "md").manifest is not None


def test_generation_does_not_reparse_written_or_validated_poscars(
    monkeypatch, tmp_path
):
    config, work = _prepare_stage1_lifecycle_case(tmp_path)
    state = {"preflight_complete": False, "result": None}
    real_preflight = build_module.preflight_stage1
    real_read_vasp = build_module.read_vasp
    handler_type = build_module.StructureHandler
    real_handler_read = handler_type.read_atoms

    def observe_preflight(*args, **kwargs):
        result = real_preflight(*args, **kwargs)
        state["result"] = result
        state["preflight_complete"] = True
        return result

    def reuse_preflight_handler(*_args, **_kwargs):
        return state["result"].structures

    def forbid_build_read_vasp(*args, **kwargs):
        if state["preflight_complete"]:
            raise AssertionError("generation reopened a validated CONTCAR")
        return real_read_vasp(*args, **kwargs)

    def forbid_handler_reparse(self, *args, **kwargs):
        if state["preflight_complete"]:
            raise AssertionError("generation reparsed a written or validated POSCAR")
        return real_handler_read(self, *args, **kwargs)

    monkeypatch.setattr(build_module, "preflight_stage1", observe_preflight)
    monkeypatch.setattr(build_module, "StructureHandler", reuse_preflight_handler)
    monkeypatch.setattr(build_module, "read_vasp", forbid_build_read_vasp)
    monkeypatch.setattr(handler_type, "read_atoms", forbid_handler_reparse)

    run_build(config, wait=False)

    assert (work / "md" / "0_0" / "POSCAR").is_file()


def test_stage1_missing_relaxation_manifest_fails_without_md_change(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=1,
        n_sectors=[1, 1],
        vasp_ml=False,
        include_monolayer_md=False,
    )
    work = tmp_path / "work"
    write_converged_relaxation(work)

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value).lower()
    assert "manifest" in message
    assert "rlx" in message
    assert not (work / "md").exists()


def test_stage1_does_not_use_sym_reduced_file_without_manifest(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=1,
        n_sectors=[2, 1],
        symm_reduce=True,
        vasp_ml=False,
        include_monolayer_md=False,
    )
    work = tmp_path / "work"
    write_converged_relaxation(work)
    (work / "sym_reduced_stackings.txt").write_text("0 0\n", encoding="utf-8")

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value).lower()
    assert "manifest" in message
    assert "rlx" in message
    assert not (work / "md").exists()


def test_stage1_does_not_regenerate_stackings_from_config(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=1,
        n_sectors=[2, 1],
        vasp_ml=False,
        include_monolayer_md=False,
    )
    work = tmp_path / "work"
    write_converged_relaxation(work)

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value).lower()
    assert "manifest" in message
    assert "rlx/1_0" not in message
    assert not (work / "md").exists()


def test_stage_manifest_published_only_after_all_targets_in_that_stage_succeed(monkeypatch, tmp_path):
    config = _stage0_relaxation_config(tmp_path)
    _fail_once_on_second_relaxation_target(monkeypatch)

    with pytest.raises(RuntimeError, match="synthetic second-target failure"):
        run_build(config, wait=False)

    work = tmp_path / "work"
    assert (work / "rlx" / "0_0" / "POSCAR").is_file()
    assert not (work / "rlx" / "manifest.yaml").exists()


def test_generation_failure_leaves_partial_stage_without_manifest(monkeypatch, tmp_path):
    config = _stage0_relaxation_config(tmp_path)
    _fail_once_on_second_relaxation_target(monkeypatch)

    with pytest.raises(RuntimeError, match="synthetic second-target failure"):
        run_build(config, wait=False)

    work = tmp_path / "work"
    assert (work / "rlx" / "0_0").is_dir()
    assert (work / "rlx" / "1_0").is_dir()
    assert not (work / "rlx" / "manifest.yaml").exists()


def test_rerun_rejects_partial_stage_until_user_deletes_it(monkeypatch, tmp_path):
    config = _stage0_relaxation_config(tmp_path)
    _fail_once_on_second_relaxation_target(monkeypatch)

    with pytest.raises(RuntimeError, match="synthetic second-target failure"):
        run_build(config, wait=False)

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "rlx")
    assert not (tmp_path / "work" / "rlx" / "manifest.yaml").exists()


def test_delete_partial_stage_then_rerun_succeeds(monkeypatch, tmp_path):
    config = _stage0_relaxation_config(tmp_path)
    _fail_once_on_second_relaxation_target(monkeypatch)

    with pytest.raises(RuntimeError, match="synthetic second-target failure"):
        run_build(config, wait=False)

    shutil.rmtree(tmp_path / "work" / "rlx")
    run_build(config, wait=False)

    work = tmp_path / "work"
    assert (work / "rlx" / "0_0" / "POSCAR").is_file()
    assert (work / "rlx" / "1_0" / "POSCAR").is_file()
    assert read_manifest(work, "rlx").manifest is not None
