from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dpmoire_lite.paths import manifest_path


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
    path.write_text(yaml.safe_dump(asdict(manifest), sort_keys=False), encoding="utf-8")


def read_manifest(work_dir: Path, stage: str) -> Manifest | None:
    path = manifest_path(work_dir, stage)
    if not path.exists():
        return None

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Manifest(**data)
