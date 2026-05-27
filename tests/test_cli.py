import subprocess
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from dpmoire_lite.cli import main


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
