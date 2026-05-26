# DPmoire-lite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `DPmoire-lite`, a clean VASP calculation-folder generator and `extxyz` dataset collector based on the approved design spec.

**Architecture:** Create a new package under `src/dpmoire_lite` with focused modules for config, paths, manifests, structure generation, VASP input generation, Slurm orchestration, build stages, and collection. Reuse proven parsing and structure algorithms from `E:\codespace\MLFF\DPmoire-master`, but expose only the new snake_case config and stage-based workflow.

**Tech Stack:** Python 3.10+, `numpy`, `ase`, `pyyaml`, `pymatgen`, `spglib`, `pytest`, Git.

---

## Scope Check

The approved spec covers one coherent product: a command line tool that builds VASP dataset calculation folders and collects results. The work spans several modules, but each module is required for the first usable release. This plan keeps every task independently testable and commits after each task.

## File Structure

Create or modify these files:

- Create: `pyproject.toml`
- Create: `README.md`
- Create: `.gitignore`
- Create: `example/config.yaml`
- Create: `example/input/init_INCAR`
- Create: `example/input/rlx_INCAR`
- Create: `example/input/MD_INCAR`
- Create: `example/input/MD_monolayer_INCAR`
- Create: `example/input/val_INCAR`
- Create: `example/input/top_layer.poscar`
- Create: `example/input/bot_layer.poscar`
- Create: `example/scripts/DFT_script.sh`
- Create: `src/dpmoire_lite/__init__.py`
- Create: `src/dpmoire_lite/cli.py`
- Create: `src/dpmoire_lite/config.py`
- Create: `src/dpmoire_lite/paths.py`
- Create: `src/dpmoire_lite/manifest.py`
- Create: `src/dpmoire_lite/outcar.py`
- Create: `src/dpmoire_lite/dataset.py`
- Create: `src/dpmoire_lite/structures.py`
- Create: `src/dpmoire_lite/inputs.py`
- Create: `src/dpmoire_lite/slurm.py`
- Create: `src/dpmoire_lite/build.py`
- Create: `src/dpmoire_lite/collect.py`
- Create: `tests/conftest.py`
- Create: `tests/test_cli.py`
- Create: `tests/test_config.py`
- Create: `tests/test_paths_manifest.py`
- Create: `tests/test_outcar.py`
- Create: `tests/test_structures_inputs.py`
- Create: `tests/test_build.py`
- Create: `tests/test_collect.py`

Source algorithms to port and adapt:

- `E:\codespace\MLFF\DPmoire-master\DPmoire\preprocess\structure_handler.py`
- `E:\codespace\MLFF\DPmoire-master\DPmoire\preprocess\env_handler.py`
- `E:\codespace\MLFF\DPmoire-master\DPmoire\data\dataset.py`
- `E:\codespace\MLFF\DPmoire-master\DPmoire\dft\dft_handler.py`

---

### Task 1: Package Scaffold And CLI Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/dpmoire_lite/__init__.py`
- Create: `src/dpmoire_lite/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI smoke tests**

Create `tests/test_cli.py`:

```python
from dpmoire_lite.cli import main


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
```

- [ ] **Step 2: Run tests and verify they fail because the package is missing**

Run:

```bash
python -m pytest tests/test_cli.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'dpmoire_lite'`.

- [ ] **Step 3: Create project metadata**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "DPmoire-lite"
version = "0.1.0"
description = "Generate VASP dataset calculation folders and collect extxyz datasets."
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
  "numpy",
  "ase",
  "pyyaml",
  "pymatgen",
  "spglib",
]

[project.optional-dependencies]
dev = ["pytest"]

[project.scripts]
DPmoireLite = "dpmoire_lite.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

Create `.gitignore`:

```gitignore
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.mypy_cache/
build/
dist/
*.egg-info/
.venv/
```

- [ ] **Step 4: Create the package and CLI skeleton**

Create `src/dpmoire_lite/__init__.py`:

```python
"""DPmoire-lite: VASP dataset folder generation and collection."""

__version__ = "0.1.0"
```

Create `src/dpmoire_lite/cli.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


def build_command(config_path: str, wait: bool) -> int:
    from .build import run_build

    run_build(Path(config_path), wait=wait)
    return 0


def collect_command(config_path: str, stage: str) -> int:
    from .collect import run_collect

    run_collect(Path(config_path), stage=stage)
    return 0


def init_example_command(target_dir: str) -> int:
    from .inputs import copy_example

    copy_example(Path(target_dir))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="DPmoireLite", description="DPmoireLite VASP dataset builder")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Generate and optionally submit calculation folders")
    build.add_argument("config", help="Path to config.yaml")
    build.add_argument("--wait", action="store_true", help="Wait for submitted Slurm jobs to finish")

    collect = subparsers.add_parser("collect", help="Collect a dataset from completed calculations")
    collect.add_argument("config", help="Path to config.yaml")
    collect.add_argument("--stage", required=True, choices=["rlx", "md", "validation"], help="Stage to collect")

    init_example = subparsers.add_parser("init-example", help="Copy the bundled example template")
    init_example.add_argument("target_dir", help="Directory to create from the example template")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "build":
        return build_command(args.config, wait=args.wait)
    if args.command == "collect":
        return collect_command(args.config, stage=args.stage)
    if args.command == "init-example":
        return init_example_command(args.target_dir)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Add temporary called-module stubs so CLI tests pass**

Create `src/dpmoire_lite/build.py`:

```python
from __future__ import annotations

from pathlib import Path


def run_build(config_path: Path, wait: bool = False) -> None:
    _ = (config_path, wait)
    raise NotImplementedError("build implementation is added in later tasks")
```

Create `src/dpmoire_lite/collect.py`:

```python
from __future__ import annotations

from pathlib import Path


def run_collect(config_path: Path, stage: str) -> None:
    _ = (config_path, stage)
    raise NotImplementedError("collect implementation is added in later tasks")
```

Create `src/dpmoire_lite/inputs.py`:

```python
from __future__ import annotations

from pathlib import Path


def copy_example(target_dir: Path) -> None:
    _ = target_dir
    raise NotImplementedError("example copying is added in later tasks")
```

- [ ] **Step 6: Run tests and verify CLI behavior passes**

Run:

```bash
python -m pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit scaffold**

Run:

```bash
git add pyproject.toml .gitignore src/dpmoire_lite tests/test_cli.py
git commit -m "feat: scaffold DPmoireLite package"
```

---

### Task 2: Config Parsing And Validation

**Files:**
- Create: `src/dpmoire_lite/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing config tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

import pytest
import yaml

from dpmoire_lite.config import ConfigError, load_config, normalize_pair


def write_config(path: Path, **overrides):
    data = {
        "dft_script": "DFT_script.sh",
        "potcar_dir": str(path.parent / "potcars"),
        "script_dir": str(path.parent / "scripts"),
        "input_dir": str(path.parent / "input"),
        "work_dir": str(path.parent / "work"),
        "n_nodes": 2,
        "stage": 0,
        "submit": False,
        "auto_resub": False,
        "vasp_ml": True,
        "outcar_collect_freq": 8,
        "do_relaxation": True,
        "init_mlff": True,
        "sc_rlx": True,
        "n_sectors": [9, 8],
        "sc": [2, 3],
        "d": 6.3,
        "k_mesh": 40,
        "encut_factor": 1.6,
        "r_cut": -1,
        "symm_reduce": True,
        "twist_val": True,
        "min_val_n": 4,
        "max_val_n": 5,
        "include_monolayer_md": True,
    }
    data.update(overrides)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_normalize_pair_accepts_int_and_pair():
    assert normalize_pair(9, field="n_sectors") == (9, 9)
    assert normalize_pair([9, 8], field="n_sectors") == (9, 8)


def test_normalize_pair_rejects_bad_values():
    with pytest.raises(ConfigError, match="n_sectors"):
        normalize_pair([9, 8, 7], field="n_sectors")
    with pytest.raises(ConfigError, match="positive"):
        normalize_pair([9, 0], field="n_sectors")


def test_load_config_resolves_paths(tmp_path):
    (tmp_path / "potcars").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "input").mkdir()
    config_file = tmp_path / "config.yaml"
    write_config(config_file)
    config = load_config(config_file)
    assert config.n_sectors == (9, 8)
    assert config.sc == (2, 3)
    assert config.input_dir == tmp_path / "input"
    assert config.work_dir == tmp_path / "work"


def test_stage_all_requires_submit_wait_at_build_time(tmp_path):
    (tmp_path / "potcars").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "input").mkdir()
    config_file = tmp_path / "config.yaml"
    write_config(config_file, stage="all", submit=False)
    config = load_config(config_file)
    with pytest.raises(ConfigError, match="stage: all"):
        config.validate_build_mode(wait=False)


def test_old_field_names_are_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("VASP_ML: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="snake_case"):
        load_config(config_file)
```

- [ ] **Step 2: Run tests and verify they fail because `config.py` is missing**

Run:

```bash
python -m pytest tests/test_config.py -v
```

Expected: FAIL with `ModuleNotFoundError` or import error for `dpmoire_lite.config`.

- [ ] **Step 3: Implement config parsing**

Create `src/dpmoire_lite/config.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when a DPmoire-lite config is invalid."""


DEFAULT_OUTCAR_PATTERNS = (
    r"^OUTCAR$",
    r"^OUTCAR\d+$",
    r"^OUT\d+$",
    r"^out\d+$",
)

OLD_FIELD_NAMES = {
    "VASP_ML",
    "K-mesh",
    "POTCAR_dir",
    "DFT_script",
    "ENMAX",
    "OUTCAR_collect_freq",
}


def normalize_pair(value: Any, field: str) -> tuple[int, int]:
    if isinstance(value, bool):
        raise ConfigError(f"{field} must be a positive integer or a two-element list")
    if isinstance(value, int):
        if value <= 0:
            raise ConfigError(f"{field} values must be positive")
        return value, value
    if isinstance(value, (list, tuple)) and len(value) == 2:
        x, y = value
        if not isinstance(x, int) or not isinstance(y, int):
            raise ConfigError(f"{field} values must be integers")
        if x <= 0 or y <= 0:
            raise ConfigError(f"{field} values must be positive")
        return x, y
    raise ConfigError(f"{field} must be a positive integer or a two-element list")


@dataclass(frozen=True)
class DPmoireLiteConfig:
    config_path: Path
    dft_script: str
    potcar_dir: Path
    script_dir: Path
    input_dir: Path
    work_dir: Path
    n_nodes: int
    stage: int | str
    submit: bool
    auto_resub: bool
    vasp_ml: bool
    outcar_collect_freq: int
    do_relaxation: bool
    init_mlff: bool
    sc_rlx: bool
    n_sectors: tuple[int, int]
    sc: tuple[int, int]
    d: float
    k_mesh: int
    encut_factor: float
    r_cut: float
    symm_reduce: bool
    twist_val: bool
    min_val_n: int
    max_val_n: int
    include_monolayer_md: bool
    outcar_patterns: tuple[str, ...] = field(default_factory=lambda: DEFAULT_OUTCAR_PATTERNS)

    def validate_build_mode(self, wait: bool) -> None:
        if self.stage == "all" and (not self.submit or not wait):
            raise ConfigError("stage: all requires submit: true and DPmoireLite build ... --wait")

    @property
    def sc_x(self) -> int:
        return self.sc[0]

    @property
    def sc_y(self) -> int:
        return self.sc[1]

    @property
    def n_sector_x(self) -> int:
        return self.n_sectors[0]

    @property
    def n_sector_y(self) -> int:
        return self.n_sectors[1]


def _resolve_path(base: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _require(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ConfigError(f"Missing required config field: {key}")
    return data[key]


def load_config(path: Path) -> DPmoireLiteConfig:
    path = path.resolve()
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ConfigError("Config file must contain a YAML mapping")
    old_keys = sorted(set(raw).intersection(OLD_FIELD_NAMES))
    if old_keys:
        raise ConfigError(f"Old config fields are not accepted; use snake_case fields instead: {', '.join(old_keys)}")

    base = path.parent
    stage = _require(raw, "stage")
    if stage in ("0", "1"):
        stage = int(stage)
    if stage not in (0, 1, "all"):
        raise ConfigError("stage must be 0, 1, or all")

    n_nodes = int(_require(raw, "n_nodes"))
    if n_nodes <= 0:
        raise ConfigError("n_nodes must be positive")

    outcar_freq = int(_require(raw, "outcar_collect_freq"))
    if outcar_freq <= 0:
        raise ConfigError("outcar_collect_freq must be positive")

    return DPmoireLiteConfig(
        config_path=path,
        dft_script=str(_require(raw, "dft_script")),
        potcar_dir=_resolve_path(base, str(_require(raw, "potcar_dir"))),
        script_dir=_resolve_path(base, str(_require(raw, "script_dir"))),
        input_dir=_resolve_path(base, str(_require(raw, "input_dir"))),
        work_dir=_resolve_path(base, str(_require(raw, "work_dir"))),
        n_nodes=n_nodes,
        stage=stage,
        submit=bool(_require(raw, "submit")),
        auto_resub=bool(_require(raw, "auto_resub")),
        vasp_ml=bool(_require(raw, "vasp_ml")),
        outcar_collect_freq=outcar_freq,
        do_relaxation=bool(_require(raw, "do_relaxation")),
        init_mlff=bool(_require(raw, "init_mlff")),
        sc_rlx=bool(_require(raw, "sc_rlx")),
        n_sectors=normalize_pair(_require(raw, "n_sectors"), field="n_sectors"),
        sc=normalize_pair(_require(raw, "sc"), field="sc"),
        d=float(_require(raw, "d")),
        k_mesh=int(_require(raw, "k_mesh")),
        encut_factor=float(_require(raw, "encut_factor")),
        r_cut=float(_require(raw, "r_cut")),
        symm_reduce=bool(_require(raw, "symm_reduce")),
        twist_val=bool(_require(raw, "twist_val")),
        min_val_n=int(_require(raw, "min_val_n")),
        max_val_n=int(_require(raw, "max_val_n")),
        include_monolayer_md=bool(_require(raw, "include_monolayer_md")),
        outcar_patterns=tuple(raw.get("outcar_patterns", DEFAULT_OUTCAR_PATTERNS)),
    )
```

- [ ] **Step 4: Run config tests**

Run:

```bash
python -m pytest tests/test_config.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit config parsing**

Run:

```bash
git add src/dpmoire_lite/config.py tests/test_config.py
git commit -m "feat: add config parser"
```

---

### Task 3: Path Layout, Backups, And Manifest Files

**Files:**
- Create: `src/dpmoire_lite/paths.py`
- Create: `src/dpmoire_lite/manifest.py`
- Create: `tests/test_paths_manifest.py`

- [ ] **Step 1: Write failing tests for stage paths, backups, and manifests**

Create `tests/test_paths_manifest.py`:

```python
from pathlib import Path

from dpmoire_lite.manifest import Manifest, read_manifest, write_manifest
from dpmoire_lite.paths import backup_existing_directory, manifest_path, relative_to_workdir, stage_dir


def test_stage_dir_layout(tmp_path):
    work = tmp_path / "work"
    assert stage_dir(work, "rlx") == work / "rlx"
    assert stage_dir(work, "md") == work / "md"
    assert manifest_path(work, "validation") == work / "validation" / "manifest.yaml"


def test_relative_to_workdir_uses_posix_paths(tmp_path):
    work = tmp_path / "work"
    target = work / "rlx" / "0_0"
    assert relative_to_workdir(work, target) == "rlx/0_0"


def test_backup_existing_directory_moves_single_target(tmp_path):
    work = tmp_path / "work"
    target = work / "rlx" / "0_0"
    target.mkdir(parents=True)
    (target / "OUTCAR").write_text("result", encoding="utf-8")
    backup = backup_existing_directory(work, "rlx", target, timestamp="20260526-211500")
    assert backup == work / "backups" / "rlx" / "0_0_20260526-211500"
    assert (backup / "OUTCAR").read_text(encoding="utf-8") == "result"
    assert not target.exists()


def test_manifest_round_trip(tmp_path):
    work = tmp_path / "work"
    manifest = Manifest(
        stage="rlx",
        generated_at="2026-05-26T21:15:00",
        config_summary={"stage": 0},
        directories=["rlx/0_0"],
        backups=["backups/rlx/0_0_20260526-211500"],
        jobs=[{"id": "123", "path": "rlx/0_0", "status": "SUBMITTED"}],
        collect={"frames": 8},
        skipped=[{"path": "rlx/0_1", "reason": "missing OUTCAR"}],
        failed=[],
        stackings=[[0, 0]],
        angles=[],
    )
    write_manifest(work, manifest)
    loaded = read_manifest(work, "rlx")
    assert loaded.stage == "rlx"
    assert loaded.directories == ["rlx/0_0"]
    assert loaded.stackings == [[0, 0]]
```

- [ ] **Step 2: Run tests and verify they fail because modules are missing**

Run:

```bash
python -m pytest tests/test_paths_manifest.py -v
```

Expected: FAIL with import errors.

- [ ] **Step 3: Implement path helpers**

Create `src/dpmoire_lite/paths.py`:

```python
from __future__ import annotations

import shutil
from pathlib import Path


VALID_STAGES = {"init_mlff", "rlx", "md", "validation"}


def stage_dir(work_dir: Path, stage: str) -> Path:
    if stage not in VALID_STAGES:
        raise ValueError(f"Unknown stage: {stage}")
    return work_dir / stage


def manifest_path(work_dir: Path, stage: str) -> Path:
    return stage_dir(work_dir, stage) / "manifest.yaml"


def relative_to_workdir(work_dir: Path, path: Path) -> str:
    return path.resolve().relative_to(work_dir.resolve()).as_posix()


def backup_existing_directory(work_dir: Path, stage: str, target: Path, timestamp: str) -> Path | None:
    if not target.exists():
        return None
    backup_root = work_dir / "backups" / stage
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / f"{target.name}_{timestamp}"
    if backup.exists():
        raise FileExistsError(f"Backup target already exists: {backup}")
    shutil.move(str(target), str(backup))
    return backup
```

- [ ] **Step 4: Implement manifest round-trip**

Create `src/dpmoire_lite/manifest.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .paths import manifest_path


@dataclass
class Manifest:
    stage: str
    generated_at: str
    config_summary: dict[str, Any] = field(default_factory=dict)
    directories: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)
    jobs: list[dict[str, Any]] = field(default_factory=list)
    collect: dict[str, Any] = field(default_factory=dict)
    skipped: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    stackings: list[list[int]] = field(default_factory=list)
    angles: list[str] = field(default_factory=list)


def write_manifest(work_dir: Path, manifest: Manifest) -> None:
    path = manifest_path(work_dir, manifest.stage)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(asdict(manifest), handle, sort_keys=False)


def read_manifest(work_dir: Path, stage: str) -> Manifest | None:
    path = manifest_path(work_dir, stage)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return Manifest(**data)
```

- [ ] **Step 5: Run path and manifest tests**

Run:

```bash
python -m pytest tests/test_paths_manifest.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit path and manifest support**

Run:

```bash
git add src/dpmoire_lite/paths.py src/dpmoire_lite/manifest.py tests/test_paths_manifest.py
git commit -m "feat: add stage paths and manifests"
```

---

### Task 4: OUTCAR Series Discovery

**Files:**
- Create: `src/dpmoire_lite/outcar.py`
- Create: `tests/test_outcar.py`

- [ ] **Step 1: Write failing OUTCAR discovery tests**

Create `tests/test_outcar.py`:

```python
import os

from dpmoire_lite.outcar import find_outcar_series


def touch(path, mtime):
    path.write_text(path.name, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_find_outcar_series_uses_default_patterns_and_mtime(tmp_path):
    touch(tmp_path / "OUTCAR1", 30)
    touch(tmp_path / "OUTCAR", 40)
    touch(tmp_path / "out0", 10)
    touch(tmp_path / "OUT0", 20)
    touch(tmp_path / "OUTCAR-relax", 5)
    touch(tmp_path / "OUTCAR.bad", 6)
    names = [path.name for path in find_outcar_series(tmp_path)]
    assert names == ["out0", "OUT0", "OUTCAR1", "OUTCAR"]


def test_find_outcar_series_accepts_config_regex(tmp_path):
    touch(tmp_path / "history.relax", 10)
    touch(tmp_path / "OUTCAR", 20)
    names = [path.name for path in find_outcar_series(tmp_path, patterns=[r"^history\.relax$"])]
    assert names == ["history.relax"]
```

- [ ] **Step 2: Run tests and verify they fail because `outcar.py` is missing**

Run:

```bash
python -m pytest tests/test_outcar.py -v
```

Expected: FAIL with import error.

- [ ] **Step 3: Implement OUTCAR series discovery**

Create `src/dpmoire_lite/outcar.py`:

```python
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from ase.io.vasp import read_vasp_out

from .config import DEFAULT_OUTCAR_PATTERNS


def find_outcar_series(directory: Path, patterns: Iterable[str] = DEFAULT_OUTCAR_PATTERNS) -> list[Path]:
    compiled = [re.compile(pattern) for pattern in patterns]
    matches: list[Path] = []
    if not directory.exists():
        return matches
    for child in directory.iterdir():
        if not child.is_file():
            continue
        if any(pattern.match(child.name) for pattern in compiled):
            matches.append(child)
    return sorted(matches, key=lambda path: (path.stat().st_mtime, path.name))


def read_outcar_frames(path: Path):
    return read_vasp_out(str(path), ":")
```

- [ ] **Step 4: Run OUTCAR tests**

Run:

```bash
python -m pytest tests/test_outcar.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit OUTCAR discovery**

Run:

```bash
git add src/dpmoire_lite/outcar.py tests/test_outcar.py
git commit -m "feat: add OUTCAR series discovery"
```

---

### Task 5: Dataset Parsing And extxyz Writing

**Files:**
- Create: `src/dpmoire_lite/dataset.py`
- Modify: `src/dpmoire_lite/outcar.py`
- Create: `tests/conftest.py`
- Create: `tests/test_collect.py`

- [ ] **Step 1: Write failing tests for ML_ABN frame counting and sample availability**

Create `tests/conftest.py`:

```python
from pathlib import Path

import pytest


SAMPLE_DIR = Path(r"E:\codespace\MLFF\03_constrained_shear_scan")


@pytest.fixture
def sample_dir():
    if not SAMPLE_DIR.exists():
        pytest.skip(f"Sample directory not available: {SAMPLE_DIR}")
    return SAMPLE_DIR
```

Create the initial content of `tests/test_collect.py`:

```python
import shutil

from dpmoire_lite.dataset import Dataset


def test_load_ml_abn_sample_counts_frames(tmp_path, sample_dir):
    source = sample_dir / "ML_ABN"
    target = tmp_path / "ML_ABN"
    shutil.copy2(source, target)
    dataset = Dataset()
    dataset.load_ml_ab(target)
    assert dataset.n_configs > 0
    assert len(dataset.data) == dataset.n_configs
```

- [ ] **Step 2: Run test and verify it fails because `dataset.py` is missing**

Run:

```bash
python -m pytest tests/test_collect.py::test_load_ml_abn_sample_counts_frames -v
```

Expected: FAIL with import error.

- [ ] **Step 3: Implement the Dataset class by porting the existing parser**

Create `src/dpmoire_lite/dataset.py` by porting these exact behaviors from `E:\codespace\MLFF\DPmoire-master\DPmoire\data\dataset.py`:

- `load_dataset_AB` becomes `load_ml_ab(path: Path, skip_configs: int = 0)`.
- `load_dataset_OUTCAR` becomes `load_outcar(path: Path, freq: int)`.
- `load_dataset_extxyz` becomes `load_extxyz(path: Path)`.
- `load_dataset_Atoms` becomes `add_atoms(structure: Atoms)`.
- `save_extxyz` becomes `save_extxyz(path: Path)`.
- Preserve stress conversion from kBar to ASE stress units.
- Preserve `SinglePointCalculator` storage for energy, forces, and stress.

The public class shape must be:

```python
from __future__ import annotations

from pathlib import Path

import numpy as np
import re
from ase import Atoms
from ase.io import read as ase_read
from ase.io import write as ase_write
from ase.io.vasp import read_vasp_out
from ase.units import GPa
from ase.calculators.singlepoint import SinglePointCalculator


class Dataset:
    def __init__(self):
        self.n_configs = 0
        self.data: list[Atoms] = []

    def load_ml_ab(self, path: Path, skip_configs: int = 0) -> None:
        # Port the old DPmoire ML_AB parser here with pathlib input.
        # Increment n_configs only for stored structures after skip_configs.
        # Use the same energy, force, lattice, symbol, and stress parsing.
        raise NotImplementedError

    def count_ml_ab_configs(self, path: Path) -> int:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line_number, line in enumerate(handle):
                if line_number == 4:
                    return int(line.split()[0])
        return 0

    def load_outcar(self, path: Path, freq: int) -> None:
        for idx, structure in enumerate(read_vasp_out(str(path), ":")):
            if idx % freq == 0:
                self.add_atoms(structure)

    def load_extxyz(self, path: Path) -> None:
        structures = ase_read(str(path), format="extxyz", index=":")
        for structure in structures:
            self.add_atoms(structure)

    def add_atoms(self, structure: Atoms) -> None:
        stored = Atoms(
            positions=structure.get_positions(),
            symbols=structure.get_chemical_symbols(),
            cell=structure.get_cell(),
            pbc=structure.get_pbc(),
        )
        calc = SinglePointCalculator(
            stored,
            energy=structure.get_potential_energy(apply_constraint=False),
            forces=structure.get_forces(apply_constraint=False),
            stress=structure.get_stress(apply_constraint=False),
        )
        stored.calc = calc
        self.data.append(stored)
        self.n_configs += 1

    def save_extxyz(self, path: Path) -> None:
        ase_write(str(path), self.data, format="extxyz")
```

Replace `raise NotImplementedError` in `load_ml_ab` with the ported old logic in the same task. Adjust one important bug during the port: `self.n_configs` must count stored frames, not the raw file header before `skip_configs`.

- [ ] **Step 4: Run the ML_ABN sample test**

Run:

```bash
python -m pytest tests/test_collect.py::test_load_ml_abn_sample_counts_frames -v
```

Expected: PASS when the sample directory exists; SKIP if the sample directory is unavailable.

- [ ] **Step 5: Add an OUTCAR sample collect test**

Append to `tests/test_collect.py`:

```python
def test_load_outcar_sample_counts_frames(tmp_path, sample_dir):
    source = sample_dir / "lambda_p0p00" / "OUTCAR"
    target = tmp_path / "OUTCAR"
    shutil.copy2(source, target)
    dataset = Dataset()
    dataset.load_outcar(target, freq=1)
    assert dataset.n_configs > 0
```

- [ ] **Step 6: Run dataset tests**

Run:

```bash
python -m pytest tests/test_collect.py::test_load_ml_abn_sample_counts_frames tests/test_collect.py::test_load_outcar_sample_counts_frames -v
```

Expected: PASS or SKIP depending on sample directory availability.

- [ ] **Step 7: Commit dataset parsing**

Run:

```bash
git add src/dpmoire_lite/dataset.py tests/conftest.py tests/test_collect.py
git commit -m "feat: add dataset parsers"
```

---

### Task 6: Structure Generation

**Files:**
- Create: `src/dpmoire_lite/structures.py`
- Create: `tests/test_structures_inputs.py`

- [ ] **Step 1: Write failing tests for rectangular stackings and CONTCAR rewrite**

Create `tests/test_structures_inputs.py`:

```python
from ase import Atoms
from ase.io.vasp import read_vasp

from dpmoire_lite.structures import generate_stackings, rewrite_contcar_as_poscar


def test_generate_stackings_supports_rectangular_grid():
    assert generate_stackings((3, 2)) == [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)]


def test_rewrite_contcar_as_poscar_removes_velocity_block(tmp_path):
    atoms = Atoms("H2", positions=[[0, 0, 0], [0, 0, 0.74]], cell=[8, 8, 8], pbc=True)
    contcar = tmp_path / "CONTCAR"
    poscar = tmp_path / "POSCAR"
    contcar.write(contcar, format="vasp", direct=True)
    with contcar.open("a", encoding="utf-8") as handle:
        handle.write("\n  0.0 0.0 0.0\n  0.0 0.0 0.0\n")
    rewrite_contcar_as_poscar(contcar, poscar)
    text = poscar.read_text(encoding="utf-8")
    assert text.count("0.0 0.0 0.0") == 0
    loaded = read_vasp(poscar)
    assert len(loaded) == 2
```

- [ ] **Step 2: Run tests and verify they fail because `structures.py` is missing**

Run:

```bash
python -m pytest tests/test_structures_inputs.py -v
```

Expected: FAIL with import error.

- [ ] **Step 3: Implement structure helpers and port core StructureHandler**

Create `src/dpmoire_lite/structures.py`.

The file must include these tested helpers:

```python
from __future__ import annotations

import copy
import re
from pathlib import Path

import numpy as np
from ase import Atoms
from ase.build import make_supercell, sort, stack
from ase.constraints import FixedLine
from ase.io.vasp import read_vasp, write_vasp
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.io.ase import AseAtomsAdaptor


def generate_stackings(n_sectors: tuple[int, int]) -> list[tuple[int, int]]:
    nx, ny = n_sectors
    return [(i, j) for i in range(nx) for j in range(ny)]


def supercell_matrix(sc: tuple[int, int]) -> list[list[int]]:
    return [[sc[0], 0, 0], [0, sc[1], 0], [0, 0, 1]]


def rewrite_contcar_as_poscar(contcar: Path, poscar: Path) -> None:
    atoms = read_vasp(contcar)
    write_vasp(poscar, atoms=atoms, direct=True, sort=False)


def normalize_symbol_label(label: str) -> str:
    match = re.match(r"^([A-Z][a-z]?)", label.strip())
    if match is None:
        return label.strip()
    return match.group(1)
```

Then port `StructureHandler` from `E:\codespace\MLFF\DPmoire-master\DPmoire\preprocess\structure_handler.py` with these required changes:

- Constructor accepts `input_dir: Path`, `work_dir: Path`, `n_sectors: tuple[int, int]`, and `d: float`.
- `_generate_all_stackings` uses `generate_stackings`.
- `_shift_primitive(i, j)` uses `i / nx` and `j / ny`.
- `shift_atoms(i, j, c_constrain=True, sc=(2, 2))` uses `supercell_matrix(sc)`.
- `shift_all` accepts `stackings: list[tuple[int, int]]`.
- `shift_all_primitive` accepts `stackings: list[tuple[int, int]]`.
- `expand_structure_file(infile, outfile, sc)` uses `supercell_matrix(sc)`.
- `find_sym_reduced_stackings` returns `list[tuple[int, int]]` and writes `sym_reduced_stackings.txt`.
- `make_twist_struct` keeps the old twist search behavior by importing from a copied `_find_homo_twist.py` if needed.

If `_find_homo_twist.py` is needed, copy it from:

```text
E:\codespace\MLFF\DPmoire-master\DPmoire\preprocess\_find_homo_twist.py
```

to:

```text
src/dpmoire_lite/_find_homo_twist.py
```

- [ ] **Step 4: Run structure tests**

Run:

```bash
python -m pytest tests/test_structures_inputs.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit structure helpers**

Run:

```bash
git add src/dpmoire_lite/structures.py src/dpmoire_lite/_find_homo_twist.py tests/test_structures_inputs.py
git commit -m "feat: add structure generation helpers"
```

If `_find_homo_twist.py` was not needed because twist helpers were implemented directly in `structures.py`, omit it from `git add`.

---

### Task 7: VASP Input Generation

**Files:**
- Create or replace: `src/dpmoire_lite/inputs.py`
- Modify: `tests/test_structures_inputs.py`
- Create: `example/`

- [ ] **Step 1: Add failing input-generation tests**

Append to `tests/test_structures_inputs.py`:

```python
from dpmoire_lite.inputs import needs_vdw_kernel, replace_incar_values, resolve_potcar_dir


def test_needs_vdw_kernel_detects_nonlocal_vdw():
    assert needs_vdw_kernel("LUSE_VDW = .TRUE.\n") is True
    assert needs_vdw_kernel("GGA = PE\n") is False


def test_replace_incar_values_updates_encut_rcut_and_langevin():
    template = "ENCUT = 400\nML_RCUT1 = 1\nML_RCUT2 = 1\nLANGEVIN_GAMMA = 1\n"
    rendered = replace_incar_values(
        template,
        encut=520.0,
        rcut1=7.1,
        rcut2=7.1,
        elements=["Mo", "S", "I"],
    )
    assert "ENCUT = 520.0" in rendered
    assert "ML_RCUT1 = 7.1" in rendered
    assert "ML_RCUT2 = 7.1" in rendered
    assert "LANGEVIN_GAMMA = 1 1 1" in rendered


def test_resolve_potcar_dir_uses_strict_mapping(tmp_path):
    potcars = tmp_path / "potcars"
    (potcars / "Na_pv").mkdir(parents=True)
    (potcars / "Na_pv" / "POTCAR").write_text(" ENMAX = 200; \n", encoding="utf-8")
    assert resolve_potcar_dir("Na", potcars) == potcars / "Na_pv"
```

- [ ] **Step 2: Run tests and verify they fail because input functions are missing**

Run:

```bash
python -m pytest tests/test_structures_inputs.py -v
```

Expected: FAIL with missing functions from `dpmoire_lite.inputs`.

- [ ] **Step 3: Implement VASP input helpers**

Replace `src/dpmoire_lite/inputs.py` with a full implementation containing:

```python
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from ase import Atoms
from ase.build import make_supercell, sort
from ase.io.vasp import read_vasp, write_vasp

from .structures import supercell_matrix


VASP_POTCAR_LINK = {
    "Li": "Li_sv",
    "Na": "Na_pv",
    "K": "K_sv",
    "Ca": "Ca_sv",
    "Sc": "Sc_sv",
    "Ti": "Ti_sv",
    "V": "V_sv",
    "Cr": "Cr_pv",
    "Mn": "Mn_pv",
    "Ga": "Ga_d",
    "Ge": "Ge_d",
    "Rb": "Rb_sv",
    "Sr": "Sr_sv",
    "Y": "Y_sv",
    "Zr": "Zr_sv",
    "Nb": "Nb_sv",
    "Tc": "Tc_pv",
    "Ru": "Ru_pv",
    "Rh": "Rh_pv",
    "In": "In_d",
    "Sn": "Sn_d",
    "Cs": "Cs_sv",
    "Ba": "Ba_sv",
    "Pr": "Pr_3",
    "Nd": "Nd_3",
    "Pm": "Pm_3",
    "Sm": "Sm_3",
    "Eu": "Eu_2",
    "Gd": "Gd_3",
    "Tb": "Tb_3",
    "Dy": "Dy_3",
    "Ho": "Ho_3",
    "Er": "Er_3",
    "Tm": "Tm_3",
    "Yb": "Yb_2",
    "Lu": "Lu_3",
    "Hf": "Hf_pv",
    "Ta": "Ta_pv",
    "Tl": "Tl_d",
    "Pb": "Pb_d",
    "Bi": "Bi_d",
    "Po": "Po_d",
    "Fr": "Fr_sv",
    "Ra": "Ra_sv",
}


def needs_vdw_kernel(incar_text: str) -> bool:
    upper = incar_text.upper()
    return "LUSE_VDW" in upper and (".TRUE." in upper or " T" in upper or "=T" in upper)


def resolve_potcar_dir(element: str, potcar_dir: Path) -> Path:
    candidates = []
    if element in VASP_POTCAR_LINK:
        candidates.append(VASP_POTCAR_LINK[element])
    candidates.append(element)
    for candidate in candidates:
        path = potcar_dir / candidate
        if (path / "POTCAR").exists():
            return path
    raise FileNotFoundError(f"No POTCAR found for {element} in {potcar_dir}. Tried: {', '.join(candidates)}")


def read_enmax(potcar_file: Path) -> float:
    for line in potcar_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "ENMAX" in line:
            return float(line.split("=")[1].split(";")[0].strip())
    raise ValueError(f"ENMAX not found in {potcar_file}")


def replace_incar_values(incar_text: str, encut: float, rcut1: float, rcut2: float, elements: list[str]) -> str:
    output = []
    for line in incar_text.splitlines():
        words = line.split()
        if not words:
            continue
        key = words[0].upper()
        if key == "ENCUT":
            output.append(f"ENCUT = {encut}")
        elif key == "ML_RCUT1":
            output.append(f"ML_RCUT1 = {rcut1}")
        elif key == "ML_RCUT2":
            output.append(f"ML_RCUT2 = {rcut2}")
        elif key == "LANGEVIN_GAMMA":
            output.append("LANGEVIN_GAMMA = " + " ".join(["1"] * len(elements)))
        else:
            output.append(line)
    return "\n".join(output) + "\n"
```

Also implement these functions in the same file:

- `get_ordered_elements(atoms: Atoms) -> list[str]`
- `write_potcar(elements, potcar_dir, output_file) -> float` returning max ENMAX
- `write_kpoints(output_dir, lat_vec, k_mesh, k_scale)`
- `render_incar(template_file, output_file, encut, rcut1, rcut2, elements)`
- `copy_vdw_if_needed(template_file, input_dir, output_dir)`
- `copy_submit_script(script_dir, dft_script, output_dir)`
- `copy_example(target_dir)` copying the bundled `example` directory from project root
- `write_supercell_poscar(input_file, output_file, sc)`
- `stage_mlff_files(init_dir, output_dir)` copying `ML_ABN -> ML_AB` and `ML_FFN -> ML_FF`

- [ ] **Step 4: Create example templates**

Create a compact example directory with syntactically valid files:

`example/config.yaml`:

```yaml
dft_script: DFT_script.sh
potcar_dir: ./potcars
script_dir: ./scripts
input_dir: ./input
work_dir: ./work
n_nodes: 1
stage: 0
submit: false
auto_resub: false
vasp_ml: true
outcar_collect_freq: 8
do_relaxation: true
init_mlff: true
sc_rlx: true
n_sectors: [3, 2]
sc: [2, 2]
d: 6.3
k_mesh: 40
encut_factor: 1.6
r_cut: -1
symm_reduce: false
twist_val: false
min_val_n: 4
max_val_n: 5
include_monolayer_md: true
```

Create each INCAR template with at least these lines:

```text
ENCUT = 400
ML_RCUT1 = 6.0
ML_RCUT2 = 6.0
LANGEVIN_GAMMA = 1
```

Create `example/scripts/DFT_script.sh`:

```bash
#!/usr/bin/env bash
echo "Replace this script with the cluster VASP submission command."
```

Create minimal VASP POSCAR files for `top_layer.poscar` and `bot_layer.poscar` using a one-atom square cell so `ase.io.vasp.read_vasp` can parse them.

- [ ] **Step 5: Run input tests**

Run:

```bash
python -m pytest tests/test_structures_inputs.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit input generation and example templates**

Run:

```bash
git add src/dpmoire_lite/inputs.py example tests/test_structures_inputs.py
git commit -m "feat: add VASP input generation helpers"
```

---

### Task 8: Slurm Submission Helpers

**Files:**
- Create: `src/dpmoire_lite/slurm.py`
- Create: `tests/test_build.py`

- [ ] **Step 1: Write Slurm unit tests with a fake runner**

Create initial `tests/test_build.py`:

```python
from dpmoire_lite.slurm import SlurmJob, parse_sbatch_output, parse_sacct_states


def test_parse_sbatch_output_extracts_job_id():
    assert parse_sbatch_output("Submitted batch job 12345\n") == "12345"


def test_parse_sacct_states_maps_states():
    output = """JobID State
------------ ----------
12345 COMPLETED
12346 FAILED
12347 RUNNING
"""
    states = parse_sacct_states(output)
    assert states["12345"] == "COMPLETED"
    assert states["12346"] == "FAILED"
    assert states["12347"] == "RUNNING"


def test_slurm_job_records_path():
    job = SlurmJob(job_id="12345", path="rlx/0_0", status="SUBMITTED")
    assert job.path == "rlx/0_0"
```

- [ ] **Step 2: Run tests and verify they fail because `slurm.py` is missing**

Run:

```bash
python -m pytest tests/test_build.py -v
```

Expected: FAIL with import error.

- [ ] **Step 3: Implement Slurm helpers**

Create `src/dpmoire_lite/slurm.py`:

```python
from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class SlurmJob:
    job_id: str
    path: str
    status: str = "SUBMITTED"

    def as_dict(self):
        return asdict(self)


def parse_sbatch_output(output: str) -> str:
    words = output.strip().split()
    if not words:
        raise ValueError("Empty sbatch output")
    return words[-1]


def parse_sacct_states(output: str) -> dict[str, str]:
    states: dict[str, str] = {}
    for line in output.splitlines():
        words = line.split()
        if len(words) < 2:
            continue
        if words[0].lower() == "jobid" or set(words[0]) == {"-"}:
            continue
        states[words[0]] = words[1]
    return states


class SlurmRunner:
    def __init__(self, script_name: str, n_nodes: int, auto_resub: bool):
        self.script_name = script_name
        self.n_nodes = n_nodes
        self.auto_resub = auto_resub

    def submit(self, work_dir: Path, rel_path: str) -> SlurmJob:
        result = subprocess.run(
            ["sbatch", self.script_name],
            cwd=str(work_dir),
            check=True,
            text=True,
            capture_output=True,
        )
        return SlurmJob(job_id=parse_sbatch_output(result.stdout), path=rel_path)

    def query(self, job_ids: list[str]) -> dict[str, str]:
        if not job_ids:
            return {}
        result = subprocess.run(
            ["sacct", "-j", ",".join(job_ids), "--format", "JobID,State"],
            check=True,
            text=True,
            capture_output=True,
        )
        return parse_sacct_states(result.stdout)

    def wait(self, jobs: list[SlurmJob], poll_seconds: int = 30) -> list[SlurmJob]:
        remaining = {job.job_id: job for job in jobs}
        while remaining:
            states = self.query(list(remaining))
            for job_id, state in states.items():
                if job_id not in remaining:
                    continue
                if state in {"PENDING", "RUNNING"}:
                    continue
                remaining[job_id].status = state
                del remaining[job_id]
            if remaining:
                time.sleep(poll_seconds)
        return jobs
```

- [ ] **Step 4: Run Slurm tests**

Run:

```bash
python -m pytest tests/test_build.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Slurm helpers**

Run:

```bash
git add src/dpmoire_lite/slurm.py tests/test_build.py
git commit -m "feat: add Slurm helpers"
```

---

### Task 9: Build Stage0

**Files:**
- Modify: `src/dpmoire_lite/build.py`
- Modify: `tests/test_build.py`

- [ ] **Step 1: Add failing stage0 build test**

Append to `tests/test_build.py`:

```python
import yaml

from dpmoire_lite.build import run_build


def write_minimal_inputs(root):
    input_dir = root / "input"
    scripts = root / "scripts"
    potcars = root / "potcars"
    input_dir.mkdir()
    scripts.mkdir()
    for element in ["H"]:
        (potcars / element).mkdir(parents=True, exist_ok=True)
        (potcars / element / "POTCAR").write_text(" ENMAX = 100; \n", encoding="utf-8")
    poscar = """H
1.0
  4.0 0.0 0.0
  0.0 4.0 0.0
  0.0 0.0 12.0
H
1
Direct
  0.0 0.0 0.25
"""
    (input_dir / "top_layer.poscar").write_text(poscar, encoding="utf-8")
    (input_dir / "bot_layer.poscar").write_text(poscar, encoding="utf-8")
    for name in ["init_INCAR", "rlx_INCAR", "MD_INCAR", "MD_monolayer_INCAR", "val_INCAR"]:
        (input_dir / name).write_text("ENCUT = 400\nML_RCUT1 = 6\nML_RCUT2 = 6\nLANGEVIN_GAMMA = 1\n", encoding="utf-8")
    (scripts / "DFT_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    return input_dir, scripts, potcars


def write_build_config(root, **overrides):
    input_dir, scripts, potcars = write_minimal_inputs(root)
    data = {
        "dft_script": "DFT_script.sh",
        "potcar_dir": str(potcars),
        "script_dir": str(scripts),
        "input_dir": str(input_dir),
        "work_dir": str(root / "work"),
        "n_nodes": 1,
        "stage": 0,
        "submit": False,
        "auto_resub": False,
        "vasp_ml": True,
        "outcar_collect_freq": 8,
        "do_relaxation": True,
        "init_mlff": True,
        "sc_rlx": True,
        "n_sectors": [2, 1],
        "sc": [1, 1],
        "d": 4.0,
        "k_mesh": 20,
        "encut_factor": 1.5,
        "r_cut": -1,
        "symm_reduce": False,
        "twist_val": False,
        "min_val_n": 4,
        "max_val_n": 5,
        "include_monolayer_md": True,
    }
    data.update(overrides)
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_stage0_generates_init_and_rlx_dirs(tmp_path):
    config = write_build_config(tmp_path)
    run_build(config, wait=False)
    work = tmp_path / "work"
    assert (work / "init_mlff" / "POSCAR").exists()
    assert (work / "rlx" / "0_0" / "INCAR").exists()
    assert (work / "rlx" / "1_0" / "KPOINTS").exists()
    assert (work / "rlx" / "manifest.yaml").exists()
```

- [ ] **Step 2: Run stage0 test and verify it fails**

Run:

```bash
python -m pytest tests/test_build.py::test_stage0_generates_init_and_rlx_dirs -v
```

Expected: FAIL because `run_build` raises `NotImplementedError`.

- [ ] **Step 3: Implement stage0 generation**

Replace `src/dpmoire_lite/build.py` with a full implementation that:

- loads config with `load_config`
- validates `config.validate_build_mode(wait)`
- builds stackings with `generate_stackings` or `StructureHandler.find_sym_reduced_stackings`
- backs up conflicting per-directory targets with `backup_existing_directory`
- creates `init_mlff` first-step folder when `init_mlff` is true
- creates `rlx/<i>_<j>` folders when `do_relaxation` is true
- creates validation folders when `twist_val` is true
- writes per-stage manifests
- copies submit scripts into generated folders
- submits jobs only when `config.submit` is true

Public functions required by later tests:

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import load_config


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def run_build(config_path: Path, wait: bool = False) -> None:
    config = load_config(config_path)
    config.validate_build_mode(wait=wait)
    if config.stage == 0:
        build_stage0(config, wait=wait)
    elif config.stage == 1:
        build_stage1(config, wait=wait)
    elif config.stage == "all":
        build_stage_all(config, wait=wait)
    else:
        raise ValueError(f"Unsupported stage: {config.stage}")
```

Keep `build_stage1` and `build_stage_all` present but raise clear `NotImplementedError` until later tasks:

```python
def build_stage1(config, wait: bool = False) -> None:
    raise NotImplementedError("stage1 is implemented in Task 10")


def build_stage_all(config, wait: bool = False) -> None:
    raise NotImplementedError("stage all is implemented in Task 11")
```

- [ ] **Step 4: Run stage0 build test**

Run:

```bash
python -m pytest tests/test_build.py::test_stage0_generates_init_and_rlx_dirs -v
```

Expected: PASS.

- [ ] **Step 5: Commit stage0 build**

Run:

```bash
git add src/dpmoire_lite/build.py tests/test_build.py
git commit -m "feat: generate stage0 folders"
```

---

### Task 10: Build Stage1 And Strict Checks

**Files:**
- Modify: `src/dpmoire_lite/build.py`
- Modify: `tests/test_build.py`

- [ ] **Step 1: Add failing stage1 test using internal fixture bypass**

Append to `tests/test_build.py`:

```python
from ase import Atoms
from ase.io.vasp import write_vasp


def test_stage1_generates_md_dirs_from_relaxed_structures(tmp_path):
    config = write_build_config(tmp_path, stage=1, sc=[2, 1], n_sectors=[1, 1])
    work = tmp_path / "work"
    rlx = work / "rlx" / "0_0"
    init = work / "init_mlff"
    rlx.mkdir(parents=True)
    init.mkdir(parents=True)
    atoms = Atoms("H2", positions=[[0, 0, 3], [0, 0, 7]], cell=[4, 4, 12], pbc=True)
    write_vasp(rlx / "CONTCAR", atoms=atoms, direct=True)
    (rlx / "OUTCAR").write_text("reached required accuracy - stopping structural energy minimisation\n", encoding="utf-8")
    (init / "ML_ABN").write_text("stub ML_ABN\n", encoding="utf-8")
    (init / "ML_FFN").write_text("stub ML_FFN\n", encoding="utf-8")
    run_build(config, wait=False)
    md = work / "md" / "0_0"
    assert (md / "POSCAR").exists()
    assert (md / "ML_AB").read_text(encoding="utf-8") == "stub ML_ABN\n"
    assert (md / "ML_FF").read_text(encoding="utf-8") == "stub ML_FFN\n"
    assert (work / "md" / "top_layer" / "POSCAR").exists()
    assert (work / "md" / "manifest.yaml").exists()
```

- [ ] **Step 2: Run stage1 test and verify it fails**

Run:

```bash
python -m pytest tests/test_build.py::test_stage1_generates_md_dirs_from_relaxed_structures -v
```

Expected: FAIL because `build_stage1` is not implemented.

- [ ] **Step 3: Implement strict stage1 checks and MD generation**

In `src/dpmoire_lite/build.py`, implement:

- `check_relaxation_converged(directory: Path) -> None`
- `check_stage1_inputs(config, stackings) -> None`
- `build_stage1(config, wait=False) -> None`

Rules:

- `OUTCAR` must contain `reached required accuracy - stopping structural energy minimisation`.
- `CONTCAR` must exist and be readable by ASE.
- `vasp_ml` requires `init_mlff/ML_ABN` and `init_mlff/ML_FFN`.
- For each stacking, write MD POSCAR by calling `rewrite_contcar_as_poscar`.
- If `sc_rlx` is false, expand the rewritten structure to `sc_x x sc_y`.
- Generate `INCAR`, `KPOINTS`, `POTCAR`, submit script, optional vdW kernel.
- If `vasp_ml` is true, copy `ML_ABN -> ML_AB` and `ML_FFN -> ML_FF`.
- If `include_monolayer_md` is true, generate `md/top_layer` and `md/bot_layer`.

- [ ] **Step 4: Run stage1 test**

Run:

```bash
python -m pytest tests/test_build.py::test_stage1_generates_md_dirs_from_relaxed_structures -v
```

Expected: PASS.

- [ ] **Step 5: Add strict failure test**

Append to `tests/test_build.py`:

```python
import pytest


def test_stage1_rejects_unconverged_relaxation(tmp_path):
    config = write_build_config(tmp_path, stage=1, n_sectors=[1, 1])
    work = tmp_path / "work"
    rlx = work / "rlx" / "0_0"
    init = work / "init_mlff"
    rlx.mkdir(parents=True)
    init.mkdir(parents=True)
    atoms = Atoms("H", positions=[[0, 0, 3]], cell=[4, 4, 12], pbc=True)
    write_vasp(rlx / "CONTCAR", atoms=atoms, direct=True)
    (rlx / "OUTCAR").write_text("not converged\n", encoding="utf-8")
    (init / "ML_ABN").write_text("stub ML_ABN\n", encoding="utf-8")
    (init / "ML_FFN").write_text("stub ML_FFN\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not converged"):
        run_build(config, wait=False)
```

- [ ] **Step 6: Run stage1 tests**

Run:

```bash
python -m pytest tests/test_build.py::test_stage1_generates_md_dirs_from_relaxed_structures tests/test_build.py::test_stage1_rejects_unconverged_relaxation -v
```

Expected: PASS.

- [ ] **Step 7: Commit stage1 build**

Run:

```bash
git add src/dpmoire_lite/build.py tests/test_build.py
git commit -m "feat: generate stage1 MD folders"
```

---

### Task 11: Init MLFF Two-Step Wait Workflow And Stage All

**Files:**
- Modify: `src/dpmoire_lite/build.py`
- Modify: `tests/test_build.py`

- [ ] **Step 1: Add tests for stage all validation and init step preparation**

Append to `tests/test_build.py`:

```python
from dpmoire_lite.build import prepare_init_mlff_step2


def test_prepare_init_mlff_step2_renames_ml_files_and_replaces_poscar(tmp_path):
    input_dir, scripts, potcars = write_minimal_inputs(tmp_path)
    init = tmp_path / "work" / "init_mlff"
    init.mkdir(parents=True)
    (init / "ML_ABN").write_text("abn", encoding="utf-8")
    (init / "ML_FFN").write_text("ffn", encoding="utf-8")
    prepare_init_mlff_step2(init_dir=init, input_dir=input_dir, sc=(1, 1))
    assert (init / "ML_AB").read_text(encoding="utf-8") == "abn"
    assert (init / "ML_FF").read_text(encoding="utf-8") == "ffn"
    assert (init / "POSCAR").exists()
```

- [ ] **Step 2: Run test and verify it fails because helper is missing**

Run:

```bash
python -m pytest tests/test_build.py::test_prepare_init_mlff_step2_renames_ml_files_and_replaces_poscar -v
```

Expected: FAIL with import error.

- [ ] **Step 3: Implement init step2 preparation**

Add to `src/dpmoire_lite/build.py`:

```python
from .inputs import write_supercell_poscar


def prepare_init_mlff_step2(init_dir: Path, input_dir: Path, sc: tuple[int, int]) -> None:
    (init_dir / "ML_AB").write_bytes((init_dir / "ML_ABN").read_bytes())
    (init_dir / "ML_FF").write_bytes((init_dir / "ML_FFN").read_bytes())
    write_supercell_poscar(input_dir / "top_layer.poscar", init_dir / "POSCAR", sc=sc)
```

Then update `build_stage_all`:

- It is reachable only after `config.validate_build_mode(wait=True)`.
- Generate and submit stage0.
- Wait for init_mlff step1 and rlx jobs; validation jobs are not blockers.
- If `init_mlff` is enabled, call `prepare_init_mlff_step2`, submit init_mlff step2, wait.
- Generate and submit stage1.

If Slurm runner is hard to exercise in local tests, keep orchestration injectable:

```python
def build_stage_all(config, wait: bool = False, runner_factory=None) -> None:
    ...
```

Default `runner_factory` creates `SlurmRunner`; tests pass a fake runner.

- [ ] **Step 4: Run init workflow test**

Run:

```bash
python -m pytest tests/test_build.py::test_prepare_init_mlff_step2_renames_ml_files_and_replaces_poscar -v
```

Expected: PASS.

- [ ] **Step 5: Commit init MLFF workflow support**

Run:

```bash
git add src/dpmoire_lite/build.py tests/test_build.py
git commit -m "feat: add init MLFF wait workflow"
```

---

### Task 12: Collect Orchestration

**Files:**
- Replace: `src/dpmoire_lite/collect.py`
- Modify: `tests/test_collect.py`

- [ ] **Step 1: Add failing collect orchestration tests**

Append to `tests/test_collect.py`:

```python
import yaml

from dpmoire_lite.collect import run_collect
from dpmoire_lite.manifest import Manifest, read_manifest, write_manifest


def write_collect_config(root, stage="md", vasp_ml=True):
    data = {
        "dft_script": "DFT_script.sh",
        "potcar_dir": str(root / "potcars"),
        "script_dir": str(root / "scripts"),
        "input_dir": str(root / "input"),
        "work_dir": str(root / "work"),
        "n_nodes": 1,
        "stage": stage,
        "submit": False,
        "auto_resub": False,
        "vasp_ml": vasp_ml,
        "outcar_collect_freq": 1,
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
    (root / "input").mkdir()
    (root / "scripts").mkdir()
    (root / "potcars").mkdir()
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_collect_md_ml_abn_writes_extxyz_and_manifest(tmp_path, sample_dir):
    config = write_collect_config(tmp_path, stage="md", vasp_ml=True)
    work = tmp_path / "work"
    md_dir = work / "md" / "0_0"
    md_dir.mkdir(parents=True)
    shutil.copy2(sample_dir / "ML_ABN", md_dir / "ML_ABN")
    write_manifest(
        work,
        Manifest(
            stage="md",
            generated_at="2026-05-26T21:30:00",
            directories=["md/0_0"],
            stackings=[[0, 0]],
        ),
    )
    run_collect(config, stage="md")
    assert (work / "MD_data.extxyz").exists()
    manifest = read_manifest(work, "md")
    assert manifest.collect["frames"] > 0


def test_collect_skips_missing_files_and_records_reason(tmp_path):
    config = write_collect_config(tmp_path, stage="md", vasp_ml=True)
    work = tmp_path / "work"
    (work / "md" / "0_0").mkdir(parents=True)
    write_manifest(
        work,
        Manifest(
            stage="md",
            generated_at="2026-05-26T21:30:00",
            directories=["md/0_0"],
            stackings=[[0, 0]],
        ),
    )
    run_collect(config, stage="md")
    manifest = read_manifest(work, "md")
    assert manifest.skipped[0]["path"] == "md/0_0"
    assert "ML_ABN" in manifest.skipped[0]["reason"]
```

- [ ] **Step 2: Run collect orchestration tests and verify failure**

Run:

```bash
python -m pytest tests/test_collect.py::test_collect_md_ml_abn_writes_extxyz_and_manifest tests/test_collect.py::test_collect_skips_missing_files_and_records_reason -v
```

Expected: FAIL because `run_collect` raises `NotImplementedError`.

- [ ] **Step 3: Implement collect orchestration**

Replace `src/dpmoire_lite/collect.py` with an implementation that:

- loads config
- reads manifest for requested stage, or derives directories from config
- collects `rlx`, `md`, and `validation` separately
- writes `rlx_data.extxyz`, `MD_data.extxyz`, or `valid.extxyz`
- updates manifest collect counts, skipped entries, and failed entries
- uses permissive handling for missing files and parse failures
- uses `find_outcar_series` for every OUTCAR-based source
- uses all validation ionic steps by setting `freq=1`
- uses `outcar_collect_freq` for rlx and non-ML MD
- for ML MD, reads `ML_ABN` and optionally skips a prefix determined by local `ML_AB`

Required public functions:

```python
def run_collect(config_path: Path, stage: str) -> None:
    ...


def collect_rlx(config, manifest) -> tuple[Dataset, Manifest]:
    ...


def collect_md(config, manifest) -> tuple[Dataset, Manifest]:
    ...


def collect_validation(config, manifest) -> tuple[Dataset, Manifest]:
    ...
```

- [ ] **Step 4: Run collect tests**

Run:

```bash
python -m pytest tests/test_collect.py -v
```

Expected: PASS or SKIP for sample-dependent tests.

- [ ] **Step 5: Commit collect orchestration**

Run:

```bash
git add src/dpmoire_lite/collect.py tests/test_collect.py
git commit -m "feat: collect extxyz datasets"
```

---

### Task 13: CLI Integration, init-example, README

**Files:**
- Modify: `src/dpmoire_lite/cli.py`
- Modify: `src/dpmoire_lite/inputs.py`
- Create or replace: `README.md`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add CLI integration tests**

Append to `tests/test_cli.py`:

```python
def test_init_example_copies_template(tmp_path):
    target = tmp_path / "new_case"
    assert main(["init-example", str(target)]) == 0
    assert (target / "config.yaml").exists()
    assert (target / "input" / "rlx_INCAR").exists()
    assert (target / "scripts" / "DFT_script.sh").exists()
```

- [ ] **Step 2: Run CLI tests and verify init-example fails until copy is implemented**

Run:

```bash
python -m pytest tests/test_cli.py -v
```

Expected: FAIL on `test_init_example_copies_template`.

- [ ] **Step 3: Implement `copy_example`**

In `src/dpmoire_lite/inputs.py`, implement `copy_example(target_dir: Path)`:

```python
def copy_example(target_dir: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    source = project_root / "example"
    if not source.exists():
        raise FileNotFoundError(f"Bundled example directory not found: {source}")
    if target_dir.exists():
        raise FileExistsError(f"Target directory already exists: {target_dir}")
    shutil.copytree(source, target_dir)
```

- [ ] **Step 4: Write bilingual README**

Create `README.md` with these sections:

```markdown
# DPmoire-lite

DPmoire-lite generates VASP calculation folders and collects extxyz datasets for moire-style bilayer force-field data construction.

DPmoire-lite 用于生成 VASP 计算目录，并从 VASP 输出中收集 extxyz 数据集，适合手动或半自动地构造二维材料/摩尔体系机器学习力场数据。

## Quick Start

```bash
DPmoireLite init-example my_case
cd my_case
DPmoireLite build config.yaml
DPmoireLite collect config.yaml --stage rlx
```

## Workflow

Stage 0 creates init MLFF, relaxation, and optional validation folders.
Stage 1 checks relaxed structures and creates MD folders.
Validation is independent and collected only when requested.

## 工作流

stage 0 生成 init MLFF、弛豫和可选 validation 目录。
stage 1 严格检查弛豫是否收敛，然后生成 MD 目录。
validation 是独立时间线，只有用户显式运行 collect 时才收集。

## Config Notes

Use snake_case fields only. `n_sectors` and `sc` accept either an integer or `[x, y]`.

## 配置说明

只支持 snake_case 字段。`n_sectors` 和 `sc` 可以写成整数，也可以写成 `[x, y]`。
```

- [ ] **Step 5: Run CLI tests**

Run:

```bash
python -m pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit CLI integration and docs**

Run:

```bash
git add src/dpmoire_lite/cli.py src/dpmoire_lite/inputs.py tests/test_cli.py README.md
git commit -m "feat: finish CLI and example workflow"
```

---

### Task 14: Full Verification And Cleanup

**Files:**
- Modify files only if verification exposes a concrete failure.

- [ ] **Step 1: Run the full test suite**

Run:

```bash
python -m pytest -v
```

Expected: all non-sample tests PASS; sample-dependent tests PASS if `E:\codespace\MLFF\03_constrained_shear_scan` exists, otherwise SKIP.

- [ ] **Step 2: Run CLI help**

Run:

```bash
python -m dpmoire_lite.cli --help
```

Expected: output contains `DPmoireLite`, `build`, `collect`, and `init-example`.

- [ ] **Step 3: Run init-example smoke**

Run:

```bash
python -m dpmoire_lite.cli init-example _smoke_case
```

Expected: `_smoke_case/config.yaml`, `_smoke_case/input`, and `_smoke_case/scripts` exist.

- [ ] **Step 4: Clean smoke output**

Remove only the smoke directory created in Step 3:

```powershell
Remove-Item -LiteralPath '_smoke_case' -Recurse -Force
```

- [ ] **Step 5: Check git status**

Run:

```bash
git status --short
```

Expected: either clean, or only intentional files from fixes made during this task.

- [ ] **Step 6: Commit verification fixes if any were needed**

If Step 1-3 required code changes, run:

```bash
git add <changed files>
git commit -m "fix: complete DPmoireLite verification"
```

If no code changes were needed, do not create an empty commit.

---

## Self-Review Checklist

- Spec coverage:
  - New project, package name, and CLI are covered in Tasks 1 and 13.
  - Snake_case config, rectangular `n_sectors`, and rectangular `sc` are covered in Task 2.
  - Stage layout, backups, and manifests are covered in Task 3.
  - OUTCAR series matching and configurable regex are covered in Task 4.
  - ML_ABN, OUTCAR, and extxyz handling are covered in Tasks 5 and 12.
  - Structure generation, `CONTCAR -> POSCAR`, and velocity stripping are covered in Task 6.
  - INCAR, KPOINTS, POTCAR, vdW kernel, and MLFF file staging are covered in Task 7.
  - Slurm, submit, wait, and job status parsing are covered in Task 8.
  - Stage0, stage1, strict checks, init MLFF, and stage all are covered in Tasks 9-11.
  - README and example template are covered in Task 13.
  - Verification commands are covered in Task 14.
- Placeholder scan: the plan has no placeholder markers or unbounded "add error handling" instructions.
- Type consistency:
  - Config object is `DPmoireLiteConfig`.
  - Entry points are `run_build(config_path: Path, wait: bool)` and `run_collect(config_path: Path, stage: str)`.
  - Manifest object is `Manifest`.
  - Dataset object is `Dataset`.
  - Stage names are `init_mlff`, `rlx`, `md`, and `validation`.
