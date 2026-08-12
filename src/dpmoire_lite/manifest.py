from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
import posixpath
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from . import atomic_io
from .init_mlff_contract import (
    INIT_MLFF_LAUNCH_FUNCTION,
    INIT_MLFF_RUN_MARKER_LINE,
    INIT_MLFF_SUBMIT_ADAPTER_SCHEMA,
)
from dpmoire_lite.paths import STAGE_OUTPUTS as _COLLECT_OUTPUTS, manifest_path


MANIFEST_SCHEMA_VERSION = 2
INIT_WORKFLOW_SCHEMA = "dpmoire-lite.init-workflow.v2"
LEGACY_INIT_WORKFLOW_SCHEMA = "dpmoire-lite.init-workflow.v1"

_INIT_WORKFLOW_STATE_PHASES = {
    "planned": ("planned", "planned"),
    "step-1-ready": ("step-1-ready", "planned"),
    "step-1-running": ("step-1-running", "planned"),
    "step-1-failed": ("step-1-failed", "planned"),
    "step-1-complete": ("step-1-complete", "planned"),
    "step-2-ready": ("step-1-complete", "step-2-ready"),
    "step-2-running": ("step-1-complete", "step-2-running"),
    "step-2-failed": ("step-1-complete", "step-2-failed"),
    "complete": ("complete", "complete"),
    "conflict": ("conflict", "conflict"),
}
_INIT_WORKFLOW_STATES = set(_INIT_WORKFLOW_STATE_PHASES)
_INIT_PHASE_ROLES = {"bottom": "step-1", "top": "step-2"}

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


def set_init_workflow_state(manifest: Manifest, state: str) -> None:
    """Apply one valid workflow state and its owned bottom/top phase states."""
    try:
        bottom_state, top_state = _INIT_WORKFLOW_STATE_PHASES[state]
    except KeyError as exc:
        raise ValueError(f"Unknown init workflow state: {state!r}") from exc
    workflow = manifest.init_workflow
    if not workflow:
        raise ValueError("Cannot set init workflow state without workflow evidence")
    phases = workflow.get("phases")
    if not isinstance(phases, dict) or set(phases) != set(_INIT_PHASE_ROLES):
        raise ValueError("Cannot set init workflow state without bottom/top phases")
    workflow["state"] = state
    phases["bottom"]["state"] = bottom_state
    phases["top"]["state"] = top_state


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

    write_manifest_to_directory(
        work_dir,
        manifest_path(work_dir, stage).parent,
        manifest,
    )


def write_manifest_to_directory(
    work_dir: Path,
    output_dir: Path,
    manifest: Manifest | ManifestReadResult,
) -> None:
    """Validate and publish a manifest through an already bounded directory."""

    path = Path(output_dir) / "manifest.yaml"
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
        _validate_init_workflow(
            data["init_workflow"],
            path,
            stage=data["stage"],
            mlff_seed=data["mlff_seed"],
        )
    _require_record_list(data, path, "jobs")
    if data.get("init_workflow"):
        _validate_init_submission_jobs(
            data["jobs"],
            data["init_workflow"],
            path,
        )
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
    mlff_seed: Mapping[str, Any],
) -> None:
    if not workflow:
        return
    if stage != "init_mlff":
        raise ValueError(
            f"Invalid manifest {path}: init_workflow is valid only for stage 'init_mlff'"
        )
    schema = workflow.get("schema")
    if schema == LEGACY_INIT_WORKFLOW_SCHEMA:
        expected_fields = {
            "schema",
            "mode",
            "state",
            "submit_source",
            "phases",
        }
    elif schema == INIT_WORKFLOW_SCHEMA:
        expected_fields = {
            "schema",
            "mode",
            "state",
            "submit_source",
            "submit_adapter",
            "phases",
        }
    else:
        raise ValueError(
            f"Invalid manifest {path}: init_workflow.schema must be "
            f"{LEGACY_INIT_WORKFLOW_SCHEMA!r} or {INIT_WORKFLOW_SCHEMA!r}"
        )
    _require_exact_mapping_fields(
        workflow,
        path,
        "init_workflow",
        expected_fields,
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
    if schema == INIT_WORKFLOW_SCHEMA:
        submit_adapter = workflow["submit_adapter"]
        _expect_mapping(submit_adapter, path, "init_workflow.submit_adapter")
        _require_exact_mapping_fields(
            submit_adapter,
            path,
            "init_workflow.submit_adapter",
            {"schema", "marker", "launch_function", "generated_script"},
        )
        if submit_adapter["schema"] != INIT_MLFF_SUBMIT_ADAPTER_SCHEMA:
            raise ValueError(
                f"Invalid manifest {path}: init_workflow.submit_adapter.schema must be "
                f"{INIT_MLFF_SUBMIT_ADAPTER_SCHEMA!r}"
            )
        if submit_adapter["marker"] != INIT_MLFF_RUN_MARKER_LINE:
            raise ValueError(
                f"Invalid manifest {path}: init_workflow.submit_adapter.marker must be "
                f"{INIT_MLFF_RUN_MARKER_LINE!r}"
            )
        if submit_adapter["launch_function"] != INIT_MLFF_LAUNCH_FUNCTION:
            raise ValueError(
                "Invalid manifest "
                f"{path}: init_workflow.submit_adapter.launch_function must be "
                f"{INIT_MLFF_LAUNCH_FUNCTION!r}"
            )
        generated_script = submit_adapter["generated_script"]
        _expect_mapping(
            generated_script,
            path,
            "init_workflow.submit_adapter.generated_script",
        )
        _validate_file_identity(
            generated_script,
            path,
            "init_workflow.submit_adapter.generated_script",
            include_name=True,
        )
        if generated_script["name"] != submit_source["name"]:
            raise ValueError(
                f"Invalid manifest {path}: generated submit script name must match "
                "the source submit script name"
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

    actual_phase_states = tuple(
        phases[phase_name]["state"] for phase_name in _INIT_PHASE_ROLES
    )
    expected_phase_states = _INIT_WORKFLOW_STATE_PHASES[workflow["state"]]
    if actual_phase_states != expected_phase_states:
        raise ValueError(
            f"Invalid manifest {path}: init_workflow.state {workflow['state']!r} "
            f"requires bottom/top states {expected_phase_states!r}, got "
            f"{actual_phase_states!r}"
        )
    if workflow["state"] == "complete":
        _validate_published_init_seed(mlff_seed, path)
    elif mlff_seed:
        raise ValueError(
            f"Invalid manifest {path}: init_workflow state {workflow['state']!r} "
            "must not publish mlff_seed evidence"
        )


def _validate_published_init_seed(
    evidence: Mapping[str, Any],
    path: Path,
) -> None:
    field_name = "mlff_seed"
    _require_exact_mapping_fields(
        evidence,
        path,
        field_name,
        {
            "source",
            "configurations",
            "digest_schema",
            "seed_prefix_sha256",
            "ml_ab_sha256",
            "ml_ff_sha256",
        },
    )
    if evidence["source"] != "init_mlff/ML_ABN":
        raise ValueError(
            f"Invalid manifest {path}: {field_name}.source must be "
            "'init_mlff/ML_ABN'"
        )
    configurations = evidence["configurations"]
    if (
        isinstance(configurations, bool)
        or not isinstance(configurations, int)
        or configurations <= 0
    ):
        raise ValueError(
            f"Invalid manifest {path}: {field_name}.configurations must be a "
            "positive integer"
        )
    if evidence["digest_schema"] != "mlab-seed-v1":
        raise ValueError(
            f"Invalid manifest {path}: {field_name}.digest_schema must be "
            "'mlab-seed-v1'"
        )
    for digest_field in (
        "seed_prefix_sha256",
        "ml_ab_sha256",
        "ml_ff_sha256",
    ):
        digest = evidence[digest_field]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError(
                f"Invalid manifest {path}: {field_name}.{digest_field} must be a "
                "lowercase SHA-256"
            )


def _validate_init_submission_jobs(
    jobs: list[Mapping[str, Any]],
    workflow: Mapping[str, Any],
    path: Path,
) -> None:
    if workflow.get("schema") != INIT_WORKFLOW_SCHEMA:
        return
    if len(jobs) > 1:
        raise ValueError(
            f"Invalid manifest {path}: jobs must contain at most one init submission record"
        )
    if not jobs:
        return

    record = jobs[0]
    status = record.get("status")
    expected_fields = {
        "SUBMITTING": {"path", "status", "script"},
        "SUBMIT_FAILED": {"path", "status", "script", "failure"},
        "SUBMITTED": {"job_id", "path", "status", "script"},
    }
    if not isinstance(status, str) or status not in expected_fields:
        raise ValueError(
            f"Invalid manifest {path}: jobs[0].status must describe only the "
            "submission request, not scheduler completion"
        )
    _require_exact_mapping_fields(
        record,
        path,
        "jobs[0]",
        expected_fields[status],
    )
    if record["path"] != "init_mlff":
        raise ValueError(
            f"Invalid manifest {path}: jobs[0].path must be 'init_mlff'"
        )

    script = record["script"]
    _expect_mapping(script, path, "jobs[0].script")
    _validate_file_identity(script, path, "jobs[0].script", include_name=True)
    generated_script = workflow["submit_adapter"]["generated_script"]
    if dict(script) != dict(generated_script):
        raise ValueError(
            f"Invalid manifest {path}: jobs[0].script must match the generated submit adapter"
        )

    if status == "SUBMITTED":
        job_id = record["job_id"]
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError(
                f"Invalid manifest {path}: jobs[0].job_id must be a non-empty string"
            )
        return
    if status != "SUBMIT_FAILED":
        return

    failure = record["failure"]
    _expect_mapping(failure, path, "jobs[0].failure")
    fields = set(failure)
    if fields not in (
        {"kind", "exception"},
        {"kind", "exception", "returncode"},
    ):
        raise ValueError(
            f"Invalid manifest {path}: jobs[0].failure fields must be bounded"
        )
    if failure["kind"] != "sbatch-invocation":
        raise ValueError(
            f"Invalid manifest {path}: jobs[0].failure.kind must be 'sbatch-invocation'"
        )
    if not isinstance(failure["exception"], str) or not failure["exception"]:
        raise ValueError(
            f"Invalid manifest {path}: jobs[0].failure.exception must be a non-empty string"
        )
    if "returncode" in failure and (
        isinstance(failure["returncode"], bool)
        or not isinstance(failure["returncode"], int)
    ):
        raise ValueError(
            f"Invalid manifest {path}: jobs[0].failure.returncode must be an integer"
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
