from __future__ import annotations

import copy
import errno
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from ase import Atoms
from ase.io import read as ase_read

from . import atomic_io
from .dataset import Dataset
from .file_lock import CollectFileLock
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

_PUBLISH_JOURNAL_FIELDS = frozenset(
    {
        "transaction_id",
        "state",
        "stage",
        "final_output",
        "data_candidate_path",
        "data_candidate_sha256",
        "result_manifest_target_kind",
        "result_manifest_path",
        "manifest_candidate_path",
        "manifest_candidate_sha256",
        "previous_output_sha256",
        "previous_manifest_sha256",
        "backup_path",
        "backup_sha256",
    }
)


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


@dataclass(frozen=True)
class CollectionAuditEvidence:
    sources_attempted: int
    sources_complete: int
    sources_partial: int
    sources_skipped: int
    sources_failed: int
    previous_sources_attempted: int | None
    previous_sources_complete: int | None
    previous_sources_partial: int | None
    previous_sources_skipped: int | None
    previous_sources_failed: int | None
    expected_directories: tuple[str, ...]
    previous_expected_directories: tuple[str, ...] | None
    source_order: tuple[str, ...]
    collection_mode: str | None
    inventory: Mapping[str, Any]
    dedup: Mapping[str, Any] | None
    coverage_declined: bool

    def __post_init__(self) -> None:
        current_counts = (
            self.sources_attempted,
            self.sources_complete,
            self.sources_partial,
            self.sources_skipped,
            self.sources_failed,
        )
        previous_counts = (
            self.previous_sources_attempted,
            self.previous_sources_complete,
            self.previous_sources_partial,
            self.previous_sources_skipped,
            self.previous_sources_failed,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in current_counts
        ):
            raise TypeError("current source counts must be nonnegative integers")
        if any(value is None for value in previous_counts):
            if not all(value is None for value in previous_counts):
                raise ValueError("previous source counts must be all known or all unknown")
        elif any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in previous_counts
        ):
            raise TypeError("previous source counts must be nonnegative integers")
        if self.sources_attempted != (
            self.sources_complete + self.sources_partial + self.sources_failed
        ):
            raise ValueError(
                "sources_attempted must equal complete plus partial plus failed"
            )

        expected_directories = tuple(self.expected_directories)
        previous_expected_directories = (
            None
            if self.previous_expected_directories is None
            else tuple(self.previous_expected_directories)
        )
        source_order = tuple(self.source_order)
        for label, values in (
            ("expected_directories", expected_directories),
            ("source_order", source_order),
        ):
            if any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f"{label} must contain non-empty strings")
        if previous_expected_directories is not None and any(
            not isinstance(value, str) or not value
            for value in previous_expected_directories
        ):
            raise ValueError(
                "previous_expected_directories must contain non-empty strings"
            )
        source_total = (
            self.sources_complete
            + self.sources_partial
            + self.sources_skipped
            + self.sources_failed
        )
        if len(source_order) != source_total:
            raise ValueError("source_order must align with aggregate source counts")
        if self.collection_mode not in {None, "seed-aware", "full-dedup"}:
            raise ValueError("collection_mode must be seed-aware, full-dedup, or null")
        if not isinstance(self.inventory, Mapping):
            raise TypeError("inventory must be a mapping")
        inventory = copy.deepcopy(dict(self.inventory))
        required_inventory_fields = {
            "manifest_kind",
            "directory_discovery",
            "directories",
            "coverage_known",
            "warnings",
        }
        if set(inventory) != required_inventory_fields:
            raise ValueError("inventory fields do not match the collection audit schema")
        if inventory["manifest_kind"] not in {"current", "legacy", "missing"}:
            raise ValueError("inventory manifest_kind is unsupported")
        if inventory["directory_discovery"] not in {"declared", "legacy-scan"}:
            raise ValueError("inventory directory_discovery is unsupported")
        if inventory["directories"] != list(expected_directories):
            raise ValueError("inventory directories must match expected_directories")
        if not isinstance(inventory["coverage_known"], bool):
            raise TypeError("inventory coverage_known must be a bool")
        if not isinstance(inventory["warnings"], list) or any(
            not isinstance(value, str) or not value
            for value in inventory["warnings"]
        ):
            raise ValueError("inventory warnings must be a list of non-empty strings")
        if self.collection_mode is None:
            if self.dedup is not None:
                raise ValueError("non-MLFF collection audit must not claim dedup evidence")
            dedup = None
        else:
            if not isinstance(self.dedup, Mapping):
                raise TypeError("MLFF collection audit requires dedup evidence")
            dedup = copy.deepcopy(dict(self.dedup))
        if not isinstance(self.coverage_declined, bool):
            raise TypeError("coverage_declined must be a bool")

        object.__setattr__(self, "expected_directories", expected_directories)
        object.__setattr__(
            self,
            "previous_expected_directories",
            previous_expected_directories,
        )
        object.__setattr__(self, "source_order", source_order)
        object.__setattr__(self, "inventory", inventory)
        object.__setattr__(self, "dedup", dedup)


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
    collection_audit: CollectionAuditEvidence | None = None
    previous_output_frames: int | None = None
    previous_output_sha256: str | None = None
    previous_manifest_sha256: str | None = None
    backup_path: str | None = None
    backup_sha256: str | None = None
    current_manifest: Manifest | ManifestReadResult | None = None
    compatibility_evidence: CompatibilityManifestEvidence | None = None
    data_writer: DataWriter | None = None


@dataclass(frozen=True)
class ManifestOnlyRequest:
    work_dir: Path
    stage: str
    final_output: Path
    target: ResultManifestTarget
    transaction_id: str
    status: str
    source_diagnostics: tuple[Mapping[str, Any], ...] = ()
    previous_output_sha256: str | None = None
    previous_manifest_sha256: str | None = None
    current_manifest: Manifest | ManifestReadResult | None = None
    compatibility_evidence: CompatibilityManifestEvidence | None = None


@dataclass(frozen=True)
class CandidateArtifacts:
    data_candidate_path: Path
    data_sha256: str
    manifest_candidate_path: Path
    manifest_sha256: str


class PublicationError(RuntimeError):
    """A collection transaction could not be published or recovered safely."""


@dataclass(frozen=True)
class PublicationResult:
    transaction_id: str
    final_output: Path
    data_sha256: str | None
    result_manifest_target_kind: ResultManifestTargetKind
    result_manifest_path: Path
    manifest_sha256: str
    previous_output_sha256: str | None
    backup_path: Path | None
    backup_sha256: str | None
    previous_output_frames: int | None = None


@dataclass(frozen=True)
class _PendingJournal:
    record: Mapping[str, Any]
    state: str
    transaction_id: str
    final_output: Path
    data_candidate_path: Path
    data_candidate_sha256: str
    result_manifest_path: Path
    manifest_candidate_path: Path
    manifest_candidate_sha256: str
    previous_output_sha256: str | None
    previous_manifest_sha256: str | None
    backup_path: Path | None
    backup_sha256: str | None
    first_publish: bool


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
        data_candidate, data_sha256 = _prepare_data_candidate(request)
        manifest_candidate, manifest_sha256 = _prepare_manifest_candidate(
            request, data_sha256
        )

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


def _prepare_data_candidate(request: CandidateRequest) -> tuple[Path, str]:
    candidate = atomic_io.create_candidate(request.final_output)
    try:
        writer = request.data_writer or _write_dataset
        writer(request.dataset, candidate)
        atomic_io.fsync_path(candidate)
        _validate_data_candidate(request, candidate)
        return candidate, atomic_io.sha256_file(candidate)
    except BaseException:
        candidate.unlink(missing_ok=True)
        raise


def _prepare_manifest_candidate(
    request: CandidateRequest, data_sha256: str
) -> tuple[Path, str]:
    candidate = atomic_io.create_candidate(request.target.path)
    try:
        manifest_text = _build_manifest_text(request, data_sha256)
        candidate.write_text(manifest_text, encoding="utf-8", newline="")
        atomic_io.fsync_path(candidate)
        reread_text = candidate.read_text(encoding="utf-8")
        _validate_manifest_candidate(request, reread_text, candidate, data_sha256)
        return candidate, atomic_io.sha256_file(candidate)
    except BaseException:
        candidate.unlink(missing_ok=True)
        raise


def _prepare_manifest_only_candidate(
    request: ManifestOnlyRequest,
    collect: Mapping[str, Any],
) -> tuple[Path, str]:
    candidate = atomic_io.create_candidate(request.target.path)
    try:
        manifest_text = _build_manifest_text_from_collect(request, collect)
        candidate.write_text(manifest_text, encoding="utf-8", newline="")
        atomic_io.fsync_path(candidate)
        reread_text = candidate.read_text(encoding="utf-8")
        _validate_manifest_only_candidate(request, reread_text, candidate, collect)
        return candidate, atomic_io.sha256_file(candidate)
    except BaseException:
        candidate.unlink(missing_ok=True)
        raise


def _validate_result_request_target(
    request: CandidateRequest | ManifestOnlyRequest,
) -> None:
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

    Path(request.final_output).parent.mkdir(parents=True, exist_ok=True)
    Path(request.target.path).parent.mkdir(parents=True, exist_ok=True)


def _validate_request(request: CandidateRequest) -> None:
    _validate_result_request_target(request)
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
    if request.collection_audit is not None:
        if not isinstance(request.collection_audit, CollectionAuditEvidence):
            raise CandidateValidationError(
                "collection_audit must be CollectionAuditEvidence or null"
            )
        diagnostic_order = tuple(
            source.get("path") for source in request.source_diagnostics
        )
        if diagnostic_order != request.collection_audit.source_order:
            raise CandidateValidationError(
                "source diagnostics must align with collection audit order"
            )
    if request.previous_output_frames is not None and (
        isinstance(request.previous_output_frames, bool)
        or not isinstance(request.previous_output_frames, int)
        or request.previous_output_frames < 0
    ):
        raise CandidateValidationError(
            "previous output frame count must be a nonnegative integer or null"
        )
    if (request.backup_path is None) != (request.backup_sha256 is None):
        raise CandidateValidationError("backup path and hash must both be set or both be null")


def _validate_manifest_only_request(request: ManifestOnlyRequest) -> None:
    _validate_result_request_target(request)
    if request.status != "no_data":
        raise CandidateValidationError(
            "manifest-only publication status must be 'no_data'"
        )


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
    return _build_manifest_text_from_collect(
        request,
        _collect_record(request, data_sha256),
    )


def _build_manifest_text_from_collect(
    request: CandidateRequest | ManifestOnlyRequest,
    collect: Mapping[str, Any],
) -> str:
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
    record = {
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
    }
    audit = request.collection_audit
    if audit is not None:
        previous_directories = audit.previous_expected_directories
        record.update(
            {
                "preserved_previous_output": False,
                "previous_frames": request.previous_output_frames,
                "sources_attempted": audit.sources_attempted,
                "sources_complete": audit.sources_complete,
                "sources_partial": audit.sources_partial,
                "sources_skipped": audit.sources_skipped,
                "sources_failed": audit.sources_failed,
                "previous_sources_attempted": audit.previous_sources_attempted,
                "previous_sources_complete": audit.previous_sources_complete,
                "previous_sources_partial": audit.previous_sources_partial,
                "previous_sources_skipped": audit.previous_sources_skipped,
                "previous_sources_failed": audit.previous_sources_failed,
                "expected_directories": list(audit.expected_directories),
                "expected_directory_count": len(audit.expected_directories),
                "previous_expected_directories": (
                    None
                    if previous_directories is None
                    else list(previous_directories)
                ),
                "previous_expected_directory_count": (
                    None
                    if previous_directories is None
                    else len(previous_directories)
                ),
                "source_order": list(audit.source_order),
                "collection_mode": audit.collection_mode,
                "inventory": copy.deepcopy(dict(audit.inventory)),
                "dedup": (
                    None
                    if audit.dedup is None
                    else copy.deepcopy(dict(audit.dedup))
                ),
                "coverage_declined": audit.coverage_declined,
            }
        )
    record["sources"] = [
        copy.deepcopy(dict(source)) for source in request.source_diagnostics
    ]
    return record


def _manifest_only_collect_record(
    request: ManifestOnlyRequest,
    *,
    previous_output_frames: int | None,
) -> dict[str, Any]:
    return {
        "transaction_id": request.transaction_id,
        "status": request.status,
        "frames": 0,
        "written": False,
        "preserved_previous_output": request.previous_output_sha256 is not None,
        "previous_output_frames": previous_output_frames,
        "output": Path(request.final_output).resolve().relative_to(
            Path(request.work_dir).resolve()
        ).as_posix(),
        "output_sha256": request.previous_output_sha256,
        "previous_sha256": request.previous_output_sha256,
        "previous_manifest_sha256": request.previous_manifest_sha256,
        "backup": None,
        "backup_sha256": None,
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


def _validate_manifest_only_candidate(
    request: ManifestOnlyRequest,
    text: str,
    path: Path,
    expected_collect: Mapping[str, Any],
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
            raise CandidateValidationError(
                f"invalid current manifest-only candidate: {exc}"
            ) from exc
        collect = candidate.collect
    else:
        collect = _validate_compatibility_manifest_text(
            request,
            text,
            path,
            expected_collect=expected_collect,
        )

    if not isinstance(collect, Mapping) or dict(collect) != dict(expected_collect):
        raise CandidateValidationError(
            "manifest-only candidate collect record does not match the request"
        )


def _validate_compatibility_manifest_text(
    request: CandidateRequest | ManifestOnlyRequest,
    text: str,
    path: Path,
    *,
    expected_collect: Mapping[str, Any] | None = None,
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
    if expected_collect is None:
        if not isinstance(request, CandidateRequest):
            raise CandidateValidationError(
                "compatibility manifest candidate lacks an expected collect record"
            )
        expected_collect_fields = set(
            _collect_record(request, collect.get("output_sha256", ""))
        )
    else:
        expected_collect_fields = set(expected_collect)
    if set(collect) != expected_collect_fields:
        raise CandidateValidationError(
            "compatibility manifest candidate collect fields do not match schema version 1"
        )
    if collect.get("output") != "MD_data.extxyz":
        raise CandidateValidationError(
            "compatibility manifest candidate output must be MD_data.extxyz"
        )
    if expected_collect is None and (
        collect.get("frames") != request.expected_frame_count
        or collect.get("written") is not True
    ):
        raise CandidateValidationError(
            "compatibility manifest candidate frame metadata does not match"
        )
    if expected_collect is not None and dict(collect) != dict(expected_collect):
        raise CandidateValidationError(
            "compatibility manifest-only collect record does not match the request"
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


class PublicationSession:
    """Hold one collection lock across recovery, collection, and publication."""

    def __init__(
        self,
        *,
        work_dir: Path,
        stage: str,
        final_output: Path,
        target: ResultManifestTarget,
    ):
        self.work_dir = Path(work_dir).resolve(strict=False)
        self.stage = stage
        self.final_output = Path(final_output).resolve(strict=False)
        self.target = ResultManifestTarget(
            kind=target.kind,
            path=Path(target.path).resolve(strict=False),
        )
        _validate_session_target(self)

        self.journal_path = self.final_output.parent / (
            f".{self.final_output.name}.collect-journal.yaml"
        )
        self.previous_output_sha256: str | None = None
        self.previous_manifest_sha256: str | None = None
        self.recovered_result: PublicationResult | None = None
        self._lock: CollectFileLock | None = None
        self._entered = False
        self._published = False

    def __enter__(self) -> PublicationSession:
        if self._entered or self._lock is not None:
            raise PublicationError("publication session is already open")

        lock = CollectFileLock(self.stage, self.final_output)
        lock.__enter__()
        self._lock = lock
        self._entered = True
        try:
            self.previous_output_sha256 = _optional_file_sha256(self.final_output)
            self.previous_manifest_sha256 = _optional_file_sha256(self.target.path)
            self.recovered_result = _recover_existing_journal(self)
            return self
        except BaseException:
            self._entered = False
            self._lock = None
            lock.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        lock = self._lock
        self._lock = None
        self._entered = False
        if lock is not None:
            lock.__exit__(exc_type, exc_value, traceback)
        return False

    def previous_collect_record(self) -> Mapping[str, Any] | None:
        """Return validated prior collect evidence while this session owns the lock."""
        if not self._entered or self._lock is None:
            raise PublicationError("publication session is not open")
        if self.previous_manifest_sha256 is None:
            return None
        if _optional_file_sha256(self.target.path) != self.previous_manifest_sha256:
            raise PublicationError(
                "result manifest changed before previous coverage was read"
            )
        collect = _recovery_manifest_collect(
            self,
            self.target.path,
            label="previous result manifest",
        )
        if _optional_file_sha256(self.target.path) != self.previous_manifest_sha256:
            raise PublicationError(
                "result manifest changed while previous coverage was read"
            )
        return copy.deepcopy(dict(collect))

    def recovered_collect_record(self) -> Mapping[str, Any]:
        """Return the collect record committed by this session's recovery."""
        if not self._entered or self._lock is None:
            raise PublicationError("publication session is not open")
        recovered = self.recovered_result
        if recovered is None:
            raise PublicationError("publication session did not recover a transaction")
        if recovered.data_sha256 is None:
            raise PublicationError("recovered transaction has no published data hash")
        if _optional_file_sha256(self.target.path) != recovered.manifest_sha256:
            raise PublicationError(
                "recovered result manifest hash does not match the transaction"
            )
        if _optional_file_sha256(self.final_output) != recovered.data_sha256:
            raise PublicationError(
                "recovered formal output hash does not match the transaction"
            )

        collect = _recovery_manifest_collect(
            self,
            self.target.path,
            label="recovered result manifest",
        )
        if collect.get("transaction_id") != recovered.transaction_id:
            raise PublicationError(
                "recovered collect transaction_id does not match the transaction"
            )
        if collect.get("output_sha256") != recovered.data_sha256:
            raise PublicationError(
                "recovered collect output hash does not match the transaction"
            )
        if _optional_file_sha256(self.target.path) != recovered.manifest_sha256:
            raise PublicationError(
                "recovered result manifest changed while evidence was read"
            )
        if _optional_file_sha256(self.final_output) != recovered.data_sha256:
            raise PublicationError(
                "recovered formal output changed while evidence was read"
            )
        return copy.deepcopy(dict(collect))

    def publish(self, request: CandidateRequest) -> PublicationResult:
        if not self._entered or self._lock is None:
            raise PublicationError("publication session is not open")
        if self.recovered_result is not None:
            raise PublicationError(
                "a recovered transaction must be returned before new publication"
            )
        if self._published:
            raise PublicationError("publication session already committed a transaction")

        data_candidate: Path | None = None
        manifest_candidate: Path | None = None
        pending_published = False
        backup: BackupArtifacts | None = None

        try:
            _validate_session_request(self, request)
            _validate_request(request)
            self._lock.update_diagnostics(transaction_id=request.transaction_id)

            data_candidate, data_sha256 = _prepare_data_candidate(request)
            backup = prepare_backup(
                work_dir=self.work_dir,
                stage=self.stage,
                final_output=self.final_output,
                transaction_id=request.transaction_id,
            )
            if backup.previous_output_sha256 != self.previous_output_sha256:
                raise PublicationError(
                    "formal output changed after the publication session read it"
                )
            if (
                _optional_file_sha256(self.target.path)
                != self.previous_manifest_sha256
            ):
                raise PublicationError(
                    "result manifest changed after the publication session read it"
                )

            backup_relative = (
                _relative_journal_path(self.work_dir, backup.backup_path)
                if backup.backup_path is not None
                else None
            )
            publish_request = replace(
                request,
                previous_output_frames=backup.previous_output_frames,
                previous_output_sha256=backup.previous_output_sha256,
                previous_manifest_sha256=self.previous_manifest_sha256,
                backup_path=backup_relative,
                backup_sha256=backup.backup_sha256,
            )
            manifest_candidate, manifest_sha256 = _prepare_manifest_candidate(
                publish_request, data_sha256
            )

            journal = _pending_journal_record(
                session=self,
                request=publish_request,
                data_candidate=data_candidate,
                data_sha256=data_sha256,
                manifest_candidate=manifest_candidate,
                manifest_sha256=manifest_sha256,
            )
            _publish_journal(self.journal_path, journal)
            pending_published = True

            os.replace(data_candidate, self.final_output)
            atomic_io.fsync_directory(self.final_output.parent)
            if atomic_io.sha256_file(self.final_output) != data_sha256:
                raise PublicationError(
                    "published data hash does not match the pending journal"
                )

            os.replace(manifest_candidate, self.target.path)
            atomic_io.fsync_directory(self.target.path.parent)
            if atomic_io.sha256_file(self.target.path) != manifest_sha256:
                raise PublicationError(
                    "published manifest hash does not match the pending journal"
                )
            published_manifest_text = self.target.path.read_text(encoding="utf-8")
            _validate_manifest_candidate(
                publish_request,
                published_manifest_text,
                self.target.path,
                data_sha256,
            )

            committed_journal = dict(journal)
            committed_journal["state"] = "committed"
            _publish_journal(self.journal_path, committed_journal)
            self.journal_path.unlink()
            atomic_io.fsync_directory(self.journal_path.parent)

            result = PublicationResult(
                transaction_id=request.transaction_id,
                final_output=self.final_output,
                data_sha256=data_sha256,
                result_manifest_target_kind=self.target.kind,
                result_manifest_path=self.target.path,
                manifest_sha256=manifest_sha256,
                previous_output_sha256=backup.previous_output_sha256,
                backup_path=backup.backup_path,
                backup_sha256=backup.backup_sha256,
                previous_output_frames=backup.previous_output_frames,
            )
            self._published = True
            return result
        except PublicationError:
            if not pending_published:
                _remove_candidates(data_candidate, manifest_candidate)
            raise
        except (CandidateValidationError, BackupError) as exc:
            if not pending_published:
                _remove_candidates(data_candidate, manifest_candidate)
            raise PublicationError(f"publication failed: {exc}") from exc
        except Exception as exc:
            if not pending_published:
                _remove_candidates(data_candidate, manifest_candidate)
            raise PublicationError(f"publication failed: {exc}") from exc
        except BaseException:
            if not pending_published:
                _remove_candidates(data_candidate, manifest_candidate)
            raise

    def publish_manifest_only(
        self, request: ManifestOnlyRequest
    ) -> PublicationResult:
        if not self._entered or self._lock is None:
            raise PublicationError("publication session is not open")
        if self.recovered_result is not None:
            raise PublicationError(
                "a recovered transaction must be returned before new publication"
            )
        if self._published:
            raise PublicationError("publication session already committed a transaction")

        manifest_candidate: Path | None = None
        try:
            _validate_session_request(self, request)
            _validate_manifest_only_request(request)
            self._lock.update_diagnostics(transaction_id=request.transaction_id)

            if (
                _optional_file_sha256(self.final_output)
                != self.previous_output_sha256
            ):
                raise PublicationError(
                    "formal output changed after the publication session read it"
                )
            if (
                _optional_file_sha256(self.target.path)
                != self.previous_manifest_sha256
            ):
                raise PublicationError(
                    "result manifest changed after the publication session read it"
                )

            previous_output_frames = _safe_output_frame_count(self.final_output)
            collect = _manifest_only_collect_record(
                request,
                previous_output_frames=previous_output_frames,
            )
            manifest_candidate, manifest_sha256 = _prepare_manifest_only_candidate(
                request,
                collect,
            )

            if (
                _optional_file_sha256(self.final_output)
                != self.previous_output_sha256
            ):
                raise PublicationError(
                    "formal output changed during manifest-only publication"
                )
            if (
                _optional_file_sha256(self.target.path)
                != self.previous_manifest_sha256
            ):
                raise PublicationError(
                    "result manifest changed during manifest-only publication"
                )

            os.replace(manifest_candidate, self.target.path)
            manifest_candidate = None
            atomic_io.fsync_directory(self.target.path.parent)

            result = PublicationResult(
                transaction_id=request.transaction_id,
                final_output=self.final_output,
                data_sha256=self.previous_output_sha256,
                result_manifest_target_kind=self.target.kind,
                result_manifest_path=self.target.path,
                manifest_sha256=manifest_sha256,
                previous_output_sha256=self.previous_output_sha256,
                backup_path=None,
                backup_sha256=None,
            )
            self._published = True
            return result
        except PublicationError:
            _remove_candidates(manifest_candidate)
            raise
        except CandidateValidationError as exc:
            _remove_candidates(manifest_candidate)
            raise PublicationError(f"manifest-only publication failed: {exc}") from exc
        except Exception as exc:
            _remove_candidates(manifest_candidate)
            raise PublicationError(f"manifest-only publication failed: {exc}") from exc
        except BaseException:
            _remove_candidates(manifest_candidate)
            raise


def _validate_session_target(session: PublicationSession) -> None:
    output_name = _STAGE_OUTPUTS.get(session.stage)
    if output_name is None:
        raise PublicationError(
            f"publication session does not support stage {session.stage!r}"
        )
    if session.final_output != session.work_dir / output_name:
        raise PublicationError("publication session final output is not stage-owned")

    if session.target.kind == ResultManifestTargetKind.CURRENT_STAGE:
        expected_target = manifest_path(session.work_dir, session.stage).resolve(
            strict=False
        )
        if session.target.path != expected_target:
            raise PublicationError(
                "current publication session target must be the stage manifest"
            )
    elif session.target.kind == ResultManifestTargetKind.MD_COMPATIBILITY:
        if (
            session.stage != "md"
            or session.final_output != session.work_dir / "MD_data.extxyz"
            or session.target.path != session.work_dir / "MD_data.collect.yaml"
        ):
            raise PublicationError(
                "compatibility publication session target is not the closed MD path"
            )
    else:
        raise PublicationError(
            f"unsupported result-manifest target kind: {session.target.kind!r}"
        )


def _validate_session_request(
    session: PublicationSession,
    request: CandidateRequest | ManifestOnlyRequest,
) -> None:
    if Path(request.work_dir).resolve(strict=False) != session.work_dir:
        raise PublicationError("publication request work_dir does not match its session")
    if request.stage != session.stage:
        raise PublicationError("publication request stage does not match its session")
    if Path(request.final_output).resolve(strict=False) != session.final_output:
        raise PublicationError(
            "publication request final output does not match its session"
        )
    if (
        request.target.kind != session.target.kind
        or Path(request.target.path).resolve(strict=False) != session.target.path
    ):
        raise PublicationError("publication request target does not match its session")
    if request.previous_output_sha256 != session.previous_output_sha256:
        raise PublicationError(
            "publication request previous output identity does not match its session"
        )
    if request.previous_manifest_sha256 != session.previous_manifest_sha256:
        raise PublicationError(
            "publication request previous manifest identity does not match its session"
        )
    if isinstance(request, CandidateRequest) and (
        request.previous_output_frames is not None
        or request.backup_path is not None
        or request.backup_sha256 is not None
    ):
        raise PublicationError(
            "publication session, not its caller, owns prior output/backup identity"
        )


def _optional_file_sha256(path: Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise PublicationError(f"transaction artifact is not a regular file: {path}")
    return atomic_io.sha256_file(path)


def _safe_output_frame_count(path: Path) -> int | None:
    path = Path(path)
    if not path.exists() or not path.is_file():
        return None
    try:
        frames = ase_read(path, format="extxyz", index=":")
    except Exception:
        return None
    if isinstance(frames, list):
        return len(frames)
    return len(list(frames))


def _relative_journal_path(work_dir: Path, path: Path) -> str:
    work_dir = Path(work_dir).resolve(strict=False)
    resolved = Path(path).resolve(strict=False)
    try:
        return resolved.relative_to(work_dir).as_posix()
    except ValueError as exc:
        raise PublicationError(
            f"transaction artifact is outside the work directory: {resolved}"
        ) from exc


def _pending_journal_record(
    *,
    session: PublicationSession,
    request: CandidateRequest,
    data_candidate: Path,
    data_sha256: str,
    manifest_candidate: Path,
    manifest_sha256: str,
) -> dict[str, Any]:
    return {
        "transaction_id": request.transaction_id,
        "state": "pending",
        "stage": session.stage,
        "final_output": _relative_journal_path(
            session.work_dir, session.final_output
        ),
        "data_candidate_path": _relative_journal_path(
            session.work_dir, data_candidate
        ),
        "data_candidate_sha256": data_sha256,
        "result_manifest_target_kind": session.target.kind.value,
        "result_manifest_path": _relative_journal_path(
            session.work_dir, session.target.path
        ),
        "manifest_candidate_path": _relative_journal_path(
            session.work_dir, manifest_candidate
        ),
        "manifest_candidate_sha256": manifest_sha256,
        "previous_output_sha256": request.previous_output_sha256,
        "previous_manifest_sha256": request.previous_manifest_sha256,
        "backup_path": request.backup_path,
        "backup_sha256": request.backup_sha256,
    }


def _publish_journal(path: Path, record: Mapping[str, Any]) -> None:
    text = yaml.safe_dump(dict(record), sort_keys=False)
    atomic_io.atomic_text_publish(path, text)


def _journal_sha256(
    value: Any, *, field: str, allow_none: bool = False
) -> str | None:
    if value is None and allow_none:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PublicationError(f"pending journal {field} is not a SHA-256 hash")
    return value


def _journal_artifact_path(
    session: PublicationSession, value: Any, *, field: str
) -> Path:
    if not isinstance(value, str) or not value:
        raise PublicationError(f"pending journal {field} must be a relative path")
    raw = Path(value)
    if raw.is_absolute() or raw.drive or raw.root:
        raise PublicationError(f"pending journal {field} must be a relative path")

    resolved = (session.work_dir / raw).resolve(strict=False)
    try:
        relative = resolved.relative_to(session.work_dir).as_posix()
    except ValueError as exc:
        raise PublicationError(
            f"pending journal {field} is outside the work directory"
        ) from exc
    if relative != value:
        raise PublicationError(f"pending journal {field} is not normalized")
    return resolved


def _validate_journal_candidate_path(
    candidate: Path, destination: Path, *, label: str
) -> None:
    expected_prefix = f".{destination.name}."
    if (
        candidate.parent != destination.parent
        or candidate == destination
        or not candidate.name.startswith(expected_prefix)
        or not candidate.name.endswith(".candidate")
    ):
        raise PublicationError(
            f"pending journal {label} path is not beside its formal destination"
        )


def _load_pending_journal(session: PublicationSession) -> _PendingJournal:
    try:
        text = session.journal_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise PublicationError(f"could not read pending journal: {exc}") from exc
    if not isinstance(data, Mapping):
        raise PublicationError("pending journal top-level data must be a mapping")
    if set(data) != _PUBLISH_JOURNAL_FIELDS:
        raise PublicationError("pending journal fields do not match the publication schema")

    state = data.get("state")
    if state not in {"pending", "committed"}:
        raise PublicationError(f"unsupported collection journal state: {state!r}")

    transaction_id = data.get("transaction_id")
    if not isinstance(transaction_id, str) or not transaction_id:
        raise PublicationError("pending journal transaction_id must be non-empty")
    if data.get("stage") != session.stage:
        raise PublicationError("pending journal stage does not match the publication session")

    final_output = _journal_artifact_path(
        session, data.get("final_output"), field="final output path"
    )
    if final_output != session.final_output:
        raise PublicationError(
            "pending journal final output path does not match the publication session"
        )

    target_kind = data.get("result_manifest_target_kind")
    if target_kind != session.target.kind.value:
        raise PublicationError(
            "pending journal result manifest target kind does not match the publication session"
        )
    result_manifest_path = _journal_artifact_path(
        session,
        data.get("result_manifest_path"),
        field="result manifest target path",
    )
    if result_manifest_path != session.target.path:
        raise PublicationError(
            "pending journal result manifest target path does not match the publication session"
        )

    data_candidate_path = _journal_artifact_path(
        session, data.get("data_candidate_path"), field="data candidate path"
    )
    manifest_candidate_path = _journal_artifact_path(
        session,
        data.get("manifest_candidate_path"),
        field="manifest candidate path",
    )
    _validate_journal_candidate_path(
        data_candidate_path, final_output, label="data candidate"
    )
    _validate_journal_candidate_path(
        manifest_candidate_path,
        result_manifest_path,
        label="manifest candidate",
    )

    data_candidate_sha256 = _journal_sha256(
        data.get("data_candidate_sha256"), field="data candidate hash"
    )
    manifest_candidate_sha256 = _journal_sha256(
        data.get("manifest_candidate_sha256"), field="manifest candidate hash"
    )
    previous_output_sha256 = _journal_sha256(
        data.get("previous_output_sha256"),
        field="previous output hash",
        allow_none=True,
    )
    previous_manifest_sha256 = _journal_sha256(
        data.get("previous_manifest_sha256"),
        field="previous manifest hash",
        allow_none=True,
    )
    backup_sha256 = _journal_sha256(
        data.get("backup_sha256"), field="backup hash", allow_none=True
    )
    backup_value = data.get("backup_path")
    backup_path = (
        None
        if backup_value is None
        else _journal_artifact_path(session, backup_value, field="backup path")
    )

    backup_identity = (
        previous_output_sha256,
        backup_path,
        backup_sha256,
    )
    first_publish = all(value is None for value in backup_identity)
    if not first_publish and any(value is None for value in backup_identity):
        raise PublicationError("pending journal backup identity is incomplete")
    if not first_publish:
        expected_backup_directory = session.work_dir / "backups" / "collect"
        expected_backup = expected_backup_directory / (
            f"{session.final_output.stem}.{transaction_id}{session.final_output.suffix}"
        )
        if (
            expected_backup.parent != expected_backup_directory
            or backup_path != expected_backup
        ):
            raise PublicationError(
                "pending journal backup path does not match its transaction"
            )

    return _PendingJournal(
        record=dict(data),
        state=state,
        transaction_id=transaction_id,
        final_output=final_output,
        data_candidate_path=data_candidate_path,
        data_candidate_sha256=data_candidate_sha256,
        result_manifest_path=result_manifest_path,
        manifest_candidate_path=manifest_candidate_path,
        manifest_candidate_sha256=manifest_candidate_sha256,
        previous_output_sha256=previous_output_sha256,
        previous_manifest_sha256=previous_manifest_sha256,
        backup_path=backup_path,
        backup_sha256=backup_sha256,
        first_publish=first_publish,
    )


def _recovery_artifact_sha256(path: Path, *, label: str) -> str | None:
    try:
        return _optional_file_sha256(path)
    except (OSError, PublicationError) as exc:
        raise PublicationError(f"could not verify recovery {label}: {exc}") from exc


def _validate_pending_backup(journal: _PendingJournal) -> None:
    if journal.first_publish:
        return
    if journal.backup_sha256 != journal.previous_output_sha256:
        raise PublicationError(
            "pending transaction backup hash does not match previous output"
        )
    assert journal.backup_path is not None
    observed = _recovery_artifact_sha256(journal.backup_path, label="backup")
    if observed != journal.previous_output_sha256:
        raise PublicationError(
            "pending transaction backup is missing or its bytes have changed"
        )


def _classify_pending_data(
    session: PublicationSession, journal: _PendingJournal
) -> str:
    observed = session.previous_output_sha256
    previous = journal.previous_output_sha256
    candidate = journal.data_candidate_sha256
    if observed == previous:
        if previous == candidate:
            candidate_observed = _recovery_artifact_sha256(
                journal.data_candidate_path, label="data candidate"
            )
            if candidate_observed is None:
                return "candidate"
            if candidate_observed != candidate:
                raise PublicationError(
                    "pending transaction data candidate hash does not match the journal"
                )
        return "previous"
    if observed == candidate:
        return "candidate"
    return "external"


def _classify_pending_manifest(
    session: PublicationSession, journal: _PendingJournal
) -> str:
    observed = session.previous_manifest_sha256
    previous = journal.previous_manifest_sha256
    candidate = journal.manifest_candidate_sha256
    if observed == previous and observed == candidate:
        raise PublicationError(
            "pending transaction manifest state is ambiguous between previous and candidate"
        )
    if observed == previous:
        return "previous"
    if observed == candidate:
        return "candidate"
    return "external"


def _require_recovery_candidate(
    path: Path, expected_sha256: str, *, label: str
) -> None:
    observed = _recovery_artifact_sha256(path, label=label)
    if observed != expected_sha256:
        raise PublicationError(
            f"pending transaction {label} is missing or its hash does not match"
        )


def _recovery_manifest_collect(
    session: PublicationSession, path: Path, *, label: str
) -> Mapping[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PublicationError(f"could not read recovery {label}: {exc}") from exc

    if session.target.kind == ResultManifestTargetKind.CURRENT_STAGE:
        try:
            manifest = validate_current_manifest_text(
                text,
                work_dir=session.work_dir,
                stage=session.stage,
                path=path,
            )
        except ValueError as exc:
            raise PublicationError(f"invalid recovery {label}: {exc}") from exc
        collect = manifest.collect
    else:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise PublicationError(f"invalid recovery {label}: {exc}") from exc
        if not isinstance(data, Mapping):
            raise PublicationError(f"recovery {label} data must be a mapping")
        if (
            data.get("schema_version") != _COMPATIBILITY_SCHEMA_VERSION
            or data.get("kind") != _COMPATIBILITY_KIND
            or data.get("stage") != "md"
        ):
            raise PublicationError(
                f"recovery {label} compatibility identity does not match"
            )
        collect = data.get("collect")

    if not isinstance(collect, Mapping):
        raise PublicationError(f"recovery {label} collect data must be a mapping")
    return collect


def _validate_recovery_manifest(
    session: PublicationSession,
    journal: _PendingJournal,
    path: Path,
    *,
    label: str,
) -> None:
    collect = _recovery_manifest_collect(session, path, label=label)
    if collect.get("transaction_id") != journal.transaction_id:
        raise PublicationError(
            f"recovery {label} transaction ID does not match the pending journal"
        )
    if collect.get("output_sha256") != journal.data_candidate_sha256:
        raise PublicationError(
            f"recovery {label} data hash does not match the pending journal"
        )


def _replace_recovery_candidate(
    candidate: Path,
    destination: Path,
    expected_sha256: str,
    *,
    label: str,
) -> None:
    try:
        os.replace(candidate, destination)
        atomic_io.fsync_directory(destination.parent)
        observed = atomic_io.sha256_file(destination)
    except Exception as exc:
        raise PublicationError(f"recovery {label} replacement failed: {exc}") from exc
    if observed != expected_sha256:
        raise PublicationError(
            f"recovery {label} hash does not match after replacement"
        )


def _commit_pending_recovery(
    session: PublicationSession, journal: _PendingJournal
) -> PublicationResult:
    committed = dict(journal.record)
    committed["state"] = "committed"
    try:
        _publish_journal(session.journal_path, committed)
        session.journal_path.unlink()
        atomic_io.fsync_directory(session.journal_path.parent)
    except Exception as exc:
        raise PublicationError(f"could not commit recovered transaction: {exc}") from exc

    return PublicationResult(
        transaction_id=journal.transaction_id,
        final_output=journal.final_output,
        data_sha256=journal.data_candidate_sha256,
        result_manifest_target_kind=session.target.kind,
        result_manifest_path=journal.result_manifest_path,
        manifest_sha256=journal.manifest_candidate_sha256,
        previous_output_sha256=journal.previous_output_sha256,
        backup_path=journal.backup_path,
        backup_sha256=journal.backup_sha256,
    )


def _recover_committed_journal(
    session: PublicationSession,
    journal: _PendingJournal,
) -> None:
    if session.previous_output_sha256 != journal.data_candidate_sha256:
        raise PublicationError(
            "committed transaction data hash does not match the journal"
        )
    if session.previous_manifest_sha256 != journal.manifest_candidate_sha256:
        raise PublicationError(
            "committed transaction manifest hash does not match the journal"
        )

    _validate_recovery_manifest(
        session,
        journal,
        journal.result_manifest_path,
        label="committed manifest",
    )
    try:
        session.journal_path.unlink()
        atomic_io.fsync_directory(session.journal_path.parent)
    except Exception as exc:
        raise PublicationError(
            f"could not remove matching committed collection journal: {exc}"
        ) from exc


def _recover_existing_journal(
    session: PublicationSession,
) -> PublicationResult | None:
    if not session.journal_path.exists():
        return None

    journal = _load_pending_journal(session)
    if journal.state == "committed":
        _recover_committed_journal(session, journal)
        return None

    _validate_pending_backup(journal)
    data_state = _classify_pending_data(session, journal)
    manifest_state = _classify_pending_manifest(session, journal)

    if data_state == "external":
        raise PublicationError(
            "pending recovery found external data or an invalid empty output state"
        )
    if manifest_state == "external":
        raise PublicationError(
            "pending recovery found an external manifest or invalid manifest absence"
        )

    if data_state == "previous" and manifest_state == "previous":
        _require_recovery_candidate(
            journal.data_candidate_path,
            journal.data_candidate_sha256,
            label="data candidate",
        )
        _require_recovery_candidate(
            journal.manifest_candidate_path,
            journal.manifest_candidate_sha256,
            label="manifest candidate",
        )
        _validate_recovery_manifest(
            session,
            journal,
            journal.manifest_candidate_path,
            label="manifest candidate",
        )
        _replace_recovery_candidate(
            journal.data_candidate_path,
            journal.final_output,
            journal.data_candidate_sha256,
            label="data",
        )
        _replace_recovery_candidate(
            journal.manifest_candidate_path,
            journal.result_manifest_path,
            journal.manifest_candidate_sha256,
            label="manifest",
        )
        _validate_recovery_manifest(
            session,
            journal,
            journal.result_manifest_path,
            label="published manifest",
        )
        return _commit_pending_recovery(session, journal)

    if data_state == "candidate" and manifest_state == "previous":
        _require_recovery_candidate(
            journal.manifest_candidate_path,
            journal.manifest_candidate_sha256,
            label="manifest candidate",
        )
        _validate_recovery_manifest(
            session,
            journal,
            journal.manifest_candidate_path,
            label="manifest candidate",
        )
        _replace_recovery_candidate(
            journal.manifest_candidate_path,
            journal.result_manifest_path,
            journal.manifest_candidate_sha256,
            label="manifest",
        )
        _validate_recovery_manifest(
            session,
            journal,
            journal.result_manifest_path,
            label="published manifest",
        )
        return _commit_pending_recovery(session, journal)

    if data_state == "candidate" and manifest_state == "candidate":
        _validate_recovery_manifest(
            session,
            journal,
            journal.result_manifest_path,
            label="published manifest",
        )
        return _commit_pending_recovery(session, journal)

    if data_state == "previous" and manifest_state == "candidate":
        raise PublicationError(
            "pending recovery found an impossible manifest-before-data state"
        )

    raise PublicationError(
        f"pending recovery state is unsupported: data={data_state}, "
        f"manifest={manifest_state}"
    )
