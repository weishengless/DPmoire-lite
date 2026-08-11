import hashlib
import os
from pathlib import Path
import stat
import subprocess
import threading
from types import SimpleNamespace

import pytest
import yaml

import dpmoire_lite.build as build_module
import dpmoire_lite.init_mlff as init_mlff_module
from dpmoire_lite.build import run_build
from dpmoire_lite.cli import main
from dpmoire_lite.config import ConfigError
from dpmoire_lite.init_mlff import (
    InitMlffCalculation,
    InitMlffWorkflow,
    InitMlffWorkflowError,
    init_mlff_manifest_lock,
)
from dpmoire_lite.init_mlff_submit import (
    BashFunctionInitMlffAdapter,
    prepare_init_mlff_submit_adapter,
)
from dpmoire_lite.inputs import prepare_source
from dpmoire_lite.manifest import read_manifest
from dpmoire_lite.slurm import SlurmJob

from test_build import write_build_config
from test_init_mlff import (
    _prepare_workflow,
    _write_op_layer,
    _write_valid_step1,
    _write_valid_step2,
)


RUN_MARKER_LINE = "dpmoire_run_vasp # DPMOIRE-LITE:RUN"


def _write_phase_templates(input_dir):
    for name in ("init_bottom_INCAR", "init_top_INCAR"):
        (input_dir / name).write_text(
            "ENCUT=400\nML_RCUT1=6\nML_RCUT2=6\n",
            encoding="utf-8",
        )


def _single_job_config(
    tmp_path,
    *,
    source_script,
    work_dir=None,
    script_name="DFT_script.sh",
    submit=False,
    stage=0,
):
    configured_work_dir = work_dir or tmp_path / "work"
    config_path = write_build_config(
        tmp_path,
        stage=stage,
        dft_script=script_name,
        work_dir=str(configured_work_dir),
        init_mlff_mode="single-job",
        init_bottom_incar="init_bottom_INCAR",
        init_top_incar="init_top_INCAR",
        do_relaxation=False,
        twist_val=False,
        n_sectors=[1, 1],
        submit=submit,
    )
    _write_phase_templates(tmp_path / "input")
    source_path = tmp_path / "scripts" / script_name
    source_path.write_text(source_script, encoding="utf-8", newline="")
    return config_path, configured_work_dir, source_path


@pytest.mark.parametrize(
    ("stage", "wait"),
    [(0, True), ("all", False), ("all", True)],
)
def test_single_job_safety_gates_run_before_runner_or_work_write(
    monkeypatch,
    tmp_path,
    stage,
    wait,
):
    source_script = (
        "#!/bin/bash\n"
        "dpmoire_run_vasp() {\n"
        "    true\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n"
    )
    config_path, work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
        submit=True,
        stage=stage,
    )

    def fail_if_constructed(*_args, **_kwargs):
        raise AssertionError("SlurmRunner was constructed before the safety gate")

    monkeypatch.setattr(build_module, "SlurmRunner", fail_if_constructed)

    with pytest.raises(ConfigError, match="stage: all|submitted --wait"):
        run_build(config_path, wait=wait)

    assert not work_dir.exists()


@pytest.mark.parametrize(
    "wait_directive",
    [
        "#SBATCH --wait",
        "#SBATCH -W",
        "#SBATCH -vW",
        "#SBATCH -Wv",
        "#SBATCH -k -W",
        "#SBATCH --exclusive -W",
        "#SLURM -W",
        "#SLURM -vW",
        "#SBATCH hetjob\n#SBATCH -W",
        "#SLURM packjob\n#SLURM -vW",
        "#SBATCH hetjob -W",
    ],
)
def test_single_job_automatic_submit_rejects_waiting_sbatch_directive_preflight(
    monkeypatch,
    tmp_path,
    wait_directive,
):
    source_script = (
        "#!/bin/bash\n"
        f"{wait_directive}\n"
        "dpmoire_run_vasp() {\n"
        "    true\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n"
    )
    config_path, work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
        submit=True,
    )

    def fail_if_constructed(*_args, **_kwargs):
        raise AssertionError("SlurmRunner was constructed before wait-directive preflight")

    monkeypatch.setattr(build_module, "SlurmRunner", fail_if_constructed)

    with pytest.raises(RuntimeError, match="wait.*fire-and-forget"):
        run_build(config_path, wait=False)

    assert not work_dir.exists()


@pytest.mark.parametrize(
    "translated_directive",
    [
        "#PBS -W block=true",
        "#BSUB -K",
        "#PBS-W block=true",
        "#BSUB-K",
        "#PBS -N synthetic",
    ],
)
def test_single_job_automatic_submit_rejects_translated_scheduler_directives(
    monkeypatch,
    tmp_path,
    translated_directive,
):
    source_script = (
        "#!/bin/bash\n"
        f"{translated_directive}\n"
        "dpmoire_run_vasp() {\n"
        "    true\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n"
    )
    config_path, work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
        submit=True,
    )

    def fail_if_constructed(*_args, **_kwargs):
        raise AssertionError("SlurmRunner was constructed before translated preflight")

    monkeypatch.setattr(build_module, "SlurmRunner", fail_if_constructed)

    with pytest.raises(RuntimeError, match="translated scheduler.*automatic"):
        run_build(config_path, wait=False)

    assert not work_dir.exists()


@pytest.mark.parametrize("translated_prefix", ["#PBS -W block=true", "#BSUB -K"])
def test_single_job_automatic_submit_rejects_translated_prefix_after_executable(
    monkeypatch,
    tmp_path,
    translated_prefix,
):
    source_script = (
        "#!/bin/bash\n"
        "dpmoire_run_vasp() {\n"
        f"{translated_prefix}\n"
        "    true\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n"
    )
    config_path, work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
        submit=True,
    )

    def fail_if_constructed(*_args, **_kwargs):
        raise AssertionError("SlurmRunner was constructed before translated preflight")

    monkeypatch.setattr(build_module, "SlurmRunner", fail_if_constructed)

    with pytest.raises(RuntimeError, match="translated scheduler.*automatic"):
        run_build(config_path, wait=False)

    assert not work_dir.exists()


@pytest.mark.parametrize(
    "directives",
    [
        "#SBATCH -JWORK\n",
        "#SBATCH -J -W\n",
        "#SBATCH --job-name -W\n",
        "#SBATCH -J\n#SBATCH -W\n",
        "#SBATCH -kW\n",
        "#SBATCH --exclusive=-W\n",
        "#SLURM -J -W\n",
    ],
)
def test_wait_detection_does_not_treat_option_argument_as_wait(
    tmp_path,
    directives,
):
    source_path = tmp_path / "DFT_script.sh"
    source_path.write_text(
        "#!/bin/bash\n"
        f"{directives}"
        "dpmoire_run_vasp() {\n"
        "    true\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n",
        encoding="utf-8",
    )

    prepared = prepare_init_mlff_submit_adapter(prepare_source(source_path))

    assert prepared.sbatch_wait_requested is False


@pytest.mark.parametrize(
    "wait_directive",
    [
        "#SBATCH --wait",
        "#SBATCH -vW",
        "#SBATCH -k -W",
        "#SBATCH --exclusive -W",
        "#SLURM -W",
        "#SBATCH hetjob\n#SBATCH -W",
        "#PBS -W block=true",
        "#BSUB -K",
        "#PBS-W block=true",
        "#BSUB-K",
    ],
)
def test_single_job_generation_only_preserves_user_waiting_directive(
    tmp_path,
    wait_directive,
):
    source_script = (
        "#!/bin/bash\n"
        f"{wait_directive}\n"
        "dpmoire_run_vasp() {\n"
        "    true\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n"
    )
    config_path, work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
        submit=False,
    )

    run_build(config_path, wait=False)

    generated_script = work_dir / "init_mlff" / "DFT_script.sh"
    assert generated_script.is_file()
    generated_text = generated_script.read_text(encoding="utf-8")
    assert all(line in generated_text for line in wait_directive.splitlines())


@pytest.mark.parametrize("translated_prefix", ["#PBS -W block=true", "#BSUB -K"])
def test_single_job_generation_only_preserves_translated_prefix_after_executable(
    tmp_path,
    translated_prefix,
):
    source_script = (
        "#!/bin/bash\n"
        "dpmoire_run_vasp() {\n"
        f"{translated_prefix}\n"
        "    true\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n"
    )
    config_path, work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
        submit=False,
    )

    run_build(config_path, wait=False)

    generated_text = (work_dir / "init_mlff" / "DFT_script.sh").read_text(
        encoding="utf-8"
    )
    assert translated_prefix in generated_text


def test_init_mlff_lock_rejects_a_symlink_before_touching_its_target(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    external_target = tmp_path / "outside-lock-target"
    external_target.write_bytes(b"")
    lock_path = work_dir / ".dpmoire-lite-init-mlff.lock"
    try:
        lock_path.symlink_to(external_target)
    except OSError as exc:
        pytest.skip(f"file symlinks are unavailable on this platform: {exc}")

    with pytest.raises(InitMlffWorkflowError, match="lock invariant"):
        with init_mlff_manifest_lock(work_dir):
            pass

    assert external_target.read_bytes() == b""


def test_init_mlff_lock_rejects_a_hard_link_before_touching_its_target(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    external_target = tmp_path / "outside-hard-link-target"
    external_target.write_bytes(b"")
    lock_path = work_dir / ".dpmoire-lite-init-mlff.lock"
    os.link(external_target, lock_path)

    with pytest.raises(InitMlffWorkflowError, match="lock invariant"):
        with init_mlff_manifest_lock(work_dir):
            pass

    assert external_target.read_bytes() == b""


def test_init_mlff_lock_rejects_windows_reparse_metadata():
    reparse_stat = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_nlink=1,
        st_reparse_tag=0xA000000C,
    )

    assert init_mlff_module._lock_file_is_safe(reparse_stat) is False


def test_init_mlff_lock_rejects_path_identity_change_after_open(
    monkeypatch,
    tmp_path,
):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    lock_path = work_dir / ".dpmoire-lite-init-mlff.lock"
    lock_path.write_bytes(b"original")
    replacement = tmp_path / "replacement-lock"
    replacement.write_bytes(b"replacement")
    real_open = init_mlff_module.os.open
    real_lstat = Path.lstat
    opened = False

    def open_then_expose_replacement(path, flags, mode=0o777):
        nonlocal opened
        descriptor = real_open(path, flags, mode)
        opened = True
        return descriptor

    def lstat_with_replacement_identity(path):
        if path == lock_path and opened:
            return real_lstat(replacement)
        return real_lstat(path)

    monkeypatch.setattr(init_mlff_module.os, "open", open_then_expose_replacement)
    monkeypatch.setattr(Path, "lstat", lstat_with_replacement_identity)
    yielded = False

    with pytest.raises(InitMlffWorkflowError, match="lock invariant"):
        with init_mlff_manifest_lock(work_dir):
            yielded = True

    assert yielded is False
    assert lock_path.read_bytes() == b"original"
    assert replacement.read_bytes() == b"replacement"


@pytest.mark.parametrize("script_name", ["--help", "-W"])
def test_single_job_rejects_option_like_submit_script_name_before_runner(
    monkeypatch,
    tmp_path,
    script_name,
):
    source_script = (
        "#!/bin/bash\n"
        "dpmoire_run_vasp() {\n"
        "    true\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n"
    )
    config_path, work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
        script_name=script_name,
        submit=True,
    )

    def fail_if_constructed(*_args, **_kwargs):
        raise AssertionError("SlurmRunner was constructed for an option-like script name")

    monkeypatch.setattr(build_module, "SlurmRunner", fail_if_constructed)

    with pytest.raises(RuntimeError, match="safe basename"):
        run_build(config_path, wait=False)

    assert not work_dir.exists()


def test_single_job_fire_and_forget_submits_one_root_adapter_and_records_request(
    monkeypatch,
    tmp_path,
    capsys,
):
    source_script = (
        "#!/bin/bash\n"
        "#SBATCH --nodes=1\n"
        "dpmoire_run_vasp() {\n"
        "    synthetic_mpirun vasp_std\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n"
    )
    config_path, work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
        submit=True,
    )
    calls = []

    class FireAndForgetRunner:
        def __init__(self, script_name, _n_nodes, _auto_resub):
            assert script_name == "DFT_script.sh"

        def submit(self, submit_dir, relative_path):
            calls.append((submit_dir, relative_path))
            return SlurmJob(job_id="24680", path=relative_path)

        def submit_many(self, *_args, **_kwargs):
            raise AssertionError("single-job init must not use the multi-job path")

        def wait(self, *_args, **_kwargs):
            raise AssertionError("fire-and-forget init must not wait")

        def query(self, *_args, **_kwargs):
            raise AssertionError("fire-and-forget init must not poll Slurm")

    monkeypatch.setattr(build_module, "SlurmRunner", FireAndForgetRunner)

    assert main(["build", str(config_path)]) == 0

    root = work_dir / "init_mlff"
    derived = root / "DFT_script.sh"
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    assert calls == [(root, "init_mlff")]
    assert manifest["jobs"] == [
        {
            "job_id": "24680",
            "path": "init_mlff",
            "status": "SUBMITTED",
            "script": {
                "name": derived.name,
                "size": derived.stat().st_size,
                "sha256": hashlib.sha256(derived.read_bytes()).hexdigest(),
            },
        }
    ]
    assert manifest["init_workflow"]["state"] == "step-1-ready"
    assert not (root / "ML_ABN").exists()
    assert not (root / "ML_FFN").exists()
    assert capsys.readouterr().err == "build status=submission_requested stage=0\n"


def test_stage0_submit_without_any_target_reports_generated(
    monkeypatch,
    tmp_path,
    capsys,
):
    source_script = (
        "#!/bin/bash\n"
        "dpmoire_run_vasp() {\n"
        "    true\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n"
    )
    config_path, _work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
        submit=True,
    )
    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config_data.update(
        {
            "init_mlff": False,
            "init_mlff_mode": "manual",
            "do_relaxation": False,
            "twist_val": False,
        }
    )
    config_path.write_text(yaml.safe_dump(config_data), encoding="utf-8")

    class NoTargetRunner:
        def __init__(self, *_args, **_kwargs):
            pass

        def submit(self, *_args, **_kwargs):
            raise AssertionError("stage0 without targets must not call sbatch")

        def submit_many(self, *_args, **_kwargs):
            raise AssertionError("stage0 without targets must not call sbatch")

    monkeypatch.setattr(build_module, "SlurmRunner", NoTargetRunner)

    assert main(["build", str(config_path)]) == 0
    assert capsys.readouterr().err == "build status=generated stage=0\n"


def test_single_job_sbatch_failure_returns_nonzero_and_records_bounded_evidence(
    monkeypatch,
    tmp_path,
    capsys,
):
    source_script = (
        "#!/bin/bash\n"
        "#SBATCH --nodes=1\n"
        "dpmoire_run_vasp() {\n"
        "    synthetic_mpirun vasp_std\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n"
    )
    config_path, work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
        submit=True,
    )

    class FailingRunner:
        def __init__(self, _script_name, _n_nodes, _auto_resub):
            pass

        def submit(self, _submit_dir, _relative_path):
            raise subprocess.CalledProcessError(
                17,
                ["sbatch", "DFT_script.sh"],
                stderr="PRIVATE-SCHEDULER-DIAGNOSTIC",
            )

        def submit_many(self, *_args, **_kwargs):
            raise AssertionError("single-job init must not use the multi-job path")

        def wait(self, *_args, **_kwargs):
            raise AssertionError("failed fire-and-forget init must not wait")

        def query(self, *_args, **_kwargs):
            raise AssertionError("failed fire-and-forget init must not poll Slurm")

    monkeypatch.setattr(build_module, "SlurmRunner", FailingRunner)

    assert main(["build", str(config_path)]) == 1

    root = work_dir / "init_mlff"
    derived = root / "DFT_script.sh"
    manifest_text = (root / "manifest.yaml").read_text(encoding="utf-8")
    manifest = yaml.safe_load(manifest_text)
    assert manifest["jobs"] == [
        {
            "path": "init_mlff",
            "status": "SUBMIT_FAILED",
            "script": {
                "name": derived.name,
                "size": derived.stat().st_size,
                "sha256": hashlib.sha256(derived.read_bytes()).hexdigest(),
            },
            "failure": {
                "kind": "sbatch-invocation",
                "exception": "CalledProcessError",
                "returncode": 17,
            },
        }
    ]
    assert manifest["init_workflow"]["state"] == "step-1-ready"
    assert "PRIVATE-SCHEDULER-DIAGNOSTIC" not in manifest_text
    stderr = capsys.readouterr().err
    assert "build status=submission_failed" in stderr
    assert "manifest=init_mlff/manifest.yaml" in stderr
    assert "PRIVATE-SCHEDULER-DIAGNOSTIC" not in stderr


def test_single_job_submission_serializes_job_evidence_before_workflow_start(
    monkeypatch,
    tmp_path,
):
    source_script = (
        "#!/bin/bash\n"
        "#SBATCH --nodes=1\n"
        "dpmoire_run_vasp() {\n"
        "    synthetic_mpirun vasp_std\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n"
    )
    config_path, work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
        submit=True,
    )
    _write_op_layer(
        tmp_path / "input" / "bot_layer.poscar",
        [[0.0, 0.0, 1.0], [2.0, 2.0, 2.0]],
    )
    _write_op_layer(
        tmp_path / "input" / "top_layer.poscar",
        [[0.0, 0.0, 1.1], [2.1, 2.0, 2.0]],
    )
    for element in ("O", "Pt"):
        potcar = tmp_path / "potcars" / element / "POTCAR"
        potcar.parent.mkdir(parents=True)
        potcar.write_text(f"synthetic {element}\n ENMAX = 100;\n", encoding="utf-8")
    attempted = threading.Event()
    phase_started = threading.Event()
    allow_phase = threading.Event()
    thread_errors = []
    thread_holder = []
    started_during_submit = []

    class SuccessfulAdapter:
        def run(self, request):
            phase_started.set()
            if not allow_phase.wait(timeout=5):
                raise AssertionError("test did not release the synthetic calculation")
            if request.phase == "step1":
                return _write_valid_step1(request.directory)
            return _write_valid_step2(request.directory)

    def run_synthetic_job():
        attempted.set()
        try:
            InitMlffWorkflow(work_dir).run(SuccessfulAdapter())
        except BaseException as exc:
            thread_errors.append(exc)

    class ImmediateStartRunner:
        def __init__(self, _script_name, _n_nodes, _auto_resub):
            pass

        def submit(self, _submit_dir, _relative_path):
            thread = threading.Thread(target=run_synthetic_job, daemon=True)
            thread_holder.append(thread)
            thread.start()
            assert attempted.wait(timeout=2)
            started_during_submit.append(phase_started.wait(timeout=1))
            return SlurmJob(job_id="24680", path="init_mlff")

        def submit_many(self, *_args, **_kwargs):
            raise AssertionError("single-job init must not use the multi-job path")

        def wait(self, *_args, **_kwargs):
            raise AssertionError("fire-and-forget init must not wait")

        def query(self, *_args, **_kwargs):
            raise AssertionError("fire-and-forget init must not poll Slurm")

    monkeypatch.setattr(build_module, "SlurmRunner", ImmediateStartRunner)

    run_build(config_path, wait=False)
    allow_phase.set()
    thread_holder[0].join(timeout=5)

    assert not thread_holder[0].is_alive()
    assert thread_errors == []
    assert started_during_submit == [False]
    result = read_manifest(work_dir, "init_mlff")
    assert result.manifest is not None
    assert result.manifest.init_workflow["state"] == "complete"
    assert result.manifest.jobs[0]["status"] == "SUBMITTED"
    assert result.manifest.jobs[0]["job_id"] == "24680"


def test_single_job_root_publication_is_locked_against_an_early_contender(
    monkeypatch,
    tmp_path,
):
    source_script = (
        "#!/bin/bash\n"
        "#SBATCH --nodes=1\n"
        "dpmoire_run_vasp() {\n"
        "    synthetic_mpirun vasp_std\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n"
    )
    config_path, work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
        submit=True,
    )
    _write_op_layer(
        tmp_path / "input" / "bot_layer.poscar",
        [[0.0, 0.0, 1.0], [2.0, 2.0, 2.0]],
    )
    _write_op_layer(
        tmp_path / "input" / "top_layer.poscar",
        [[0.0, 0.0, 1.1], [2.1, 2.0, 2.0]],
    )
    for element in ("O", "Pt"):
        potcar = tmp_path / "potcars" / element / "POTCAR"
        potcar.parent.mkdir(parents=True)
        potcar.write_text(f"synthetic {element}\n ENMAX = 100;\n", encoding="utf-8")

    attempted = threading.Event()
    phase_started = threading.Event()
    allow_phase = threading.Event()
    thread_errors = []
    thread_holder = []
    started_before_publish_return = []
    submit_calls = []

    class SuccessfulAdapter:
        def run(self, request):
            phase_started.set()
            if not allow_phase.wait(timeout=5):
                raise AssertionError("test did not release the synthetic calculation")
            if request.phase == "step1":
                return _write_valid_step1(request.directory)
            return _write_valid_step2(request.directory)

    def run_contender():
        attempted.set()
        try:
            InitMlffWorkflow(work_dir).run(SuccessfulAdapter())
        except BaseException as exc:
            thread_errors.append(exc)

    real_publish = build_module.atomic_directory_publish_no_replace

    def publish_then_start_contender(candidate, destination):
        result = real_publish(candidate, destination)
        thread = threading.Thread(target=run_contender, daemon=True)
        thread_holder.append(thread)
        thread.start()
        assert attempted.wait(timeout=2)
        started_before_publish_return.append(phase_started.wait(timeout=1))
        return result

    class RecordingRunner:
        def __init__(self, _script_name, _n_nodes, _auto_resub):
            pass

        def submit(self, submit_dir, relative_path):
            submit_calls.append((submit_dir, relative_path))
            return SlurmJob(job_id="24680", path=relative_path)

        def submit_many(self, *_args, **_kwargs):
            raise AssertionError("single-job init must not use the multi-job path")

        def wait(self, *_args, **_kwargs):
            raise AssertionError("fire-and-forget init must not wait")

        def query(self, *_args, **_kwargs):
            raise AssertionError("fire-and-forget init must not poll Slurm")

    monkeypatch.setattr(
        build_module,
        "atomic_directory_publish_no_replace",
        publish_then_start_contender,
    )
    monkeypatch.setattr(build_module, "SlurmRunner", RecordingRunner)

    run_build(config_path, wait=False)
    allow_phase.set()
    thread_holder[0].join(timeout=5)

    assert not thread_holder[0].is_alive()
    assert thread_errors == []
    assert started_before_publish_return == [False]
    assert submit_calls == [(work_dir / "init_mlff", "init_mlff")]
    result = read_manifest(work_dir, "init_mlff")
    assert result.manifest is not None
    assert result.manifest.init_workflow["state"] == "complete"
    assert result.manifest.jobs[0]["status"] == "SUBMITTED"
    assert result.manifest.jobs[0]["job_id"] == "24680"


def test_single_job_build_renders_auditable_submit_adapter_without_execution(
    tmp_path,
    capsys,
):
    work_dir = tmp_path / "work dir's data"
    launch_sentinel = tmp_path / "launch-must-not-run"
    source_script = """#!/bin/bash
#SBATCH --job-name=synthetic-init
#SBATCH --nodes=1

module load synthetic-vasp
export SYNTHETIC_CLUSTER_SETTING=enabled

dpmoire_run_vasp() {
    touch launch-must-not-run
    synthetic_mpirun vasp_std > \"sout.${DPMOIRE_PHASE:-manual}\"
}

dpmoire_run_vasp # DPMOIRE-LITE:RUN
"""
    config_path, configured_work_dir, source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
        work_dir=work_dir,
    )
    source_bytes = source_path.read_bytes()

    assert main(["build", str(config_path)]) == 0

    assert source_path.read_bytes() == source_bytes
    assert not launch_sentinel.exists()
    assert list(tmp_path.rglob(launch_sentinel.name)) == []
    root = configured_work_dir / "init_mlff"
    derived_path = root / "DFT_script.sh"
    derived_bytes = derived_path.read_bytes()
    derived_text = derived_bytes.decode("utf-8")
    assert RUN_MARKER_LINE not in derived_text
    source_prefix, source_suffix = source_bytes.split(RUN_MARKER_LINE.encode("ascii"))
    assert derived_bytes.startswith(source_prefix)
    assert derived_bytes.endswith(source_suffix)
    for preserved in (
        "#!/bin/bash",
        "#SBATCH --job-name=synthetic-init",
        "module load synthetic-vasp",
        "export SYNTHETIC_CLUSTER_SETTING=enabled",
        "dpmoire_run_vasp() {",
        "synthetic_mpirun vasp_std",
    ):
        assert preserved in derived_text
    assert "export -f dpmoire_run_vasp" in derived_text
    assert (
        'DPmoireLite run-init-mlff --work-dir "$SLURM_SUBMIT_DIR/.."'
        in derived_text
    )
    assert str(configured_work_dir.resolve()) not in derived_text
    assert "_dpmoire_lite_status=$?" in derived_text
    assert 'exit "$_dpmoire_lite_status"' in derived_text

    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    workflow = manifest["init_workflow"]
    assert workflow["schema"] == "dpmoire-lite.init-workflow.v2"
    assert workflow["submit_source"] == {
        "name": source_path.name,
        "size": len(source_bytes),
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
    }
    assert workflow["submit_adapter"] == {
        "schema": "dpmoire-lite.init-submit-adapter.v1",
        "marker": RUN_MARKER_LINE,
        "launch_function": "dpmoire_run_vasp",
        "generated_script": {
            "name": derived_path.name,
            "size": len(derived_bytes),
            "sha256": hashlib.sha256(derived_bytes).hexdigest(),
        },
    }
    assert capsys.readouterr().err == "build status=generated stage=0\n"


@pytest.mark.parametrize(
    ("source_script", "message"),
    [
        (
            "#!/bin/bash\ndpmoire_run_vasp() {\n    true\n}\n",
            "marker",
        ),
        (
            "#!/bin/bash\ndpmoire_run_vasp() {\n    true\n}\n"
            f"{RUN_MARKER_LINE}\n{RUN_MARKER_LINE}\n",
            "exactly one",
        ),
        (
            f"#!/bin/bash\n{RUN_MARKER_LINE}\n"
            "dpmoire_run_vasp() {\n    true\n}\n",
            "after",
        ),
        (
            "#!/bin/bash\ndpmoire_run_vasp() {\n    true\n}\n"
            "dpmoire_run_vasp && true # DPMOIRE-LITE:RUN\n",
            "exact marker line",
        ),
        (
            f"#!/bin/bash\n{RUN_MARKER_LINE}\n",
            "function",
        ),
        (
            "#!/bin/bash\ndpmoire_run_vasp() (\n    true\n)\n"
            f"{RUN_MARKER_LINE}\n",
            "function",
        ),
        (
            "#!/bin/bash\ndpmoire_run_vasp() {\n    true\n"
            f"{RUN_MARKER_LINE}\n",
            "closing",
        ),
        (
            "#!/bin/bash\ndpmoire_run_vasp() {\n    true\n}\n"
            "#SBATCH --nodes=2\n"
            f"{RUN_MARKER_LINE}\n",
            "Slurm directives",
        ),
        (
            "#!/bin/bash\ndpmoire_run_vasp() {\n    true\n}\n"
            "outer() {\n"
            f"{RUN_MARKER_LINE}\n"
            "}\n",
            "top-level",
        ),
        (
            "#!/bin/bash\ndpmoire_run_vasp() {\n    true\n}\n"
            "function dpmoire_run_vasp {\n    true\n}\n"
            f"{RUN_MARKER_LINE}\n",
            "launch function",
        ),
        (
            "#!/bin/bash\n"
            "function dpmoire_run_vasp()\n"
            "{\n"
            "    true\n"
            "}\n"
            "dpmoire_run_vasp() {\n"
            "    true\n"
            "}\n"
            f"{RUN_MARKER_LINE}\n",
            "launch function",
        ),
        (
            "#!/bin/bash\ndpmoire_run_vasp() {\n    true\n}\n"
            f"{RUN_MARKER_LINE}\n"
            "printf 'unexpected epilogue\\n'\n",
            "final executable",
        ),
    ],
    ids=(
        "missing-marker",
        "duplicate-marker",
        "marker-before-function",
        "malformed-marker-line",
        "missing-function",
        "malformed-function",
        "unclosed-function",
        "misplaced-slurm-directive",
        "marker-inside-outer-function",
        "alternate-function-redefinition",
        "split-brace-function-redefinition",
        "executable-after-marker",
    ),
)
def test_single_job_submit_contract_fails_preflight_without_output(
    tmp_path,
    source_script,
    message,
):
    config_path, work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
    )

    with pytest.raises(RuntimeError, match=message):
        run_build(config_path, wait=False)

    assert not work_dir.exists()


def test_single_job_submit_name_cannot_collide_with_formal_root_entries(tmp_path):
    source_script = (
        "#!/bin/bash\n"
        "dpmoire_run_vasp() {\n"
        "    true\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n"
    )
    config_path, work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
        script_name="manifest.yaml",
    )

    with pytest.raises(RuntimeError, match="reserved init_mlff root entry"):
        run_build(config_path, wait=False)

    assert not work_dir.exists()


def test_single_job_submit_contract_accepts_login_bash_shebang(tmp_path):
    source_script = (
        "#!/bin/bash -l\n"
        "#SBATCH --nodes=1\n"
        "dpmoire_run_vasp() {\n"
        "    true\n"
        "}\n"
        f"{RUN_MARKER_LINE}\n"
    )
    config_path, work_dir, _source_path = _single_job_config(
        tmp_path,
        source_script=source_script,
    )

    run_build(config_path, wait=False)

    assert (work_dir / "init_mlff" / "DFT_script.sh").is_file()


def test_bash_function_adapter_propagates_phase_context_cwd_and_exit_code(
    monkeypatch,
    tmp_path,
    capsys,
):
    phase_dir = tmp_path / "phase with spaces"
    phase_dir.mkdir()
    observed = {}

    def synthetic_shell(command, *, cwd, env, check):
        observed.update(command=command, cwd=cwd, env=env, check=check)
        return subprocess.CompletedProcess(command, 23)

    monkeypatch.setattr(
        "dpmoire_lite.init_mlff_submit.subprocess.run",
        synthetic_shell,
    )
    request = InitMlffCalculation(
        phase="step2",
        role="step-2",
        name="top",
        directory=phase_dir,
    )

    exit_code = BashFunctionInitMlffAdapter().run(request)

    assert exit_code == 23
    assert observed["command"] == ["bash", "-c", "dpmoire_run_vasp"]
    assert observed["cwd"] == str(phase_dir)
    assert observed["check"] is False
    assert observed["env"]["DPMOIRE_PHASE"] == "step2"
    assert observed["env"]["DPMOIRE_PHASE_ROLE"] == "step-2"
    assert observed["env"]["DPMOIRE_PHASE_NAME"] == "top"
    stderr = capsys.readouterr().err
    assert "phase=step2" in stderr
    assert "role=step-2" in stderr


def test_run_init_mlff_cli_executes_both_phases_through_calculation_adapter(
    monkeypatch,
    tmp_path,
    capsys,
):
    _config_path, work_dir = _prepare_workflow(tmp_path)
    calls = []

    def synthetic_run(_adapter, request):
        calls.append((request.phase, request.directory))
        if request.phase == "step1":
            return _write_valid_step1(request.directory)
        return _write_valid_step2(request.directory)

    monkeypatch.setattr(BashFunctionInitMlffAdapter, "run", synthetic_run)

    assert main(["run-init-mlff", "--work-dir", str(work_dir)]) == 0

    assert calls == [
        ("step1", work_dir / "init_mlff" / "bottom"),
        ("step2", work_dir / "init_mlff" / "top"),
    ]
    result = read_manifest(work_dir, "init_mlff")
    assert result.manifest is not None
    assert result.manifest.init_workflow["state"] == "complete"
    assert (work_dir / "init_mlff" / "ML_ABN").is_file()
    assert (work_dir / "init_mlff" / "ML_FFN").is_file()
    assert "init_mlff status=complete" in capsys.readouterr().err


def test_run_init_mlff_cli_never_starts_step2_after_nonzero_step1(
    monkeypatch,
    tmp_path,
    capsys,
):
    _config_path, work_dir = _prepare_workflow(tmp_path)
    calls = []

    def failed_step1(_adapter, request):
        calls.append(request.phase)
        return 41

    monkeypatch.setattr(BashFunctionInitMlffAdapter, "run", failed_step1)

    exit_code = main(["run-init-mlff", "--work-dir", str(work_dir)])

    assert exit_code == 41
    assert calls == ["step1"]
    result = read_manifest(work_dir, "init_mlff")
    assert result.manifest is not None
    assert result.manifest.init_workflow["state"] == "step-1-failed"
    assert not (work_dir / "init_mlff" / "top" / "ML_AB").exists()
    stderr = capsys.readouterr().err
    assert "init_mlff status=failed" in stderr
    assert "exit_code=41" in stderr


def test_run_init_mlff_cli_never_starts_step2_after_invalid_step1_output(
    monkeypatch,
    tmp_path,
):
    _config_path, work_dir = _prepare_workflow(tmp_path)
    calls = []

    def nominal_success_without_outputs(_adapter, request):
        calls.append(request.phase)
        return 0

    monkeypatch.setattr(
        BashFunctionInitMlffAdapter,
        "run",
        nominal_success_without_outputs,
    )

    exit_code = main(["run-init-mlff", "--work-dir", str(work_dir)])

    assert exit_code == 1
    assert calls == ["step1"]
    result = read_manifest(work_dir, "init_mlff")
    assert result.manifest is not None
    assert result.manifest.init_workflow["state"] == "step-1-failed"
    assert not (work_dir / "init_mlff" / "top" / "ML_AB").exists()
