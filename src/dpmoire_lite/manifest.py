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
INIT_WORKFLOW_SCHEMA = "dpmoire-lite.init-workflow.v1"

_INIT_WORKFLOW_STATES = {"planned", "step-1-ready", "conflict"}
_INIT_PHASE_ROLES = {"bottom": "step-1", "top": "step-2"}

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
    "init_workflow",
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
    init_workflow: dict[str, Any] = field(default_factory=dict)
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
        stage = manifest.manifest.stage
    else:
        stage = manifest.stage

    path = manifest_path(work_dir, stage)
    serialized = serialize_manifest(work_dir, manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_io.atomic_text_publish(path, serialized, encoding="utf-8")


def serialize_manifest(work_dir: Path, manifest: Manifest | ManifestReadResult) -> str:
    if isinstance(manifest, ManifestReadResult):
        if manifest.manifest is None:
            raise ValueError("Cannot write a manifest read as missing or legacy")
        manifest = manifest.manifest

    path = manifest_path(work_dir, manifest.stage)
    data = asdict(manifest)
    _validate_v2_data(data, path, work_dir=Path(work_dir), expected_stage=manifest.stage)
    return yaml.safe_dump(data, sort_keys=False)


def validate_current_manifest_text(
    text: str,
    *,
    work_dir: Path,
    stage: str,
    path: Path,
) -> Manifest:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Could not read manifest candidate {path}: {exc}") from exc

    if not isinstance(data, Mapping):
        raise ValueError(f"Invalid manifest {path}: top-level data must be a mapping")
    data = dict(data)
    _validate_v2_data(data, Path(path), work_dir=Path(work_dir), expected_stage=stage)
    try:
        return Manifest(**data)
    except TypeError as exc:
        raise ValueError(f"Invalid Manifest v2 field in {path}: {exc}") from exc


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
    if "init_workflow" in data:
        _require_mapping_field(data, path, "init_workflow")
        _validate_init_workflow(data["init_workflow"], path, stage=data["stage"])
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

    _normalize_init_workflow_paths(data, path, work_dir)

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


def _validate_init_workflow(
    workflow: Mapping[str, Any],
    path: Path,
    *,
    stage: str,
) -> None:
    if not workflow:
        return
    if stage != "init_mlff":
        raise ValueError(
            f"Invalid manifest {path}: init_workflow is valid only for stage 'init_mlff'"
        )
    _require_exact_mapping_fields(
        workflow,
        path,
        "init_workflow",
        {"schema", "mode", "state", "submit_source", "phases"},
    )
    if workflow["schema"] != INIT_WORKFLOW_SCHEMA:
        raise ValueError(
            f"Invalid manifest {path}: init_workflow.schema must be "
            f"{INIT_WORKFLOW_SCHEMA!r}"
        )
    if workflow["mode"] != "single-job":
        raise ValueError(
            f"Invalid manifest {path}: init_workflow.mode must be 'single-job'"
        )
    _require_init_state(workflow["state"], path, "init_workflow.state")
    submit_source = workflow["submit_source"]
    _expect_mapping(submit_source, path, "init_workflow.submit_source")
    _validate_file_identity(
        submit_source,
        path,
        "init_workflow.submit_source",
        include_name=True,
    )

    phases = workflow["phases"]
    _expect_mapping(phases, path, "init_workflow.phases")
    if set(phases) != set(_INIT_PHASE_ROLES):
        raise ValueError(
            f"Invalid manifest {path}: init_workflow.phases must contain exactly "
            "bottom and top"
        )
    for phase_name, expected_role in _INIT_PHASE_ROLES.items():
        record = phases[phase_name]
        field_name = f"init_workflow.phases.{phase_name}"
        _expect_mapping(record, path, field_name)
        _require_exact_mapping_fields(
            record,
            path,
            field_name,
            {"role", "state", "directory", "incar_template", "static_inputs"},
        )
        if record["role"] != expected_role:
            raise ValueError(
                f"Invalid manifest {path}: {field_name}.role must be {expected_role!r}"
            )
        _require_init_state(record["state"], path, f"{field_name}.state")
        if not isinstance(record["directory"], str):
            raise ValueError(
                f"Invalid manifest {path}: {field_name}.directory must be a string"
            )
        incar_template = record["incar_template"]
        _expect_mapping(incar_template, path, f"{field_name}.incar_template")
        _validate_file_identity(
            incar_template,
            path,
            f"{field_name}.incar_template",
            include_name=True,
        )
        static_inputs = record["static_inputs"]
        _expect_mapping(static_inputs, path, f"{field_name}.static_inputs")
        required_static = {"POSCAR", "POTCAR", "INCAR", "KPOINTS", submit_source["name"]}
        if not required_static.issubset(static_inputs):
            missing = sorted(required_static - set(static_inputs))
            raise ValueError(
                f"Invalid manifest {path}: {field_name}.static_inputs is missing {missing}"
            )
        for name, identity in static_inputs.items():
            _require_safe_basename(name, path, f"{field_name}.static_inputs key")
            _expect_mapping(identity, path, f"{field_name}.static_inputs.{name}")
            _validate_file_identity(
                identity,
                path,
                f"{field_name}.static_inputs.{name}",
                include_name=False,
            )


def _normalize_init_workflow_paths(
    data: Mapping[str, Any],
    path: Path,
    work_dir: Path,
) -> None:
    workflow = data.get("init_workflow")
    if not workflow:
        return
    expected_directories = []
    for phase_name in _INIT_PHASE_ROLES:
        record = workflow["phases"][phase_name]
        field_name = f"init_workflow.phases.{phase_name}.directory"
        normalized = _normalize_relative_path(
            record["directory"],
            work_dir,
            path,
            field_name,
        )
        expected = f"init_mlff/{phase_name}"
        if normalized != expected:
            raise ValueError(
                f"Invalid manifest {path}: {field_name} must be {expected!r}"
            )
        record["directory"] = normalized
        expected_directories.append(expected)
    if data["directories"] != expected_directories:
        raise ValueError(
            f"Invalid manifest {path}: directories must match init_workflow phase order"
        )


def _require_exact_mapping_fields(
    data: Mapping[str, Any],
    path: Path,
    field_name: str,
    expected: set[str],
) -> None:
    missing = sorted(expected - set(data))
    unknown = sorted(set(data) - expected, key=repr)
    if missing or unknown:
        raise ValueError(
            f"Invalid manifest {path}: {field_name} fields mismatch; "
            f"missing={missing}, unknown={unknown}"
        )


def _require_init_state(value: Any, path: Path, field_name: str) -> None:
    if not isinstance(value, str) or value not in _INIT_WORKFLOW_STATES:
        allowed = ", ".join(sorted(_INIT_WORKFLOW_STATES))
        raise ValueError(
            f"Invalid manifest {path}: {field_name} must be one of: {allowed}"
        )


def _validate_file_identity(
    identity: Mapping[str, Any],
    path: Path,
    field_name: str,
    *,
    include_name: bool,
) -> None:
    expected = {"size", "sha256"}
    if include_name:
        expected.add("name")
    _require_exact_mapping_fields(identity, path, field_name, expected)
    if include_name:
        _require_safe_basename(identity["name"], path, f"{field_name}.name")
    size = identity["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError(
            f"Invalid manifest {path}: {field_name}.size must be a non-negative integer"
        )
    digest = identity["sha256"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError(
            f"Invalid manifest {path}: {field_name}.sha256 must be a lowercase SHA-256"
        )


def _require_safe_basename(value: Any, path: Path, field_name: str) -> None:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError(f"Invalid manifest {path}: {field_name} must be a safe basename")
    normalized = value.replace("\\", "/")
    if "/" in normalized:
        raise ValueError(f"Invalid manifest {path}: {field_name} must be a safe basename")


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
