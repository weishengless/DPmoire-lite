from __future__ import annotations

import copy
import errno
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from ase import Atoms
from ase.io import read as ase_read

from . import atomic_io
from .dataset import Dataset
from .manifest import (
    Manifest,
    ManifestReadResult,
    serialize_manifest,
    validate_current_manifest_text,
)
from .paths import manifest_path


_STAGE_OUTPUTS = {
    "rlx": "rlx_data.extxyz",
    "md": "MD_data.extxyz",
    "validation": "valid.extxyz",
}

_COMPATIBILITY_SCHEMA_VERSION = 1
_COMPATIBILITY_KIND = "dpmoire-lite-collect-result"


class CandidateValidationError(RuntimeError):
    """A candidate could not be proven complete and safe to publish."""


class ResultManifestTargetKind(str, Enum):
    CURRENT_STAGE = "current-stage"
    MD_COMPATIBILITY = "md-compatibility"


@dataclass(frozen=True)
class ResultManifestTarget:
    kind: ResultManifestTargetKind
    path: Path


@dataclass(frozen=True)
class CompatibilityManifestEvidence:
    input_layout: str
    directory_discovery: str
    declared_directories: tuple[str, ...]
    discovered_directories: tuple[str, ...]
    collection_mode: str
    dedup: Mapping[str, Any]


DataWriter = Callable[[Dataset, Path], None]


@dataclass(frozen=True)
class CandidateRequest:
    work_dir: Path
    stage: str
    final_output: Path
    target: ResultManifestTarget
    transaction_id: str
    dataset: Dataset
    expected_frame_count: int
    status: str
    source_diagnostics: tuple[Mapping[str, Any], ...] = ()
    previous_output_sha256: str | None = None
    previous_manifest_sha256: str | None = None
    backup_path: str | None = None
    backup_sha256: str | None = None
    current_manifest: Manifest | ManifestReadResult | None = None
    compatibility_evidence: CompatibilityManifestEvidence | None = None
    data_writer: DataWriter | None = None


@dataclass(frozen=True)
class CandidateArtifacts:
    data_candidate_path: Path
    data_sha256: str
    manifest_candidate_path: Path
    manifest_sha256: str


class BackupError(RuntimeError):
    """Raised when a previous formal output cannot be backed up safely."""


@dataclass(frozen=True)
class BackupArtifacts:
    previous_output_sha256: str | None
    previous_output_frames: int | None
    backup_path: Path | None
    backup_sha256: str | None


def prepare_backup(
    *,
    work_dir: Path,
    stage: str,
    final_output: Path,
    transaction_id: str,
) -> BackupArtifacts:
    """Prepare and publish a durable backup of an existing formal output."""
    work_dir = Path(work_dir).resolve(strict=False)
    final_output = Path(final_output).resolve(strict=False)

    if not isinstance(stage, str) or stage not in _STAGE_OUTPUTS:
        raise BackupError(f"unsupported backup stage: {stage!r}")
    expected_output = work_dir / _STAGE_OUTPUTS[stage]
    if final_output != expected_output:
        raise BackupError(
            f"final output does not match the {stage!r} stage output contract"
        )
    if not isinstance(transaction_id, str) or not transaction_id:
        raise BackupError("transaction_id must be a non-empty string")

    backup_directory = work_dir / "backups" / "collect"
    backup_path = backup_directory / (
        f"{final_output.stem}.{transaction_id}{final_output.suffix}"
    )
    if backup_path.parent.resolve(strict=False) != backup_directory.resolve(
        strict=False
    ):
        raise BackupError("transaction_id must produce a direct backup path")

    if not final_output.exists():
        return BackupArtifacts(
            previous_output_sha256=None,
            previous_output_frames=None,
            backup_path=None,
            backup_sha256=None,
        )
    if not final_output.is_file():
        raise BackupError("formal output is not a regular file")
    if backup_path.exists():
        raise BackupError(f"backup already exists: {backup_path}")

    candidate: Path | None = None
    try:
        previous_output_sha256 = atomic_io.sha256_file(final_output)
        previous_output_frames = len(
            ase_read(final_output, format="extxyz", index=":")
        )

        backup_directory.mkdir(parents=True, exist_ok=True)
        candidate = atomic_io.create_candidate(backup_path)
        candidate.unlink(missing_ok=True)
        try:
            os.link(final_output, candidate)
        except OSError as exc:
            if not _is_hardlink_capability_error(exc):
                raise
            candidate.unlink(missing_ok=True)
            candidate = atomic_io.create_candidate(backup_path)
            with final_output.open("rb") as source, candidate.open("wb") as destination:
                shutil.copyfileobj(source, destination)

        atomic_io.fsync_path(candidate)
        backup_sha256 = atomic_io.sha256_file(candidate)
        if backup_sha256 != previous_output_sha256:
            raise BackupError("backup hash does not match previous output")

        os.replace(candidate, backup_path)
        candidate = None
        atomic_io.fsync_directory(backup_path.parent)
        return BackupArtifacts(
            previous_output_sha256=previous_output_sha256,
            previous_output_frames=previous_output_frames,
            backup_path=backup_path,
            backup_sha256=backup_sha256,
        )
    except BackupError:
        _remove_backup_candidate(candidate)
        raise
    except Exception as exc:
        _remove_backup_candidate(candidate)
        raise BackupError(f"backup preparation failed: {exc}") from exc
    except BaseException:
        _remove_backup_candidate(candidate)
        raise


def _is_hardlink_capability_error(exc: OSError) -> bool:
    supported_errors = {
        errno.EXDEV,
        errno.EACCES,
        errno.EPERM,
        errno.ENOSYS,
    }
    for name in ("ENOTSUP", "EOPNOTSUPP"):
        value = getattr(errno, name, None)
        if value is not None:
            supported_errors.add(value)
    if exc.errno in supported_errors:
        return True
    return getattr(exc, "winerror", None) in {1, 5, 17, 50}


def _remove_backup_candidate(candidate: Path | None) -> None:
    if candidate is not None:
        Path(candidate).unlink(missing_ok=True)


def prepare_candidates(request: CandidateRequest) -> CandidateArtifacts:
    """Write and validate both candidates without changing formal outputs."""
    _validate_request(request)
    data_candidate: Path | None = None
    manifest_candidate: Path | None = None

    try:
        data_candidate = atomic_io.create_candidate(request.final_output)
        writer = request.data_writer or _write_dataset
        writer(request.dataset, data_candidate)
        atomic_io.fsync_path(data_candidate)
        _validate_data_candidate(request, data_candidate)
        data_sha256 = atomic_io.sha256_file(data_candidate)

        manifest_text = _build_manifest_text(request, data_sha256)
        manifest_candidate = atomic_io.create_candidate(request.target.path)
        manifest_candidate.write_text(manifest_text, encoding="utf-8", newline="")
        atomic_io.fsync_path(manifest_candidate)
        reread_text = manifest_candidate.read_text(encoding="utf-8")
        _validate_manifest_candidate(request, reread_text, manifest_candidate, data_sha256)
        manifest_sha256 = atomic_io.sha256_file(manifest_candidate)

        return CandidateArtifacts(
            data_candidate_path=data_candidate,
            data_sha256=data_sha256,
            manifest_candidate_path=manifest_candidate,
            manifest_sha256=manifest_sha256,
        )
    except CandidateValidationError:
        _remove_candidates(data_candidate, manifest_candidate)
        raise
    except Exception as exc:
        _remove_candidates(data_candidate, manifest_candidate)
        raise CandidateValidationError(f"candidate preparation failed: {exc}") from exc
    except BaseException:
        _remove_candidates(data_candidate, manifest_candidate)
        raise


def _validate_request(request: CandidateRequest) -> None:
    work_dir = Path(request.work_dir).resolve()
    final_output = Path(request.final_output).resolve()
    target_path = Path(request.target.path).resolve()

    if request.target.kind == ResultManifestTargetKind.MD_COMPATIBILITY:
        expected_output = work_dir / "MD_data.extxyz"
        expected_target = work_dir / "MD_data.collect.yaml"
        if (
            request.stage != "md"
            or final_output != expected_output
            or target_path != expected_target
            or request.compatibility_evidence is None
        ):
            raise CandidateValidationError(
                "compatibility target requires stage 'md', work_dir/MD_data.extxyz, "
                "work_dir/MD_data.collect.yaml, and compatibility evidence"
            )
    elif request.target.kind == ResultManifestTargetKind.CURRENT_STAGE:
        output_name = _STAGE_OUTPUTS.get(request.stage)
        if output_name is None:
            raise CandidateValidationError(
                f"current result-manifest target does not support stage {request.stage!r}"
            )
        if final_output != work_dir / output_name:
            raise CandidateValidationError(
                f"final output does not match the {request.stage!r} stage contract"
            )
        expected_target = manifest_path(work_dir, request.stage).resolve()
        if target_path != expected_target:
            raise CandidateValidationError(
                "current result-manifest target must be the stage manifest path"
            )
        current = _current_manifest(request.current_manifest)
        if current is None or current.stage != request.stage:
            raise CandidateValidationError(
                "current result-manifest target requires a matching Manifest v2"
            )
    else:
        raise CandidateValidationError(
            f"unsupported result-manifest target kind: {request.target.kind!r}"
        )

    if not isinstance(request.transaction_id, str) or not request.transaction_id:
        raise CandidateValidationError("transaction_id must be a non-empty string")
    if (
        isinstance(request.expected_frame_count, bool)
        or not isinstance(request.expected_frame_count, int)
        or request.expected_frame_count <= 0
    ):
        raise CandidateValidationError("candidate frame count must be a positive integer")
    if len(request.dataset.data) != request.expected_frame_count:
        raise CandidateValidationError(
            "in-memory dataset frame count does not match expected frame count"
        )
    if request.dataset.n_configs != request.expected_frame_count:
        raise CandidateValidationError(
            "Dataset.n_configs does not match expected frame count"
        )
    if (request.backup_path is None) != (request.backup_sha256 is None):
        raise CandidateValidationError("backup path and hash must both be set or both be null")

    Path(request.final_output).parent.mkdir(parents=True, exist_ok=True)
    Path(request.target.path).parent.mkdir(parents=True, exist_ok=True)


def _write_dataset(dataset: Dataset, path: Path) -> None:
    dataset.save_extxyz(path)


def _validate_data_candidate(request: CandidateRequest, path: Path) -> None:
    try:
        frames = ase_read(path, format="extxyz", index=":")
    except Exception as exc:
        raise CandidateValidationError(
            f"could not reread extxyz candidate to EOF: {exc}"
        ) from exc

    if not isinstance(frames, list):
        frames = list(frames)
    if len(frames) != request.expected_frame_count:
        raise CandidateValidationError(
            "extxyz candidate frame count does not match expected frame count: "
            f"{len(frames)} != {request.expected_frame_count}"
        )

    for index, (expected, observed) in enumerate(zip(request.dataset.data, frames)):
        _validate_frame(index, expected, observed)


def _validate_frame(index: int, expected: Atoms, observed: Atoms) -> None:
    if len(observed) != len(expected):
        raise CandidateValidationError(f"candidate frame {index} has the wrong atom count")
    if observed.get_chemical_symbols() != expected.get_chemical_symbols():
        raise CandidateValidationError(f"candidate frame {index} has the wrong composition")
    if not np.allclose(observed.cell.array, expected.cell.array, rtol=0.0, atol=1e-8):
        raise CandidateValidationError(f"candidate frame {index} has a changed cell")
    if not np.allclose(observed.positions, expected.positions, rtol=0.0, atol=1e-8):
        raise CandidateValidationError(f"candidate frame {index} has changed positions")

    results = {} if observed.calc is None else observed.calc.results
    required = ("energy", "forces", "stress")
    missing = [name for name in required if name not in results]
    if missing:
        raise CandidateValidationError(
            f"candidate frame {index} requires energy, forces, and stress; missing {missing}"
        )

    energy = np.asarray(results["energy"])
    forces = np.asarray(results["forces"])
    stress = np.asarray(results["stress"])
    if energy.shape not in {(), (1,)}:
        raise CandidateValidationError(f"candidate frame {index} energy has an invalid shape")
    if forces.shape != (len(observed), 3):
        raise CandidateValidationError(f"candidate frame {index} forces have an invalid shape")
    if stress.shape not in {(6,), (3, 3)}:
        raise CandidateValidationError(f"candidate frame {index} stress has an invalid shape")
    if not (
        np.all(np.isfinite(energy))
        and np.all(np.isfinite(forces))
        and np.all(np.isfinite(stress))
    ):
        raise CandidateValidationError(f"candidate frame {index} contains non-finite results")

    try:
        expected_energy = expected.get_potential_energy(apply_constraint=False)
        expected_forces = expected.get_forces(apply_constraint=False)
        expected_stress = expected.get_stress(apply_constraint=False)
        observed_energy = observed.get_potential_energy(apply_constraint=False)
        observed_forces = observed.get_forces(apply_constraint=False)
        observed_stress = observed.get_stress(apply_constraint=False)
    except Exception as exc:
        raise CandidateValidationError(
            f"candidate frame {index} could not supply energy, forces, and stress: {exc}"
        ) from exc

    if not np.isclose(observed_energy, expected_energy, rtol=0.0, atol=1e-8):
        raise CandidateValidationError(f"candidate frame {index} has changed energy")
    if not np.allclose(observed_forces, expected_forces, rtol=0.0, atol=1e-8):
        raise CandidateValidationError(f"candidate frame {index} has changed forces")
    if not np.allclose(observed_stress, expected_stress, rtol=0.0, atol=1e-8):
        raise CandidateValidationError(f"candidate frame {index} has changed stress")


def _build_manifest_text(request: CandidateRequest, data_sha256: str) -> str:
    collect = _collect_record(request, data_sha256)
    if request.target.kind == ResultManifestTargetKind.CURRENT_STAGE:
        current = _current_manifest(request.current_manifest)
        if current is None:
            raise CandidateValidationError("current Manifest v2 is unavailable")
        candidate = copy.deepcopy(current)
        candidate.collect = collect
        try:
            return serialize_manifest(request.work_dir, candidate)
        except ValueError as exc:
            raise CandidateValidationError(f"invalid current manifest candidate: {exc}") from exc

    evidence = request.compatibility_evidence
    if evidence is None:
        raise CandidateValidationError("compatibility evidence is unavailable")
    data = {
        "schema_version": _COMPATIBILITY_SCHEMA_VERSION,
        "kind": _COMPATIBILITY_KIND,
        "stage": "md",
        "input_layout": evidence.input_layout,
        "directory_discovery": evidence.directory_discovery,
        "declared_directories": list(evidence.declared_directories),
        "discovered_directories": list(evidence.discovered_directories),
        "collection_mode": evidence.collection_mode,
        "dedup": copy.deepcopy(dict(evidence.dedup)),
        "collect": collect,
    }
    return yaml.safe_dump(data, sort_keys=False)


def _collect_record(request: CandidateRequest, data_sha256: str) -> dict[str, Any]:
    return {
        "transaction_id": request.transaction_id,
        "status": request.status,
        "frames": request.expected_frame_count,
        "written": True,
        "output": Path(request.final_output).resolve().relative_to(
            Path(request.work_dir).resolve()
        ).as_posix(),
        "output_sha256": data_sha256,
        "previous_sha256": request.previous_output_sha256,
        "previous_manifest_sha256": request.previous_manifest_sha256,
        "backup": request.backup_path,
        "backup_sha256": request.backup_sha256,
        "sources": [copy.deepcopy(dict(source)) for source in request.source_diagnostics],
    }


def _validate_manifest_candidate(
    request: CandidateRequest,
    text: str,
    path: Path,
    data_sha256: str,
) -> None:
    if request.target.kind == ResultManifestTargetKind.CURRENT_STAGE:
        try:
            candidate = validate_current_manifest_text(
                text,
                work_dir=request.work_dir,
                stage=request.stage,
                path=path,
            )
        except ValueError as exc:
            raise CandidateValidationError(f"invalid current manifest candidate: {exc}") from exc
        collect = candidate.collect
    else:
        collect = _validate_compatibility_manifest_text(request, text, path)

    if collect.get("transaction_id") != request.transaction_id:
        raise CandidateValidationError("manifest candidate transaction_id does not match")
    if collect.get("output_sha256") != data_sha256:
        raise CandidateValidationError("manifest candidate data hash does not match")


def _validate_compatibility_manifest_text(
    request: CandidateRequest, text: str, path: Path
) -> Mapping[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CandidateValidationError(
            f"could not reread compatibility manifest candidate {path}: {exc}"
        ) from exc
    if not isinstance(data, Mapping):
        raise CandidateValidationError(
            "compatibility manifest candidate top-level data must be a mapping"
        )

    required_fields = {
        "schema_version",
        "kind",
        "stage",
        "input_layout",
        "directory_discovery",
        "declared_directories",
        "discovered_directories",
        "collection_mode",
        "dedup",
        "collect",
    }
    if set(data) != required_fields:
        raise CandidateValidationError(
            "compatibility manifest candidate fields do not match schema version 1"
        )
    if (
        data.get("schema_version") != _COMPATIBILITY_SCHEMA_VERSION
        or data.get("kind") != _COMPATIBILITY_KIND
        or data.get("stage") != "md"
    ):
        raise CandidateValidationError(
            "compatibility manifest candidate identity does not match schema version 1"
        )
    for field in ("input_layout", "directory_discovery", "collection_mode"):
        if not isinstance(data.get(field), str) or not data[field]:
            raise CandidateValidationError(
                f"compatibility manifest candidate {field} must be a non-empty string"
            )
    for field in ("declared_directories", "discovered_directories"):
        value = data.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise CandidateValidationError(
                f"compatibility manifest candidate {field} must be a list of strings"
            )
    if not isinstance(data.get("dedup"), Mapping):
        raise CandidateValidationError("compatibility manifest candidate dedup must be a mapping")
    collect = data.get("collect")
    if not isinstance(collect, Mapping):
        raise CandidateValidationError(
            "compatibility manifest candidate collect must be a mapping"
        )
    expected_collect_fields = set(_collect_record(request, collect.get("output_sha256", "")))
    if set(collect) != expected_collect_fields:
        raise CandidateValidationError(
            "compatibility manifest candidate collect fields do not match schema version 1"
        )
    if collect.get("output") != "MD_data.extxyz":
        raise CandidateValidationError(
            "compatibility manifest candidate output must be MD_data.extxyz"
        )
    if collect.get("frames") != request.expected_frame_count or collect.get("written") is not True:
        raise CandidateValidationError(
            "compatibility manifest candidate frame metadata does not match"
        )
    if not isinstance(collect.get("sources"), list) or not all(
        isinstance(source, Mapping) for source in collect["sources"]
    ):
        raise CandidateValidationError(
            "compatibility manifest candidate sources must be a list of mappings"
        )
    return collect


def _current_manifest(
    value: Manifest | ManifestReadResult | None,
) -> Manifest | None:
    if isinstance(value, ManifestReadResult):
        return value.manifest if value.kind == "current" else None
    return value


def _remove_candidates(*paths: Path | None) -> None:
    for path in paths:
        if path is not None:
            Path(path).unlink(missing_ok=True)
