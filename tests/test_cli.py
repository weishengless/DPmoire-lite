import hashlib
import shutil
import subprocess
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path
import re
from uuid import uuid4

import pytest
import yaml

import dpmoire_lite.collect as collect_module
from dpmoire_lite.cli import main
from dpmoire_lite.collect_models import (
    CollectResult,
    CollectStatus,
    CollectionCandidate,
    MLFFCollectMode,
    RecoveredCollectionEvidence,
    SourceKind,
    SourceResult,
    SourceStatus,
)
from dpmoire_lite.config import ConfigError
from dpmoire_lite.manifest import Manifest, read_manifest, write_manifest
from dpmoire_lite.paths import manifest_path


@contextmanager
def local_tmp_dir(prefix):
    root = Path.cwd() / ".test-tmp"
    root.mkdir(exist_ok=True)
    path = root / f"{prefix}-{uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        import shutil

        shutil.rmtree(path, ignore_errors=True)


def test_main_help_exits_cleanly(capsys):
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    captured = capsys.readouterr()
    assert "DPmoireLite" in captured.out
    assert "build" in captured.out
    assert "collect" in captured.out
    assert "init-example" in captured.out


def test_collect_requires_stage(capsys):
    try:
        main(["collect", "config.yaml"])
    except SystemExit as exc:
        assert exc.code != 0
    captured = capsys.readouterr()
    assert "--stage" in captured.err


def _write_collect_cli_config(
    root,
    *,
    stage="rlx",
    vasp_ml=False,
    outcar_collect_freq=8,
):
    input_dir = root / "input"
    script_dir = root / "scripts"
    potcar_dir = root / "potcars"
    input_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)
    potcar_dir.mkdir(parents=True, exist_ok=True)
    work_dir = root / "work"
    config_path = root / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "dft_script": "DFT_script.sh",
                "potcar_dir": str(potcar_dir),
                "script_dir": str(script_dir),
                "input_dir": str(input_dir),
                "work_dir": str(work_dir),
                "n_nodes": 1,
                "stage": 0,
                "submit": False,
                "auto_resub": False,
                "vasp_ml": vasp_ml,
                "outcar_collect_freq": outcar_collect_freq,
                "do_relaxation": True,
                "init_mlff": True,
                "sc_rlx": True,
                "n_sectors": [1, 1],
                "sc": [1, 1],
                "d": 4.0,
                "k_mesh": 20,
                "encut_factor": 1.5,
                "r_cut": -1,
                "symm_reduce": False,
                "twist_val": False,
                "min_val_n": 4,
                "max_val_n": 5,
                "include_monolayer_md": False,
            }
        ),
        encoding="utf-8",
    )
    write_manifest(
        work_dir,
        Manifest(stage=stage, generated_at="cli-red", directories=[]),
    )
    return config_path


def _recovered_cli_result(
    status,
    *,
    frames,
    complete,
    partial=0,
    skipped=0,
    failed=0,
    warnings=(),
):
    return CollectResult(
        status=status,
        recovered_evidence=RecoveredCollectionEvidence(
            transaction_id="collect-20260714-120000-" + "a" * 32,
            status=status,
            frame_count=frames,
            sources_attempted=complete + partial + failed,
            sources_complete=complete,
            sources_partial=partial,
            sources_skipped=skipped,
            sources_failed=failed,
        ),
        publication_committed=True,
        warnings=warnings,
    )


def _replace_orchestrate_collect(monkeypatch, result):
    calls = []

    def fake_orchestrate_collect(**request):
        calls.append(request)
        return result

    monkeypatch.setattr(
        collect_module,
        "orchestrate_collect",
        fake_orchestrate_collect,
    )
    return calls


def _assert_orchestration_request(calls, *, config_path, stage, collection_mode):
    assert len(calls) == 1
    request = calls[0]
    assert request["config_path"] == config_path
    assert request["stage"] == stage
    assert request["collection_mode"] is collection_mode
    assert re.fullmatch(
        r"collect-\d{8}-\d{6}-[0-9a-f]{32}",
        request["transaction_id"],
    )


def _assert_one_stderr_summary(capsys, expected):
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert captured.err == expected + "\n"


def _main_exit_code(argv):
    try:
        return main(argv)
    except SystemExit as exc:
        return exc.code


def test_collect_cli_complete_returns_zero(monkeypatch, tmp_path, capsys):
    config_path = _write_collect_cli_config(tmp_path, stage="rlx")
    result = _recovered_cli_result(
        CollectStatus.COMPLETE,
        frames=3,
        complete=2,
    )
    calls = _replace_orchestrate_collect(monkeypatch, result)

    exit_code = main(["collect", str(config_path), "--stage", "rlx"])

    assert exit_code == 0
    _assert_orchestration_request(
        calls,
        config_path=config_path,
        stage="rlx",
        collection_mode=MLFFCollectMode.SEED_AWARE,
    )
    _assert_one_stderr_summary(
        capsys,
        "collect status=complete frames=3 sources=2 complete=2 partial=0 "
        "skipped=0 failed=0 output=rlx_data.extxyz",
    )


def test_collect_cli_fatal_returns_one(monkeypatch, tmp_path, capsys):
    config_path = _write_collect_cli_config(tmp_path, stage="rlx")
    result = CollectResult(
        status=CollectStatus.FATAL,
        fatal_diagnostic="manifest invariant failed\n  before publication",
    )
    calls = _replace_orchestrate_collect(monkeypatch, result)

    exit_code = main(["collect", str(config_path), "--stage", "rlx"])

    assert exit_code == 1
    _assert_orchestration_request(
        calls,
        config_path=config_path,
        stage="rlx",
        collection_mode=MLFFCollectMode.SEED_AWARE,
    )
    _assert_one_stderr_summary(
        capsys,
        "collect status=fatal frames=0 sources=0 complete=0 partial=0 skipped=0 "
        "failed=0 output=rlx_data.extxyz diagnostic=manifest invariant failed "
        "before publication",
    )


def test_collect_cli_degraded_returns_two(monkeypatch, tmp_path, capsys):
    config_path = _write_collect_cli_config(tmp_path, stage="validation")
    result = _recovered_cli_result(
        CollectStatus.DEGRADED,
        frames=4,
        complete=1,
        partial=1,
        skipped=1,
        failed=1,
    )
    calls = _replace_orchestrate_collect(monkeypatch, result)

    exit_code = main(["collect", str(config_path), "--stage", "validation"])

    assert exit_code == 2
    _assert_orchestration_request(
        calls,
        config_path=config_path,
        stage="validation",
        collection_mode=MLFFCollectMode.SEED_AWARE,
    )
    _assert_one_stderr_summary(
        capsys,
        "collect status=degraded frames=4 sources=4 complete=1 partial=1 skipped=1 "
        "failed=1 output=valid.extxyz",
    )


def test_collect_cli_no_data_returns_three(monkeypatch, tmp_path, capsys):
    config_path = _write_collect_cli_config(tmp_path, stage="rlx")
    candidate = CollectionCandidate(
        source_results=(
            SourceResult(
                source_path="rlx/skipped/OUTCAR",
                source_kind=SourceKind.OUTCAR,
                status=SourceStatus.SKIPPED,
                complete_count=0,
                reason="source was not ready",
            ),
            SourceResult(
                source_path="rlx/failed/OUTCAR",
                source_kind=SourceKind.OUTCAR,
                status=SourceStatus.FAILED,
                complete_count=0,
                reason="source parse failed",
            ),
        ),
        accepted_frames=(),
    )
    result = CollectResult(
        status=CollectStatus.NO_DATA,
        candidate=candidate,
        publication_committed=True,
    )
    calls = _replace_orchestrate_collect(monkeypatch, result)

    exit_code = main(["collect", str(config_path), "--stage", "rlx"])

    assert exit_code == 3
    _assert_orchestration_request(
        calls,
        config_path=config_path,
        stage="rlx",
        collection_mode=MLFFCollectMode.SEED_AWARE,
    )
    _assert_one_stderr_summary(
        capsys,
        "collect status=no_data frames=0 sources=2 complete=0 partial=0 skipped=1 "
        "failed=1 output=rlx_data.extxyz",
    )


def test_collect_cli_prints_short_summary_to_stderr(monkeypatch, tmp_path, capsys):
    config_path = _write_collect_cli_config(
        tmp_path,
        stage="md",
        vasp_ml=False,
    )
    result = _recovered_cli_result(
        CollectStatus.DEGRADED,
        frames=8,
        complete=2,
        partial=1,
        skipped=1,
        failed=1,
    )
    calls = _replace_orchestrate_collect(monkeypatch, result)

    exit_code = main(["collect", str(config_path), "--stage", "md"])

    _assert_one_stderr_summary(
        capsys,
        "collect status=degraded frames=8 sources=5 complete=2 partial=1 skipped=1 "
        "failed=1 output=MD_data.extxyz",
    )
    assert exit_code == 2
    _assert_orchestration_request(
        calls,
        config_path=config_path,
        stage="md",
        collection_mode=MLFFCollectMode.SEED_AWARE,
    )


def test_cli_does_not_infer_exit_code_from_manifest_or_log_text(
    monkeypatch,
    tmp_path,
    capsys,
):
    config_path = _write_collect_cli_config(tmp_path, stage="validation")
    result = _recovered_cli_result(
        CollectStatus.COMPLETE,
        frames=5,
        complete=1,
        warnings=(
            "manifest and log contain misleading fatal degraded no_data text",
        ),
    )
    calls = _replace_orchestrate_collect(monkeypatch, result)

    exit_code = main(["collect", str(config_path), "--stage", "validation"])

    assert exit_code == 0
    _assert_one_stderr_summary(
        capsys,
        "collect status=complete frames=5 sources=1 complete=1 partial=0 skipped=0 "
        "failed=0 output=valid.extxyz",
    )
    _assert_orchestration_request(
        calls,
        config_path=config_path,
        stage="validation",
        collection_mode=MLFFCollectMode.SEED_AWARE,
    )


def test_collect_cli_defaults_mlff_mode_to_seed_aware(
    monkeypatch,
    tmp_path,
    capsys,
):
    config_path = _write_collect_cli_config(tmp_path, stage="rlx")
    result = _recovered_cli_result(
        CollectStatus.COMPLETE,
        frames=2,
        complete=1,
    )
    calls = _replace_orchestrate_collect(monkeypatch, result)

    exit_code = main(["collect", str(config_path), "--stage", "rlx"])

    _assert_orchestration_request(
        calls,
        config_path=config_path,
        stage="rlx",
        collection_mode=MLFFCollectMode.SEED_AWARE,
    )
    assert exit_code == 0
    _assert_one_stderr_summary(
        capsys,
        "collect status=complete frames=2 sources=1 complete=1 partial=0 skipped=0 "
        "failed=0 output=rlx_data.extxyz",
    )


def test_collect_cli_accepts_full_dedup_only_for_mlff_md(
    monkeypatch,
    tmp_path,
    capsys,
):
    config_path = _write_collect_cli_config(tmp_path, stage="md", vasp_ml=True)
    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config_data["vasp_ml"] is True
    result = _recovered_cli_result(
        CollectStatus.COMPLETE,
        frames=2,
        complete=1,
    )
    calls = _replace_orchestrate_collect(monkeypatch, result)

    exit_code = _main_exit_code(
        [
            "collect",
            str(config_path),
            "--stage",
            "md",
            "--mlff-collect-mode",
            "full-dedup",
        ]
    )

    assert exit_code == 0
    _assert_orchestration_request(
        calls,
        config_path=config_path,
        stage="md",
        collection_mode=MLFFCollectMode.FULL_DEDUP,
    )
    _assert_one_stderr_summary(
        capsys,
        "collect status=complete frames=2 sources=1 complete=1 partial=0 skipped=0 "
        "failed=0 output=MD_data.extxyz",
    )


def test_collect_cli_rejects_full_dedup_for_rlx_validation_or_non_ml_md(
    monkeypatch,
    tmp_path,
    capsys,
):
    cases = (
        (
            _write_collect_cli_config(tmp_path / "rlx", stage="rlx", vasp_ml=True),
            "rlx",
            "rlx_data.extxyz",
        ),
        (
            _write_collect_cli_config(
                tmp_path / "validation",
                stage="validation",
                vasp_ml=True,
            ),
            "validation",
            "valid.extxyz",
        ),
        (
            _write_collect_cli_config(tmp_path / "md", stage="md", vasp_ml=False),
            "md",
            "MD_data.extxyz",
        ),
    )
    real_load_config = collect_module.load_config
    loaded_configs = []
    manifest_reads = []

    def tracking_load_config(config_path):
        loaded_configs.append(Path(config_path))
        return real_load_config(config_path)

    def forbidden_manifest_read(*args, **kwargs):
        manifest_reads.append((args, kwargs))
        raise AssertionError("stage manifest was read before full-dedup rejection")

    monkeypatch.setattr(collect_module, "load_config", tracking_load_config)
    monkeypatch.setattr(collect_module, "read_manifest", forbidden_manifest_read)

    observed = []
    for config_path, stage, output_basename in cases:
        exit_code = _main_exit_code(
            [
                "collect",
                str(config_path),
                "--stage",
                stage,
                "--mlff-collect-mode",
                "full-dedup",
            ]
        )
        captured = capsys.readouterr()
        observed.append(
            (
                exit_code,
                captured.out,
                captured.err,
                output_basename,
            )
        )

    assert loaded_configs == [case[0] for case in cases]
    assert manifest_reads == []
    for exit_code, stdout, stderr, output_basename in observed:
        assert exit_code == 1
        assert stdout == ""
        assert len(stderr.splitlines()) == 1
        assert stderr == (
            "collect status=fatal frames=0 sources=0 complete=0 partial=0 skipped=0 "
            f"failed=0 output={output_basename} "
            "diagnostic=full-dedup collection requires MLFF MD mode\n"
        )


TASK6_CLI_OUTCAR_FIXTURE = (
    Path(__file__).parent / "data" / "outcar" / "complete_two_frame.OUTCAR"
)


def _prepare_task6_cli_rlx_case(root, directories):
    config_path = _write_collect_cli_config(
        root,
        stage="rlx",
        vasp_ml=False,
        outcar_collect_freq=1,
    )
    work = root / "work"
    write_manifest(
        work,
        Manifest(
            stage="rlx",
            generated_at="task6-cli-e2e",
            directories=list(directories),
        ),
    )
    return config_path, work


def _task6_cli_output_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_complete_degraded_no_data_fatal_end_to_end_exit_codes(
    tmp_path,
    capsys,
):
    exit_codes = []

    complete_config, complete_work = _prepare_task6_cli_rlx_case(
        tmp_path / "complete",
        ["rlx/complete"],
    )
    complete_source = complete_work / "rlx" / "complete"
    complete_source.mkdir(parents=True)
    shutil.copy2(TASK6_CLI_OUTCAR_FIXTURE, complete_source / "OUTCAR")
    exit_codes.append(main(["collect", str(complete_config), "--stage", "rlx"]))
    complete_cli = capsys.readouterr()
    complete_manifest = read_manifest(complete_work, "rlx").manifest
    complete_output = complete_work / "rlx_data.extxyz"
    assert complete_manifest is not None
    assert complete_manifest.collect["status"] == "complete"
    assert complete_manifest.collect["frames"] == 2
    assert complete_manifest.collect["written"] is True
    assert [
        source["status"] for source in complete_manifest.collect["sources"]
    ] == ["complete"]
    assert complete_output.is_file()
    assert complete_manifest.collect["output_sha256"] == _task6_cli_output_sha256(
        complete_output
    )
    assert complete_cli.out == ""
    assert complete_cli.err == (
        "collect status=complete frames=2 sources=1 complete=1 partial=0 "
        "skipped=0 failed=0 output=rlx_data.extxyz\n"
    )

    degraded_config, degraded_work = _prepare_task6_cli_rlx_case(
        tmp_path / "degraded",
        ["rlx/complete", "rlx/missing"],
    )
    degraded_source = degraded_work / "rlx" / "complete"
    degraded_source.mkdir(parents=True)
    shutil.copy2(TASK6_CLI_OUTCAR_FIXTURE, degraded_source / "OUTCAR")
    exit_codes.append(main(["collect", str(degraded_config), "--stage", "rlx"]))
    degraded_cli = capsys.readouterr()
    degraded_manifest = read_manifest(degraded_work, "rlx").manifest
    degraded_output = degraded_work / "rlx_data.extxyz"
    assert degraded_manifest is not None
    assert degraded_manifest.collect["status"] == "degraded"
    assert degraded_manifest.collect["frames"] == 2
    assert degraded_manifest.collect["source_order"] == [
        "rlx/complete/OUTCAR",
        "rlx/missing",
    ]
    assert [
        source["status"] for source in degraded_manifest.collect["sources"]
    ] == ["complete", "skipped"]
    assert degraded_output.is_file()
    assert degraded_manifest.collect["output_sha256"] == _task6_cli_output_sha256(
        degraded_output
    )
    assert degraded_cli.out == ""
    assert degraded_cli.err == (
        "collect status=degraded frames=2 sources=2 complete=1 partial=0 "
        "skipped=1 failed=0 output=rlx_data.extxyz\n"
    )

    no_data_config, no_data_work = _prepare_task6_cli_rlx_case(
        tmp_path / "no-data",
        ["rlx/missing"],
    )
    exit_codes.append(main(["collect", str(no_data_config), "--stage", "rlx"]))
    no_data_cli = capsys.readouterr()
    no_data_manifest = read_manifest(no_data_work, "rlx").manifest
    assert no_data_manifest is not None
    assert no_data_manifest.collect["status"] == "no_data"
    assert no_data_manifest.collect["frames"] == 0
    assert no_data_manifest.collect["written"] is False
    assert no_data_manifest.collect["preserved_previous_output"] is False
    assert no_data_manifest.collect["sources"][0]["status"] == "skipped"
    assert "Missing declared directory" in no_data_manifest.collect["sources"][0][
        "reason"
    ]
    assert not (no_data_work / "rlx_data.extxyz").exists()
    assert no_data_cli.out == ""
    assert no_data_cli.err == (
        "collect status=no_data frames=0 sources=1 complete=0 partial=0 "
        "skipped=1 failed=0 output=rlx_data.extxyz\n"
    )

    fatal_config, fatal_work = _prepare_task6_cli_rlx_case(
        tmp_path / "fatal",
        [],
    )
    fatal_manifest_path = manifest_path(fatal_work, "rlx")
    fatal_manifest_path.unlink()
    exit_codes.append(main(["collect", str(fatal_config), "--stage", "rlx"]))
    fatal_cli = capsys.readouterr()
    assert not fatal_manifest_path.exists()
    assert not (fatal_work / "rlx_data.extxyz").exists()
    assert fatal_cli.out == ""
    assert fatal_cli.err == (
        "collect status=fatal frames=0 sources=0 complete=0 partial=0 skipped=0 "
        "failed=0 output=rlx_data.extxyz diagnostic=stage manifest for 'rlx' "
        "is missing; only explicit MD full-dedup may use compatibility discovery\n"
    )

    assert tuple(exit_codes) == (0, 2, 3, 1)


def test_build_help_marks_wait_temporarily_disabled_for_submitted_workflows(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["build", "--help"])

    assert exc_info.value.code == 0
    help_text = " ".join(capsys.readouterr().out.lower().split())
    assert "temporarily disabled" in help_text
    assert "submit: true" in help_text
    assert "stage: all is unavailable" in help_text


def test_build_error_recommends_manual_submission(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    example_config = repo_root / "src" / "dpmoire_lite" / "example" / "config.yaml"
    data = yaml.safe_load(example_config.read_text(encoding="utf-8"))
    data["stage"] = 0
    data["submit"] = True
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        main(["build", str(config_path), "--wait"])

    message = str(exc_info.value)
    assert "submit: false" in message
    assert "manually" in message


def test_init_example_copies_bundled_template():
    with local_tmp_dir("init-example") as tmp_path:
        target = tmp_path / "my_case"

        assert main(["init-example", str(target)]) == 0
        assert (target / "config.yaml").is_file()
        assert (target / "input" / "rlx_INCAR").is_file()
        assert (target / "scripts" / "sub").is_file()


def test_wheel_includes_bundled_example_template():
    if subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode != 0:
        pytest.skip("pip is unavailable in this Python environment")
    for module in ("setuptools", "wheel"):
        if subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True).returncode != 0:
            pytest.skip(f"{module} is unavailable in this Python environment")

    repo_root = Path(__file__).resolve().parents[1]
    with local_tmp_dir("wheel") as tmp_path:
        wheel_dir = tmp_path / "wheelhouse"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "-w",
                str(wheel_dir),
                str(repo_root),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        wheels = list(wheel_dir.glob("*.whl"))
        assert wheels, result.stdout + result.stderr
        with zipfile.ZipFile(wheels[0]) as wheel:
            names = set(wheel.namelist())
    assert "dpmoire_lite/example/config.yaml" in names
    assert "dpmoire_lite/example/input/rlx_INCAR" in names
    assert "dpmoire_lite/example/scripts/sub" in names


def test_wheel_example_contains_preserve_grid_shift_md_false():
    if subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode != 0:
        pytest.skip("pip is unavailable in this Python environment")
    for module in ("setuptools", "wheel"):
        if subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True).returncode != 0:
            pytest.skip(f"{module} is unavailable in this Python environment")

    repo_root = Path(__file__).resolve().parents[1]
    with local_tmp_dir("wheel-config") as tmp_path:
        wheel_dir = tmp_path / "wheelhouse"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "-w",
                str(wheel_dir),
                str(repo_root),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        wheels = list(wheel_dir.glob("*.whl"))
        assert wheels
        with zipfile.ZipFile(wheels[0]) as wheel:
            data = yaml.safe_load(wheel.read("dpmoire_lite/example/config.yaml"))

    assert data["preserve_grid_shift_md"] is False
