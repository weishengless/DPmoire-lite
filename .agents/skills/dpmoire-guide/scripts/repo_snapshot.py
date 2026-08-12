"""Print a bounded, read-only DPmoire-lite repository snapshot as JSON."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


KEY_FILES = (
    "AGENTS.md",
    "README.md",
    "workflow.md",
    "pyproject.toml",
    "example/config.yaml",
    "src/dpmoire_lite/AGENTS.md",
    "src/dpmoire_lite/cli.py",
    "src/dpmoire_lite/config.py",
    "src/dpmoire_lite/build_preflight.py",
    "src/dpmoire_lite/build.py",
    "src/dpmoire_lite/manifest.py",
    "src/dpmoire_lite/collect.py",
    "src/dpmoire_lite/collect_publish.py",
    "tests/AGENTS.md",
)


def _git(
    path: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise RuntimeError(detail)
    return result


def _repository_root(path: Path) -> Path:
    result = _git(path, "rev-parse", "--show-toplevel")
    return Path(result.stdout.strip()).resolve()


def _branch(root: Path) -> str:
    result = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else "(detached)"


def build_snapshot(requested_path: Path) -> dict[str, object]:
    root = _repository_root(requested_path.resolve())
    project_file = root / "pyproject.toml"
    project_text = project_file.read_text(encoding="utf-8") if project_file.exists() else ""
    if 'name = "DPmoire-lite"' not in project_text:
        raise RuntimeError(f"not a DPmoire-lite checkout: {root}")

    status = _git(root, "status", "--short").stdout.splitlines()
    return {
        "schema": "dpmoire-guide.repo-snapshot.v1",
        "repository_root": str(root),
        "branch": _branch(root),
        "head": _git(root, "rev-parse", "HEAD").stdout.strip(),
        "python_executable": sys.executable,
        "dirty_paths": status,
        "key_files": {
            relative: (root / relative).exists()
            for relative in KEY_FILES
        },
        "reminders": [
            "Read every AGENTS.md that governs the intended files.",
            "Treat dirty paths as user-owned until proven otherwise.",
            "Use README.md and workflow.md for public behavior; use source/tests for evidence.",
            "This snapshot performs no package workflow operation.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print a bounded, read-only DPmoire-lite repository snapshot as JSON."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Any directory inside the DPmoire-lite checkout (default: current directory).",
    )
    args = parser.parse_args()

    try:
        snapshot = build_snapshot(args.repo)
    except (OSError, RuntimeError) as exc:
        print(f"repo_snapshot: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
