import subprocess
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from dpmoire_lite.cli import main
from dpmoire_lite.config import ConfigError


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
