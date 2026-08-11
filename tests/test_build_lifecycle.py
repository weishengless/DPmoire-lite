import hashlib
import shutil
import warnings
from pathlib import Path

import pytest
from ase import Atoms

import dpmoire_lite.build as build_module
import dpmoire_lite.build_preflight as preflight_module
import dpmoire_lite.inputs as inputs_module
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

    def fail_once(
        config,
        output_dir,
        atoms,
        incar_template,
        rcut,
        workflow_cutoff,
        submit_script,
    ):
        nonlocal failed
        if output_dir.name == "1_0" and not failed:
            failed = True
            raise RuntimeError("synthetic second-target failure")
        return original(
            config,
            output_dir,
            atoms,
            incar_template,
            rcut,
            workflow_cutoff,
            submit_script,
        )

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


def _assert_prepared_source_identity(source, path: Path) -> None:
    path = Path(path)
    payload = path.read_bytes()
    assert source.path == path
    assert source.size == len(payload)
    assert source.sha256 == hashlib.sha256(payload).hexdigest()


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


def test_stage0_treats_dangling_init_target_as_preflight_conflict(
    monkeypatch,
    tmp_path,
):
    config = write_build_config(
        tmp_path,
        stage=0,
        init_mlff_mode="single-job",
        init_bottom_incar="init_bottom_INCAR",
        init_top_incar="init_top_INCAR",
        do_relaxation=False,
        twist_val=False,
        n_sectors=[1, 1],
        symm_reduce=True,
    )
    input_dir = tmp_path / "input"
    for name in ("init_bottom_INCAR", "init_top_INCAR"):
        (input_dir / name).write_text(
            "ENCUT=400\nML_RCUT1=6\nML_RCUT2=6\n",
            encoding="utf-8",
        )
    work = tmp_path / "work"
    init_target = work / "init_mlff"
    real_exists = Path.exists
    real_is_symlink = Path.is_symlink

    def dangling_target_exists(path):
        if path == init_target:
            return False
        return real_exists(path)

    def dangling_target_is_symlink(path):
        if path == init_target:
            return True
        return real_is_symlink(path)

    monkeypatch.setattr(Path, "exists", dangling_target_exists)
    monkeypatch.setattr(Path, "is_symlink", dangling_target_is_symlink)

    with pytest.raises(RuntimeError, match="Build target conflict"):
        run_build(config, wait=False)

    assert not (work / "sym_reduced_stackings.txt").exists()
    assert list(work.glob(".init_mlff-candidate-*")) == []


@pytest.mark.parametrize(
    "script_name",
    ["POTCAR", "potcar", "vdw_kernel.bindat", "VDW_KERNEL.BINDAT"],
)
def test_single_job_init_preflight_rejects_static_filename_collisions(
    tmp_path,
    script_name,
):
    config = write_build_config(
        tmp_path,
        stage=0,
        dft_script=script_name,
        init_mlff_mode="single-job",
        init_bottom_incar="init_bottom_INCAR",
        init_top_incar="init_top_INCAR",
        do_relaxation=False,
        twist_val=False,
        n_sectors=[1, 1],
    )
    input_dir = tmp_path / "input"
    for name in ("init_bottom_INCAR", "init_top_INCAR"):
        (input_dir / name).write_text(
            "ENCUT=400\nML_RCUT1=6\nML_RCUT2=6\nLUSE_VDW=.TRUE.\n",
            encoding="utf-8",
        )
    (input_dir / "vdw_kernel.bindat").write_bytes(b"synthetic support data\n")
    (tmp_path / "scripts" / script_name).write_text(
        "#!/usr/bin/env bash\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="filename collision"):
        run_build(config, wait=False)

    assert not (tmp_path / "work").exists()


@pytest.mark.parametrize(
    "script_name",
    [
        "nested/submit.sh",
        "C:submit.sh",
        "name:stream",
        "POTCAR.",
        "submit.sh ",
        "CON",
        "input?.sh",
    ],
)
def test_single_job_init_preflight_requires_safe_submit_basename(
    tmp_path,
    script_name,
):
    config = write_build_config(
        tmp_path,
        stage=0,
        dft_script=script_name,
        init_mlff_mode="single-job",
        init_bottom_incar="init_bottom_INCAR",
        init_top_incar="init_top_INCAR",
        do_relaxation=False,
        twist_val=False,
        n_sectors=[1, 1],
    )
    input_dir = tmp_path / "input"
    for name in ("init_bottom_INCAR", "init_top_INCAR"):
        (input_dir / name).write_text(
            "ENCUT=400\nML_RCUT1=6\nML_RCUT2=6\n",
            encoding="utf-8",
        )
    if script_name == "nested/submit.sh":
        nested_script = tmp_path / "scripts" / script_name
        nested_script.parent.mkdir()
        nested_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="safe basename"):
        run_build(config, wait=False)

    assert not (tmp_path / "work").exists()


def test_single_job_init_preflight_reports_both_missing_phase_templates(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        init_mlff_mode="single-job",
        init_bottom_incar="missing_bottom_INCAR",
        init_top_incar="missing_top_INCAR",
        do_relaxation=False,
        twist_val=False,
        n_sectors=[1, 1],
    )

    with pytest.raises(RuntimeError) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value)
    assert "missing required template init_bottom_INCAR" in message
    assert "missing required template init_top_INCAR" in message
    assert not (tmp_path / "work" / "init_mlff").exists()


def test_single_job_init_preflight_returns_frozen_phase_plan(tmp_path):
    config_path = write_build_config(
        tmp_path,
        stage=0,
        init_mlff_mode="single-job",
        init_bottom_incar="init_bottom_INCAR",
        init_top_incar="init_top_INCAR",
        do_relaxation=False,
        twist_val=False,
        n_sectors=[1, 1],
        sc=[2, 1],
        input_kwargs={"top_a": 3.0, "bot_a": 4.0},
    )
    input_dir = tmp_path / "input"
    for name in ("init_bottom_INCAR", "init_top_INCAR"):
        (input_dir / name).write_text(
            "ENCUT=400\nML_RCUT1=6\nML_RCUT2=6\n",
            encoding="utf-8",
        )

    result = preflight_module.preflight_stage0(load_config(config_path))

    workflow = result.init_mlff_workflow
    assert workflow is not None
    assert workflow.mode == "single-job"
    assert workflow.initial_state == "step-1-ready"
    assert workflow.root_dir == tmp_path / "work" / "init_mlff"
    assert [
        (
            phase.name,
            phase.role,
            phase.initial_state,
            phase.target_dir,
            phase.template.name,
        )
        for phase in workflow.phases
    ] == [
        (
            "bottom",
            "step-1",
            "step-1-ready",
            tmp_path / "work" / "init_mlff" / "bottom",
            "init_bottom_INCAR",
        ),
        (
            "top",
            "step-2",
            "planned",
            tmp_path / "work" / "init_mlff" / "top",
            "init_top_INCAR",
        ),
    ]
    assert workflow.phases[0].atoms.cell.lengths()[:2] == pytest.approx([8.0, 4.0])
    assert workflow.phases[1].atoms.cell.lengths()[:2] == pytest.approx([6.0, 3.0])


def test_single_job_init_generation_failure_leaves_no_formal_workspace(
    monkeypatch,
    tmp_path,
):
    config = write_build_config(
        tmp_path,
        stage=0,
        init_mlff_mode="single-job",
        init_bottom_incar="init_bottom_INCAR",
        init_top_incar="init_top_INCAR",
        do_relaxation=False,
        twist_val=False,
        n_sectors=[1, 1],
    )
    input_dir = tmp_path / "input"
    bottom_template = input_dir / "init_bottom_INCAR"
    top_template = input_dir / "init_top_INCAR"
    bottom_template.write_text(
        "ENCUT=400\nML_RCUT1=6\nML_RCUT2=6\n",
        encoding="utf-8",
    )
    top_template.write_text(
        "ENCUT=400\nML_RCUT1=6\nML_RCUT2=6\n",
        encoding="utf-8",
    )
    real_preflight = build_module.preflight_stage0

    def mutate_top_template_after_preflight(config_value):
        result = real_preflight(config_value)
        top_template.write_text("changed after preflight\n", encoding="utf-8")
        return result

    monkeypatch.setattr(
        build_module,
        "preflight_stage0",
        mutate_top_template_after_preflight,
    )

    with pytest.raises(RuntimeError, match="changed since preflight|identity"):
        run_build(config, wait=False)

    work = tmp_path / "work"
    assert not (work / "init_mlff").exists()
    assert list(work.glob(".init_mlff-candidate-*")) == []


def test_single_job_init_late_target_conflict_is_not_replaced(monkeypatch, tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        init_mlff_mode="single-job",
        init_bottom_incar="init_bottom_INCAR",
        init_top_incar="init_top_INCAR",
        do_relaxation=False,
        twist_val=False,
        n_sectors=[1, 1],
    )
    input_dir = tmp_path / "input"
    for name in ("init_bottom_INCAR", "init_top_INCAR"):
        (input_dir / name).write_text(
            "ENCUT=400\nML_RCUT1=6\nML_RCUT2=6\n",
            encoding="utf-8",
        )
    real_publish = build_module.atomic_directory_publish_no_replace

    def create_conflict_before_publish(candidate, destination):
        destination.mkdir()
        return real_publish(candidate, destination)

    monkeypatch.setattr(
        build_module,
        "atomic_directory_publish_no_replace",
        create_conflict_before_publish,
    )

    with pytest.raises(RuntimeError, match="appeared after preflight"):
        run_build(config, wait=False)

    work = tmp_path / "work"
    conflict = work / "init_mlff"
    assert conflict.is_dir()
    assert list(conflict.iterdir()) == []
    assert list(work.glob(".init_mlff-candidate-*")) == []


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


def test_stage0_preflight_rejects_nonpositive_enmax_before_writing(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        do_relaxation=False,
        twist_val=False,
    )
    (tmp_path / "potcars" / "H" / "POTCAR").write_text(
        "synthetic invalid potential\n ENMAX = 0;\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="ENMAX must be finite and positive"):
        run_build(config, wait=False)

    assert not (tmp_path / "work").exists()


def test_stage0_preflight_rejects_multiple_enmax_values_before_writing(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        do_relaxation=False,
        twist_val=False,
    )
    (tmp_path / "potcars" / "H" / "POTCAR").write_text(
        "synthetic ambiguous potential\n ENMAX = 100;\n ENMAX = 200;\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="exactly one ENMAX"):
        run_build(config, wait=False)

    assert not (tmp_path / "work").exists()


def test_stage0_preflight_rejects_ambiguous_minimal_potcar_before_writing(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        do_relaxation=False,
        twist_val=False,
        potcar_policy="minimal",
    )
    potcars = tmp_path / "potcars"
    (potcars / "H" / "POTCAR").write_text(
        "synthetic plain potential\n ENMAX = 100; ZVAL = 1;\n",
        encoding="utf-8",
    )
    (potcars / "H_sv").mkdir()
    (potcars / "H_sv" / "POTCAR").write_text(
        "synthetic alternate potential\n ENMAX = 120; ZVAL = 1;\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Ambiguous minimal POTCAR candidates"):
        run_build(config, wait=False)

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
    monkeypatch.setattr(
        build_module,
        "_resolve_rcut",
        forbid_reinterpretation,
        raising=False,
    )
    monkeypatch.setattr(
        build_module,
        "generate_stackings",
        forbid_reinterpretation,
        raising=False,
    )

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

    def observe_inputs(
        config_value,
        output_dir,
        atoms,
        incar_template,
        rcut,
        workflow_cutoff,
        submit_script,
    ):
        observed_rcuts.append(rcut)
        return real_inputs(
            config_value,
            output_dir,
            atoms,
            incar_template,
            rcut,
            workflow_cutoff,
            submit_script,
        )

    monkeypatch.setattr(build_module, "preflight_stage1", observe_preflight)
    monkeypatch.setattr(build_module, "StructureHandler", forbid_reinterpretation)
    monkeypatch.setattr(
        build_module,
        "_resolve_rcut",
        forbid_reinterpretation,
        raising=False,
    )
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


def test_preflight_returns_parsed_incar_documents_and_required_vdw_sources(
    tmp_path,
):
    config_path = write_build_config(
        tmp_path,
        stage=0,
        init_mlff=False,
        do_relaxation=True,
        twist_val=False,
        n_sectors=[1, 1],
        vasp_ml=False,
    )
    template = tmp_path / "input" / "rlx_INCAR"
    template.write_text(
        "ENCUT=400\nML_RCUT1=6\nML_RCUT2=6\nLUSE_VDW=T\n",
        encoding="utf-8",
    )
    kernel = tmp_path / "input" / "vdw_kernel.bindat"
    kernel.write_bytes(b"synthetic vdw kernel\n")

    result = preflight_module.preflight_stage0(load_config(config_path))

    assert isinstance(result.templates, tuple)
    prepared = next(item for item in result.templates if item.name == "rlx_INCAR")
    assert prepared.analysis.document.source_name == str(template)
    assert prepared.analysis.is_effectively_true("LUSE_VDW") is True
    assert prepared.vdw_source is not None
    _assert_prepared_source_identity(prepared.vdw_source, kernel)


def test_generation_does_not_reparse_preflight_incar_templates(
    monkeypatch, tmp_path
):
    config = write_build_config(
        tmp_path,
        stage=0,
        init_mlff=True,
        do_relaxation=False,
        twist_val=False,
        n_sectors=[1, 1],
    )
    state = {"preflight_complete": False}
    real_preflight = build_module.preflight_stage0
    real_parse = inputs_module.parse_incar

    def observe_preflight(config_value):
        result = real_preflight(config_value)
        state["preflight_complete"] = True
        return result

    def forbid_generation_parse(*args, **kwargs):
        if state["preflight_complete"]:
            raise AssertionError("generation reparsed a prepared INCAR template")
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(build_module, "preflight_stage0", observe_preflight)
    monkeypatch.setattr(inputs_module, "parse_incar", forbid_generation_parse)

    run_build(config, wait=False)

    assert (tmp_path / "work" / "init_mlff" / "INCAR").is_file()


def test_generation_does_not_reresolve_potcars_or_reread_enmax(
    monkeypatch, tmp_path
):
    config = write_build_config(
        tmp_path,
        stage=0,
        init_mlff=True,
        do_relaxation=False,
        twist_val=False,
        n_sectors=[1, 1],
    )
    state = {"preflight_complete": False}
    real_preflight = build_module.preflight_stage0
    real_resolve = inputs_module.resolve_potcar_dir
    real_read_enmax = inputs_module.read_enmax

    def observe_preflight(config_value):
        result = real_preflight(config_value)
        state["preflight_complete"] = True
        return result

    def forbid_generation_resolve(*args, **kwargs):
        if state["preflight_complete"]:
            raise AssertionError("generation re-resolved a prepared POTCAR source")
        return real_resolve(*args, **kwargs)

    def forbid_generation_enmax(*args, **kwargs):
        if state["preflight_complete"]:
            raise AssertionError("generation reread prepared POTCAR ENMAX")
        return real_read_enmax(*args, **kwargs)

    monkeypatch.setattr(build_module, "preflight_stage0", observe_preflight)
    monkeypatch.setattr(inputs_module, "resolve_potcar_dir", forbid_generation_resolve)
    monkeypatch.setattr(inputs_module, "read_enmax", forbid_generation_enmax)

    run_build(config, wait=False)

    assert (tmp_path / "work" / "init_mlff" / "POTCAR").is_file()


def test_preflight_records_potcar_script_and_vdw_source_identities(tmp_path):
    config_path = write_build_config(
        tmp_path,
        stage=0,
        init_mlff=True,
        do_relaxation=False,
        twist_val=False,
        n_sectors=[1, 1],
    )
    template = tmp_path / "input" / "init_INCAR"
    template.write_text(
        "ENCUT=400\nML_RCUT1=6\nML_RCUT2=6\nLUSE_VDW=T\n",
        encoding="utf-8",
    )
    kernel = tmp_path / "input" / "vdw_kernel.bindat"
    kernel.write_bytes(b"synthetic vdw kernel\n")
    config = load_config(config_path)

    result = preflight_module.preflight_stage0(config)

    assert isinstance(result.potcars, tuple)
    potcar = next(item for item in result.potcars if item.element == "H")
    _assert_prepared_source_identity(
        potcar.source,
        tmp_path / "potcars" / "H" / "POTCAR",
    )
    assert potcar.enmax == 100.0
    _assert_prepared_source_identity(
        result.submit_script,
        tmp_path / "scripts" / "DFT_script.sh",
    )
    prepared_template = next(
        item for item in result.templates if item.name == "init_INCAR"
    )
    _assert_prepared_source_identity(prepared_template.source, template)
    _assert_prepared_source_identity(prepared_template.vdw_source, kernel)


def test_generation_copies_only_the_prepared_source_paths(monkeypatch, tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        init_mlff=True,
        do_relaxation=False,
        twist_val=False,
        n_sectors=[1, 1],
    )
    template = tmp_path / "input" / "init_INCAR"
    template.write_text(
        "ENCUT=400\nML_RCUT1=6\nML_RCUT2=6\nLUSE_VDW=T\n",
        encoding="utf-8",
    )
    kernel = tmp_path / "input" / "vdw_kernel.bindat"
    kernel.write_bytes(b"prepared synthetic vdw kernel\n")
    potcar_source = tmp_path / "potcars" / "H" / "POTCAR"
    script_source = tmp_path / "scripts" / "DFT_script.sh"
    expected_potcar = potcar_source.read_bytes()
    expected_script = script_source.read_bytes()
    expected_kernel = kernel.read_bytes()

    def write_decoy_potcar(_elements, _potcar_dir, output_file, **_kwargs):
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(b"decoy POTCAR\n ENMAX = 999;\n")
        return 999.0

    def copy_decoy_script(_script_dir, dft_script, output_dir):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / dft_script).write_bytes(b"decoy script\n")

    def copy_decoy_vdw(_template_file, _input_dir, output_dir):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "vdw_kernel.bindat").write_bytes(b"decoy kernel\n")

    monkeypatch.setattr(build_module, "write_potcar", write_decoy_potcar, raising=False)
    monkeypatch.setattr(build_module, "copy_submit_script", copy_decoy_script, raising=False)
    monkeypatch.setattr(build_module, "copy_vdw_if_needed", copy_decoy_vdw, raising=False)

    run_build(config, wait=False)

    output = tmp_path / "work" / "init_mlff"
    assert (output / "POTCAR").read_bytes() == expected_potcar
    assert (output / "DFT_script.sh").read_bytes() == expected_script
    assert (output / "vdw_kernel.bindat").read_bytes() == expected_kernel


def test_changed_prepared_source_fails_identity_check_before_copy(
    monkeypatch, tmp_path
):
    config = write_build_config(
        tmp_path,
        stage=0,
        init_mlff=True,
        do_relaxation=False,
        twist_val=False,
        n_sectors=[1, 1],
    )
    script_source = tmp_path / "scripts" / "DFT_script.sh"
    real_preflight = build_module.preflight_stage0

    def mutate_after_preflight(config_value):
        result = real_preflight(config_value)
        script_source.write_bytes(b"changed after preflight\n")
        return result

    monkeypatch.setattr(build_module, "preflight_stage0", mutate_after_preflight)

    with pytest.raises(RuntimeError, match="changed since preflight|identity"):
        run_build(config, wait=False)

    output = tmp_path / "work" / "init_mlff"
    assert not (output / "DFT_script.sh").exists()
    assert not (output / "manifest.yaml").exists()


def test_preflight_returns_the_exact_validated_output_paths(monkeypatch, tmp_path):
    def prepare_twist(self, _n_min, _n_max, *_extra_args):
        return ["synthetic-angle"], [self.new_struct.copy()]

    monkeypatch.setattr(
        preflight_module.StructureHandler,
        "make_twist_struct",
        prepare_twist,
    )
    stage0_root = tmp_path / "stage0"
    stage0_config = load_config(
        write_build_config(
            stage0_root,
            stage=0,
            init_mlff=True,
            do_relaxation=True,
            twist_val=True,
            n_sectors=[2, 1],
        )
    )
    stage0_result = preflight_module.preflight_stage0(stage0_config)
    stage0_work = stage0_root / "work"

    assert stage0_result.stage_targets == (
        ("init_mlff", stage0_work / "init_mlff"),
        ("rlx", stage0_work / "rlx"),
        ("validation", stage0_work / "validation"),
    )
    assert stage0_result.output_dirs == (
        stage0_work / "init_mlff",
        stage0_work / "rlx" / "0_0",
        stage0_work / "rlx" / "1_0",
        stage0_work / "validation" / "synthetic-angle",
    )

    stage1_root = tmp_path / "stage1"
    stage1_config = load_config(
        write_build_config(
            stage1_root,
            stage=1,
            n_sectors=[1, 1],
            vasp_ml=False,
            include_monolayer_md=True,
        )
    )
    stage1_work = stage1_root / "work"
    write_converged_relaxation(stage1_work)
    write_relaxation_manifest(stage1_work, [(0, 0)])
    stage1_result = preflight_module.preflight_stage1(stage1_config)

    assert stage1_result.stage_targets == (("md", stage1_work / "md"),)
    assert stage1_result.output_dirs == (
        stage1_work / "md" / "0_0",
        stage1_work / "md" / "top_layer",
        stage1_work / "md" / "bot_layer",
    )
    for result in (stage0_result, stage1_result):
        for path in result.output_dirs:
            path.resolve().relative_to(result.structures.work_dir.resolve())


def test_target_conflict_still_precedes_all_domain_preparation(
    monkeypatch, tmp_path
):
    config_path = write_build_config(
        tmp_path,
        stage=0,
        init_mlff=False,
        do_relaxation=True,
        twist_val=False,
        n_sectors=[1, 1],
        vasp_ml=False,
    )
    work = tmp_path / "work"
    marker = _mark_stage(work, "rlx", "conflict sentinel")

    def fail_domain_preparation(*_args, **_kwargs):
        raise AssertionError("domain preparation ran before the target conflict gate")

    monkeypatch.setattr(preflight_module, "_validate_templates", fail_domain_preparation)
    monkeypatch.setattr(preflight_module, "_validate_submit_script", fail_domain_preparation)
    monkeypatch.setattr(preflight_module, "_read_structures", fail_domain_preparation)

    with pytest.raises(RuntimeError, match="Build target conflict"):
        preflight_module.preflight_stage0(load_config(config_path))

    assert marker.read_text(encoding="utf-8") == "conflict sentinel"


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
