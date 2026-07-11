from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
import posixpath
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from . import atomic_io
from dpmoire_lite.paths import manifest_path


MANIFEST_SCHEMA_VERSION = 2

_COLLECT_OUTPUTS = {
    "rlx": "rlx_data.extxyz",
    "md": "MD_data.extxyz",
    "validation": "valid.extxyz",
}

_MANIFEST_FIELDS = {
    "stage",
    "generated_at",
    "config_summary",
    "directories",
    "backups",
    "jobs",
    "collect",
    "skipped",
    "failed",
    "stackings",
    "angles",
    "schema_version",
    "structure_provenance",
    "grid_shift_anchors",
    "mlff_seed",
    "partial",
}


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
    schema_version: int = MANIFEST_SCHEMA_VERSION
    structure_provenance: dict[str, Any] = field(default_factory=dict)
    grid_shift_anchors: dict[str, Any] = field(default_factory=dict)
    mlff_seed: dict[str, Any] = field(default_factory=dict)
    partial: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ManifestReadResult:
    kind: str
    manifest: Manifest | None = None
    raw_data: dict[str, Any] | None = None

    def __bool__(self) -> bool:
        return self.manifest is not None

    def __getattr__(self, name: str) -> Any:
        manifest = self.manifest
        if manifest is not None:
            return getattr(manifest, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"kind", "manifest", "raw_data"} or "manifest" not in self.__dict__:
            object.__setattr__(self, name, value)
            return
        manifest = self.__dict__["manifest"]
        if manifest is not None and hasattr(manifest, name):
            setattr(manifest, name, value)
            return
        object.__setattr__(self, name, value)


def write_manifest(work_dir: Path, manifest: Manifest | ManifestReadResult) -> None:
    if isinstance(manifest, ManifestReadResult):
        if manifest.manifest is None:
            raise ValueError("Cannot write a manifest read as missing or legacy")
        manifest = manifest.manifest

    path = manifest_path(work_dir, manifest.stage)
    data = asdict(manifest)
    _validate_v2_data(data, path, work_dir=Path(work_dir), expected_stage=manifest.stage)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(data, sort_keys=False)
    atomic_io.atomic_text_publish(path, serialized, encoding="utf-8")


def read_manifest(work_dir: Path, stage: str) -> ManifestReadResult:
    path = manifest_path(work_dir, stage)
    if not path.exists():
        return ManifestReadResult(kind="missing")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not read manifest {path}: {exc}") from exc

    if data is None:
        data = {}
    if not isinstance(data, Mapping):
        raise ValueError(f"Invalid manifest {path}: top-level data must be a mapping")
    data = dict(data)

    if "schema_version" not in data:
        _validate_legacy_data(data, path, work_dir=Path(work_dir), expected_stage=stage)
        return ManifestReadResult(kind="legacy", raw_data=data)

    _validate_v2_data(data, path, work_dir=Path(work_dir), expected_stage=stage)
    try:
        manifest = Manifest(**data)
    except TypeError as exc:
        raise ValueError(f"Invalid Manifest v2 field in {path}: {exc}") from exc
    return ManifestReadResult(kind="current", manifest=manifest, raw_data=data)


def _validate_v2_data(
    data: Mapping[str, Any],
    path: Path,
    *,
    work_dir: Path | None = None,
    expected_stage: str | None = None,
) -> None:
    _expect_mapping(data, path, "top-level data")

    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"Invalid manifest {path}: unsupported schema_version {version!r}; "
            f"expected {MANIFEST_SCHEMA_VERSION}"
        )

    unknown = sorted(set(data) - _MANIFEST_FIELDS)
    if unknown:
        raise ValueError(f"Invalid Manifest v2 field in {path}: unknown top-level field(s) {unknown}")

    _require_str(data, path, "stage")
    if expected_stage is not None and data["stage"] != expected_stage:
        raise ValueError(
            f"Invalid manifest {path}: stage {data['stage']!r} does not match "
            f"manifest location for {expected_stage!r}"
        )
    _require_str(data, path, "generated_at")
    _require_list_of_str(data, path, "directories")
    _require_list_of_str(data, path, "backups")
    _require_list_of_str(data, path, "angles")
    _require_mapping_field(data, path, "config_summary")
    _require_mapping_field(data, path, "collect")
    _require_mapping_field(data, path, "structure_provenance")
    _require_mapping_field(data, path, "grid_shift_anchors")
    _require_mapping_field(data, path, "mlff_seed")
    _require_record_list(data, path, "jobs")
    _require_record_list(data, path, "skipped")
    _require_record_list(data, path, "failed")
    _require_record_list(data, path, "partial")
    _require_stackings(data, path)

    if work_dir is not None:
        _normalize_manifest_paths(data, path, work_dir, data["stage"])


def _validate_legacy_data(
    data: dict[str, Any],
    path: Path,
    *,
    work_dir: Path,
    expected_stage: str,
) -> None:
    if "stage" in data:
        if not isinstance(data["stage"], str):
            raise ValueError(f"Invalid legacy manifest {path}: stage must be a string")
        if data["stage"] != expected_stage:
            raise ValueError(
                f"Invalid legacy manifest {path}: stage {data['stage']!r} does not match "
                f"manifest location for {expected_stage!r}"
            )

    if "directories" in data:
        _require_list_of_str(data, path, "directories")
        data["directories"] = [
            _normalize_relative_path(value, work_dir, path, "directories")
            for value in data["directories"]
        ]
    if "backups" in data:
        _require_list_of_str(data, path, "backups")
        data["backups"] = [
            _normalize_relative_path(value, work_dir, path, "backups")
            for value in data["backups"]
        ]


def _normalize_manifest_paths(
    data: Mapping[str, Any], path: Path, work_dir: Path, stage: str
) -> None:
    data["directories"] = [
        _normalize_relative_path(value, work_dir, path, "directories")
        for value in data["directories"]
    ]
    data["backups"] = [
        _normalize_relative_path(value, work_dir, path, "backups")
        for value in data["backups"]
    ]

    collect = data["collect"]
    output = collect.get("output")
    if output is not None:
        if not isinstance(output, str):
            raise ValueError(f"Invalid manifest {path}: collect.output must be a string")
        normalized_output = _normalize_relative_path(output, work_dir, path, "collect.output")
        expected_output = _COLLECT_OUTPUTS.get(stage)
        if expected_output is None or normalized_output != expected_output:
            raise ValueError(
                f"Invalid manifest {path}: collect.output {normalized_output!r} "
                f"does not match the stage contract for {stage!r}"
            )
        collect["output"] = normalized_output

    backup = collect.get("backup")
    if isinstance(backup, str):
        collect["backup"] = _normalize_relative_path(backup, work_dir, path, "collect.backup")
    elif backup is not None:
        raise ValueError(f"Invalid manifest {path}: collect.backup must be a relative path")


def _normalize_relative_path(value: str, work_dir: Path, manifest: Path, field_name: str) -> str:
    raw = value.replace("\\", "/")
    windows_path = PureWindowsPath(raw)
    posix_path = PurePosixPath(raw)
    if (
        not raw
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in posix_path.parts
    ):
        raise ValueError(
            f"Invalid manifest {manifest}: {field_name} must be a safe relative path; got {value!r}"
        )

    normalized = posixpath.normpath(raw)
    if normalized in {"", "."}:
        raise ValueError(
            f"Invalid manifest {manifest}: {field_name} must not be an empty path"
        )

    resolved_work_dir = Path(work_dir).resolve()
    resolved_path = (resolved_work_dir / normalized).resolve()
    try:
        resolved_path.relative_to(resolved_work_dir)
    except ValueError as exc:
        raise ValueError(
            f"Invalid manifest {manifest}: {field_name} escapes work_dir; got {value!r}"
        ) from exc
    return normalized


def _expect_mapping(data: Any, path: Path, field_name: str) -> None:
    if not isinstance(data, Mapping):
        raise ValueError(f"Invalid manifest {path}: {field_name} must be a mapping")


def _require_str(data: Mapping[str, Any], path: Path, field_name: str) -> None:
    value = data.get(field_name)
    if not isinstance(value, str):
        raise ValueError(f"Invalid manifest {path}: {field_name} must be a string")


def _require_list_of_str(data: Mapping[str, Any], path: Path, field_name: str) -> None:
    value = data.get(field_name)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Invalid manifest {path}: {field_name} must be a list of strings")


def _require_mapping_field(data: Mapping[str, Any], path: Path, field_name: str) -> None:
    value = data.get(field_name)
    if not isinstance(value, Mapping):
        raise ValueError(f"Invalid manifest {path}: {field_name} must be a mapping")


def _require_record_list(data: Mapping[str, Any], path: Path, field_name: str) -> None:
    value = data.get(field_name)
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ValueError(f"Invalid manifest {path}: {field_name} must be a list of mappings")


def _require_stackings(data: Mapping[str, Any], path: Path) -> None:
    value = data.get("stackings")
    if not isinstance(value, list) or any(
        not isinstance(item, list)
        or any(isinstance(index, bool) or not isinstance(index, int) for index in item)
        for item in value
    ):
        raise ValueError(f"Invalid manifest {path}: stackings must be a list of integer lists")
