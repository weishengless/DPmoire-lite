import hashlib
import subprocess

import pytest
import yaml

from dpmoire_lite.build import run_build
from dpmoire_lite.cli import main
from dpmoire_lite.init_mlff import InitMlffCalculation
from dpmoire_lite.init_mlff_submit import BashFunctionInitMlffAdapter
from dpmoire_lite.manifest import read_manifest

from test_build import write_build_config
from test_init_mlff import (
    _prepare_workflow,
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
):
    configured_work_dir = work_dir or tmp_path / "work"
    config_path = write_build_config(
        tmp_path,
        stage=0,
        dft_script=script_name,
        work_dir=str(configured_work_dir),
        init_mlff_mode="single-job",
        init_bottom_incar="init_bottom_INCAR",
        init_top_incar="init_top_INCAR",
        do_relaxation=False,
        twist_val=False,
        n_sectors=[1, 1],
    )
    _write_phase_templates(tmp_path / "input")
    source_path = tmp_path / "scripts" / script_name
    source_path.write_text(source_script, encoding="utf-8", newline="")
    return config_path, configured_work_dir, source_path


def test_single_job_build_renders_auditable_submit_adapter_without_execution(tmp_path):
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

    run_build(config_path, wait=False)

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


def test_run_init_mlff_cli_never_starts_step2_after_nonzero_step1(
    monkeypatch,
    tmp_path,
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
