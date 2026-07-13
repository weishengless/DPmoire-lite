from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
import warnings

from ase.io import ParseError
import yaml

from .config import ConfigError, DPmoireLiteConfig, load_config
from .collect_models import (
    CollectResult,
    CollectStatus,
    CollectionCandidate,
    DedupStats,
    MLFFCollectMode,
    RecoveredCollectionEvidence,
    SourceKind,
    SourceResult,
    SourceDedupStats,
    SourceInventory,
    SourceStatus,
    _validate_relative_source_path,
)
from .file_lock import CollectLockError
from .collect_publish import (
    CandidateRequest,
    CollectionAuditEvidence,
    CompatibilityManifestEvidence,
    ManifestOnlyRequest,
    PublicationError,
    PublicationSession,
    ResultManifestTarget,
    ResultManifestTargetKind,
)
from .dataset import Dataset, atoms_from_mlab_configuration, count_ml_ab_configs
from .manifest import Manifest, ManifestReadResult, read_manifest, write_manifest
from .mlff_collect import (
    FullDedupSourceFoldResult,
    build_mlff_source_inventory,
    fold_exact_configurations,
)
from .mlab import MlabParseError, parse_mlab, seed_prefix_identity
from .outcar import (
    OutcarSelection,
    _find_outcar_tail_evidence,
    find_outcar_series,
    open_outcar_frames,
)
from .paths import manifest_path, relative_to_workdir


COLLECT_OUTPUTS = {
    "rlx": "rlx_data.extxyz",
    "md": "MD_data.extxyz",
    "validation": "valid.extxyz",
}


class CollectionInvariantError(ValueError):
    """Collection inputs cannot be interpreted without unsafe assumptions."""


@dataclass(frozen=True)
class _PreviousCoverage:
    sources_attempted: int | None = None
    sources_complete: int | None = None
    sources_partial: int | None = None
    sources_skipped: int | None = None
    sources_failed: int | None = None
    expected_directories: tuple[str, ...] | None = None
    source_statuses: tuple[tuple[str, SourceStatus], ...] = ()


def select_collect_status(
    candidate: CollectionCandidate | None,
    *,
    coverage_declined: bool = False,
    fatal_diagnostic: str | None = None,
) -> CollectStatus:
    if not isinstance(coverage_declined, bool):
        raise TypeError("coverage_declined must be a bool")
    if fatal_diagnostic is not None:
        if not isinstance(fatal_diagnostic, str) or not fatal_diagnostic.strip():
            raise ValueError("fatal_diagnostic must be a non-empty string or None")
        return CollectStatus.FATAL
    if not isinstance(candidate, CollectionCandidate):
        raise TypeError(
            "candidate must be a CollectionCandidate unless a fatal diagnostic "
            "is supplied"
        )
    if candidate.frame_count == 0:
        return CollectStatus.NO_DATA
    if coverage_declined:
        return CollectStatus.DEGRADED
    if (
        candidate.sources_partial
        or candidate.sources_skipped
        or candidate.sources_failed
    ):
        return CollectStatus.DEGRADED
    if (
        candidate.source_inventory is not None
        and not candidate.source_inventory.coverage_known
    ):
        return CollectStatus.DEGRADED
    return CollectStatus.COMPLETE


def _validated_previous_collect_record(
    session: PublicationSession,
) -> Mapping[str, object] | None:
    previous_collect = session.previous_collect_record()
    if session.target.kind is not ResultManifestTargetKind.MD_COMPATIBILITY:
        return previous_collect
    if previous_collect is None:
        return None
    if (
        "output_sha256" not in previous_collect
        or previous_collect.get("output_sha256")
        != session.previous_output_sha256
    ):
        raise PublicationError(
            "compatibility result output hash does not match the formal output"
        )
    return previous_collect


def _publish_no_data_in_session(
    session: PublicationSession,
    *,
    work_dir: Path,
    stage: str,
    final_output: Path,
    target: ResultManifestTarget,
    manifest: ManifestReadResult,
    candidate: CollectionCandidate,
    compatibility_evidence: CompatibilityManifestEvidence | None,
    transaction_id: str,
) -> CollectResult:
    _validated_previous_collect_record(session)
    source_diagnostics = tuple(
        source_result.as_diagnostic()
        for source_result in candidate.source_results
    )
    request = ManifestOnlyRequest(
        work_dir=work_dir,
        stage=stage,
        final_output=final_output,
        target=target,
        transaction_id=transaction_id,
        status=CollectStatus.NO_DATA.value,
        source_diagnostics=source_diagnostics,
        previous_output_sha256=session.previous_output_sha256,
        previous_manifest_sha256=session.previous_manifest_sha256,
        current_manifest=(
            manifest
            if target.kind is ResultManifestTargetKind.CURRENT_STAGE
            else None
        ),
        compatibility_evidence=compatibility_evidence,
    )
    session.publish_manifest_only(request)
    return CollectResult(
        status=CollectStatus.NO_DATA,
        candidate=candidate,
        publication_committed=True,
    )


def _publish_nonzero_in_session(
    session: PublicationSession,
    *,
    work_dir: Path,
    stage: str,
    final_output: Path,
    target: ResultManifestTarget,
    manifest: ManifestReadResult,
    candidate: CollectionCandidate,
    collection_mode: MLFFCollectMode | None,
    compatibility_evidence: CompatibilityManifestEvidence | None,
    transaction_id: str,
) -> CollectResult:
    dataset = Dataset()
    for frame in candidate.accepted_frames:
        dataset.add_atoms(frame)
    source_diagnostics = tuple(
        source_result.as_diagnostic()
        for source_result in candidate.source_results
    )
    previous = _previous_coverage(_validated_previous_collect_record(session))
    audit, new_noncomplete = _collection_audit_evidence(
        manifest=manifest,
        candidate=candidate,
        collection_mode=collection_mode,
        previous=previous,
    )
    status = select_collect_status(
        candidate,
        coverage_declined=audit.coverage_declined,
    )
    request = CandidateRequest(
        work_dir=work_dir,
        stage=stage,
        final_output=final_output,
        target=target,
        transaction_id=transaction_id,
        dataset=dataset,
        expected_frame_count=candidate.frame_count,
        status=status.value,
        source_diagnostics=source_diagnostics,
        collection_audit=audit,
        previous_output_sha256=session.previous_output_sha256,
        previous_manifest_sha256=session.previous_manifest_sha256,
        current_manifest=(
            manifest
            if target.kind is ResultManifestTargetKind.CURRENT_STAGE
            else None
        ),
        compatibility_evidence=compatibility_evidence,
    )
    publication = session.publish(request)

    result_warnings = list(
        candidate.source_inventory.warnings
        if candidate.source_inventory is not None
        else ()
    )
    frame_declined = (
        publication.previous_output_frames is not None
        and candidate.frame_count < publication.previous_output_frames
    )
    if frame_declined or audit.coverage_declined or new_noncomplete:
        result_warnings.append(
            _coverage_warning(
                work_dir=work_dir,
                candidate=candidate,
                previous=previous,
                previous_frames=publication.previous_output_frames,
                backup_path=publication.backup_path,
            )
        )
    return CollectResult(
        status=status,
        candidate=candidate,
        publication_committed=True,
        coverage_declined=audit.coverage_declined,
        warnings=tuple(result_warnings),
    )


def publish_no_data_candidate(
    *,
    work_dir: Path,
    stage: str,
    manifest: ManifestReadResult,
    candidate: CollectionCandidate,
    collection_mode: MLFFCollectMode | None,
    transaction_id: str,
) -> CollectResult:
    if not isinstance(manifest, ManifestReadResult):
        raise TypeError("manifest must be a ManifestReadResult")
    if not isinstance(candidate, CollectionCandidate):
        raise TypeError("candidate must be a CollectionCandidate")
    if candidate.frame_count != 0:
        raise ValueError("manifest-only publication requires a zero-frame candidate")
    if stage not in COLLECT_OUTPUTS:
        raise ValueError(f"Unknown collect stage: {stage!r}")
    if collection_mode is not None and not isinstance(
        collection_mode,
        MLFFCollectMode,
    ):
        raise TypeError("collection_mode must be an MLFFCollectMode or None")
    if candidate.collection_mode is not None and (
        collection_mode is not candidate.collection_mode
    ):
        raise ValueError("collection_mode does not match the candidate")
    if collection_mode is MLFFCollectMode.FULL_DEDUP and (
        candidate.collection_mode is not MLFFCollectMode.FULL_DEDUP
    ):
        raise ValueError("full-dedup publication requires full-dedup candidate evidence")

    work_dir = Path(work_dir)
    final_output = work_dir / COLLECT_OUTPUTS[stage]
    target, compatibility_evidence = _no_data_result_target(
        work_dir=work_dir,
        stage=stage,
        manifest=manifest,
        candidate=candidate,
        collection_mode=collection_mode,
    )
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        with PublicationSession(
            work_dir=work_dir,
            stage=stage,
            final_output=final_output,
            target=target,
        ) as session:
            if session.recovered_result is not None:
                raise RuntimeError(
                    "recovered publication requires Plan 10 Task 4 orchestration"
                )
            return _publish_no_data_in_session(
                session,
                work_dir=work_dir,
                stage=stage,
                final_output=final_output,
                target=target,
                manifest=manifest,
                candidate=candidate,
                compatibility_evidence=compatibility_evidence,
                transaction_id=transaction_id,
            )
    except PublicationError as exc:
        return CollectResult(
            status=CollectStatus.FATAL,
            candidate=candidate,
            fatal_diagnostic=str(exc),
        )


def publish_nonzero_candidate(
    *,
    work_dir: Path,
    stage: str,
    manifest: ManifestReadResult,
    candidate: CollectionCandidate,
    collection_mode: MLFFCollectMode | None,
    transaction_id: str,
) -> CollectResult:
    if not isinstance(manifest, ManifestReadResult):
        raise TypeError("manifest must be a ManifestReadResult")
    if not isinstance(candidate, CollectionCandidate):
        raise TypeError("candidate must be a CollectionCandidate")
    if candidate.frame_count <= 0:
        raise ValueError("nonzero publication requires a positive-frame candidate")
    if stage not in COLLECT_OUTPUTS:
        raise ValueError(f"Unknown collect stage: {stage!r}")
    if collection_mode is not None and not isinstance(
        collection_mode,
        MLFFCollectMode,
    ):
        raise TypeError("collection_mode must be an MLFFCollectMode or None")
    if candidate.collection_mode is not None and (
        collection_mode is not candidate.collection_mode
    ):
        raise ValueError("collection_mode does not match the candidate")
    if collection_mode is MLFFCollectMode.FULL_DEDUP and (
        candidate.collection_mode is not MLFFCollectMode.FULL_DEDUP
    ):
        raise ValueError("full-dedup publication requires full-dedup candidate evidence")

    work_dir = Path(work_dir)
    final_output = work_dir / COLLECT_OUTPUTS[stage]
    target, compatibility_evidence = _no_data_result_target(
        work_dir=work_dir,
        stage=stage,
        manifest=manifest,
        candidate=candidate,
        collection_mode=collection_mode,
    )
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        with PublicationSession(
            work_dir=work_dir,
            stage=stage,
            final_output=final_output,
            target=target,
        ) as session:
            if session.recovered_result is not None:
                raise RuntimeError(
                    "recovered publication requires Plan 10 Task 4 orchestration"
                )
            return _publish_nonzero_in_session(
                session,
                work_dir=work_dir,
                stage=stage,
                final_output=final_output,
                target=target,
                manifest=manifest,
                candidate=candidate,
                collection_mode=collection_mode,
                compatibility_evidence=compatibility_evidence,
                transaction_id=transaction_id,
            )
    except PublicationError as exc:
        return CollectResult(
            status=CollectStatus.FATAL,
            candidate=candidate,
            fatal_diagnostic=str(exc),
        )


def _previous_coverage(
    collect: Mapping[str, object] | None,
) -> _PreviousCoverage:
    if not collect:
        return _PreviousCoverage()

    source_statuses: tuple[tuple[str, SourceStatus], ...] = ()
    structured_sources_known = False
    raw_sources = collect.get("sources")
    if isinstance(raw_sources, list):
        parsed_sources: list[tuple[str, SourceStatus]] = []
        for raw_source in raw_sources:
            if not isinstance(raw_source, Mapping):
                break
            path = raw_source.get("path")
            raw_status = raw_source.get("status")
            if not isinstance(path, str) or not path:
                break
            try:
                status = SourceStatus(raw_status)
            except (TypeError, ValueError):
                break
            parsed_sources.append((path, status))
        else:
            source_statuses = tuple(parsed_sources)
            structured_sources_known = True

    count_fields = (
        "sources_attempted",
        "sources_complete",
        "sources_partial",
        "sources_skipped",
        "sources_failed",
    )
    raw_counts = tuple(collect.get(field) for field in count_fields)
    if all(_is_nonnegative_int(value) for value in raw_counts):
        counts = raw_counts
    elif structured_sources_known:
        status_counts = Counter(status for _, status in source_statuses)
        counts = (
            status_counts[SourceStatus.COMPLETE]
            + status_counts[SourceStatus.PARTIAL]
            + status_counts[SourceStatus.FAILED],
            status_counts[SourceStatus.COMPLETE],
            status_counts[SourceStatus.PARTIAL],
            status_counts[SourceStatus.SKIPPED],
            status_counts[SourceStatus.FAILED],
        )
    else:
        counts = (None, None, None, None, None)

    expected_directories: tuple[str, ...] | None = None
    raw_directories = collect.get("expected_directories")
    if isinstance(raw_directories, list) and all(
        isinstance(directory, str) and directory
        for directory in raw_directories
    ):
        expected_directory_count = collect.get("expected_directory_count")
        if expected_directory_count is None or (
            _is_nonnegative_int(expected_directory_count)
            and expected_directory_count == len(raw_directories)
        ):
            expected_directories = tuple(raw_directories)

    return _PreviousCoverage(
        sources_attempted=counts[0],
        sources_complete=counts[1],
        sources_partial=counts[2],
        sources_skipped=counts[3],
        sources_failed=counts[4],
        expected_directories=expected_directories,
        source_statuses=source_statuses,
    )


def _is_nonnegative_int(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _collection_audit_evidence(
    *,
    manifest: ManifestReadResult,
    candidate: CollectionCandidate,
    collection_mode: MLFFCollectMode | None,
    previous: _PreviousCoverage,
) -> tuple[CollectionAuditEvidence, bool]:
    current_complete_paths = Counter(
        source_result.source_path
        for source_result in candidate.source_results
        if source_result.status is SourceStatus.COMPLETE
    )
    previous_complete_paths = Counter(
        path
        for path, status in previous.source_statuses
        if status is SourceStatus.COMPLETE
    )
    source_coverage_declined = bool(
        previous_complete_paths - current_complete_paths
    ) or (
        previous.sources_complete is not None
        and candidate.sources_complete < previous.sources_complete
    )
    directory_coverage_declined = (
        previous.expected_directories is not None
        and bool(
            Counter(previous.expected_directories)
            - Counter(candidate.expected_directories)
        )
    )
    coverage_declined = source_coverage_declined or directory_coverage_declined

    current_noncomplete = Counter(
        (source_result.source_path, source_result.status)
        for source_result in candidate.source_results
        if source_result.status
        in {SourceStatus.PARTIAL, SourceStatus.SKIPPED, SourceStatus.FAILED}
    )
    previous_noncomplete = Counter(
        (path, status)
        for path, status in previous.source_statuses
        if status in {SourceStatus.PARTIAL, SourceStatus.SKIPPED, SourceStatus.FAILED}
    )
    previous_source_baseline_known = (
        previous.sources_attempted is not None
        or bool(previous.source_statuses)
    )
    new_noncomplete = previous_source_baseline_known and bool(
        current_noncomplete - previous_noncomplete
    )
    if previous.sources_partial is not None:
        new_noncomplete = new_noncomplete or any(
            (
                candidate.sources_partial > previous.sources_partial,
                candidate.sources_skipped > previous.sources_skipped,
                candidate.sources_failed > previous.sources_failed,
            )
        )

    inventory = _collection_inventory_evidence(
        manifest=manifest,
        candidate=candidate,
    )
    dedup = (
        None
        if collection_mode is None
        else _no_data_dedup_evidence(candidate, collection_mode)
    )
    audit = CollectionAuditEvidence(
        sources_attempted=candidate.sources_attempted,
        sources_complete=candidate.sources_complete,
        sources_partial=candidate.sources_partial,
        sources_skipped=candidate.sources_skipped,
        sources_failed=candidate.sources_failed,
        previous_sources_attempted=previous.sources_attempted,
        previous_sources_complete=previous.sources_complete,
        previous_sources_partial=previous.sources_partial,
        previous_sources_skipped=previous.sources_skipped,
        previous_sources_failed=previous.sources_failed,
        expected_directories=candidate.expected_directories,
        previous_expected_directories=previous.expected_directories,
        source_order=tuple(
            source_result.source_path
            for source_result in candidate.source_results
        ),
        collection_mode=(
            None if collection_mode is None else collection_mode.value
        ),
        inventory=inventory,
        dedup=dedup,
        coverage_declined=coverage_declined,
    )
    return audit, new_noncomplete


def _collection_inventory_evidence(
    *,
    manifest: ManifestReadResult,
    candidate: CollectionCandidate,
) -> dict[str, object]:
    inventory = candidate.source_inventory
    if inventory is not None:
        return {
            "manifest_kind": inventory.manifest_kind,
            "directory_discovery": inventory.directory_discovery,
            "directories": list(inventory.directories),
            "coverage_known": inventory.coverage_known,
            "warnings": list(inventory.warnings),
        }
    return {
        "manifest_kind": manifest.kind,
        "directory_discovery": "declared",
        "directories": list(candidate.expected_directories),
        "coverage_known": True,
        "warnings": [],
    }


def _coverage_warning(
    *,
    work_dir: Path,
    candidate: CollectionCandidate,
    previous: _PreviousCoverage,
    previous_frames: int | None,
    backup_path: Path | None,
) -> str:
    backup = (
        "none"
        if backup_path is None
        else backup_path.resolve(strict=False)
        .relative_to(work_dir.resolve(strict=False))
        .as_posix()
    )
    previous_directories = (
        None
        if previous.expected_directories is None
        else len(previous.expected_directories)
    )
    return (
        "collection coverage warning: "
        f"frames new={candidate.frame_count} old={_display_count(previous_frames)}; "
        f"complete new={candidate.sources_complete} "
        f"old={_display_count(previous.sources_complete)}; "
        f"partial new={candidate.sources_partial} "
        f"old={_display_count(previous.sources_partial)}; "
        f"skipped new={candidate.sources_skipped} "
        f"old={_display_count(previous.sources_skipped)}; "
        f"failed new={candidate.sources_failed} "
        f"old={_display_count(previous.sources_failed)}; "
        f"directories new={candidate.expected_directory_count} "
        f"old={_display_count(previous_directories)}; backup={backup}"
    )


def _display_count(value: int | None) -> str:
    return "unknown" if value is None else str(value)


def _no_data_result_target(
    *,
    work_dir: Path,
    stage: str,
    manifest: ManifestReadResult,
    candidate: CollectionCandidate,
    collection_mode: MLFFCollectMode | None,
) -> tuple[ResultManifestTarget, CompatibilityManifestEvidence | None]:
    if manifest.kind in {"current", "legacy"}:
        declared_directories = _candidate_manifest_directories(
            manifest,
            stage=stage,
        )
        if candidate.expected_directories != declared_directories:
            raise ValueError(
                "candidate expected directories do not match the input manifest"
            )
        _validate_no_data_inventory(
            candidate,
            manifest_kind=manifest.kind,
            directories=declared_directories,
        )
    else:
        declared_directories = ()

    if manifest.kind == "current":
        return (
            ResultManifestTarget(
                kind=ResultManifestTargetKind.CURRENT_STAGE,
                path=manifest_path(work_dir, stage),
            ),
            None,
        )

    if manifest.kind == "legacy":
        if stage != "md":
            raise ValueError("legacy result publication is supported only for MD")
        mode = _require_compatibility_mode(collection_mode)
        return (
            ResultManifestTarget(
                kind=ResultManifestTargetKind.MD_COMPATIBILITY,
                path=work_dir / "MD_data.collect.yaml",
            ),
            CompatibilityManifestEvidence(
                input_layout="legacy-stage-manifest",
                directory_discovery="declared",
                declared_directories=declared_directories,
                discovered_directories=(),
                collection_mode=mode.value,
                dedup=_no_data_dedup_evidence(candidate, mode),
            ),
        )

    if manifest.kind != "missing":
        raise ValueError(f"unsupported manifest result kind: {manifest.kind!r}")
    if manifest.manifest is not None or manifest.raw_data is not None:
        raise ValueError("missing manifest result is internally inconsistent")
    if stage != "md" or collection_mode is not MLFFCollectMode.FULL_DEDUP:
        raise ValueError(
            "missing manifests require explicit MD full-dedup compatibility publication"
        )
    inventory = candidate.source_inventory
    if (
        candidate.collection_mode is not MLFFCollectMode.FULL_DEDUP
        or inventory is None
        or inventory.manifest_kind != "missing"
        or inventory.directory_discovery != "legacy-scan"
        or inventory.coverage_known
        or candidate.dedup_stats is None
    ):
        raise ValueError(
            "missing manifest publication requires coherent legacy-scan inventory"
        )

    return (
        ResultManifestTarget(
            kind=ResultManifestTargetKind.MD_COMPATIBILITY,
            path=work_dir / "MD_data.collect.yaml",
        ),
        CompatibilityManifestEvidence(
            input_layout="missing-stage-manifest",
            directory_discovery="legacy-scan",
            declared_directories=(),
            discovered_directories=inventory.directories,
            collection_mode=collection_mode.value,
            dedup=_no_data_dedup_evidence(candidate, collection_mode),
        ),
    )


def _validate_no_data_inventory(
    candidate: CollectionCandidate,
    *,
    manifest_kind: str,
    directories: tuple[str, ...],
) -> None:
    inventory = candidate.source_inventory
    if inventory is None:
        return
    if (
        inventory.manifest_kind != manifest_kind
        or inventory.directory_discovery != "declared"
        or not inventory.coverage_known
        or inventory.directories != directories
    ):
        raise ValueError("candidate inventory does not match the input manifest")


def _require_compatibility_mode(
    collection_mode: MLFFCollectMode | None,
) -> MLFFCollectMode:
    if not isinstance(collection_mode, MLFFCollectMode):
        raise ValueError("compatibility publication requires an explicit MLFF mode")
    return collection_mode


def _no_data_dedup_evidence(
    candidate: CollectionCandidate,
    collection_mode: MLFFCollectMode,
) -> dict[str, object]:
    if collection_mode is MLFFCollectMode.FULL_DEDUP:
        stats = candidate.dedup_stats
        if stats is None:
            raise ValueError("full-dedup publication requires dedup statistics")
        return {
            "applied": True,
            "schema": stats.schema,
            "seen": stats.seen,
            "unique": stats.unique,
            "duplicates_removed": stats.duplicates_removed,
            "candidate_frame_count": stats.candidate_frame_count,
            "per_source": [
                {
                    "source_path": source_stats.source_path,
                    "seen": source_stats.seen,
                    "retained": source_stats.retained,
                    "duplicates_removed": source_stats.duplicates_removed,
                }
                for source_stats in stats.per_source
            ],
        }

    per_source = [
        {
            "source_path": source_result.source_path,
            "seen": source_result.accepted_count,
            "retained": source_result.accepted_count,
            "duplicates_removed": 0,
        }
        for source_result in candidate.source_results
    ]
    seen = sum(source["seen"] for source in per_source)
    return {
        "applied": False,
        "schema": None,
        "seen": seen,
        "unique": candidate.frame_count,
        "duplicates_removed": 0,
        "candidate_frame_count": candidate.frame_count,
        "per_source": per_source,
    }


def _fatal_collection_result(
    error: object,
    *,
    candidate: CollectionCandidate | None = None,
) -> CollectResult:
    diagnostic = str(error).strip() or type(error).__name__
    return CollectResult(
        status=CollectStatus.FATAL,
        candidate=candidate,
        fatal_diagnostic=diagnostic,
    )


def _orchestration_result_target(
    *,
    config: DPmoireLiteConfig,
    stage: str,
    manifest: ManifestReadResult,
    collection_mode: MLFFCollectMode,
) -> ResultManifestTarget:
    try:
        if collection_mode is MLFFCollectMode.FULL_DEDUP and (
            stage != "md" or not config.vasp_ml
        ):
            raise ValueError("full-dedup collection requires MLFF MD mode")

        if manifest.kind in {"current", "legacy"}:
            _candidate_manifest_directories(manifest, stage=stage)
        elif manifest.kind == "missing":
            if manifest.manifest is not None or manifest.raw_data is not None:
                raise ValueError("missing manifest result is internally inconsistent")
            if stage != "md" or collection_mode is not MLFFCollectMode.FULL_DEDUP:
                raise ValueError(
                    "stage manifest is missing; only explicit MD full-dedup may "
                    "use compatibility discovery"
                )
        else:
            raise ValueError(f"unsupported manifest result kind: {manifest.kind!r}")

        if (
            manifest.kind == "current"
            and stage == "md"
            and config.vasp_ml
            and collection_mode is MLFFCollectMode.SEED_AWARE
        ):
            current_manifest = manifest.manifest
            if current_manifest is None:
                raise ValueError("current manifest result has no Manifest")
            _current_seed_evidence(current_manifest)

        if manifest.kind == "current":
            return ResultManifestTarget(
                kind=ResultManifestTargetKind.CURRENT_STAGE,
                path=manifest_path(config.work_dir, stage),
            )
        if stage != "md":
            raise ValueError("legacy result publication is supported only for MD")
        _require_compatibility_mode(collection_mode)
        return ResultManifestTarget(
            kind=ResultManifestTargetKind.MD_COMPATIBILITY,
            path=Path(config.work_dir) / "MD_data.collect.yaml",
        )
    except ValueError as exc:
        raise CollectionInvariantError(str(exc)) from exc


def _build_orchestration_candidate(
    *,
    config: DPmoireLiteConfig,
    stage: str,
    manifest: ManifestReadResult,
    collection_mode: MLFFCollectMode,
) -> CollectionCandidate:
    try:
        if collection_mode is MLFFCollectMode.FULL_DEDUP:
            return build_full_dedup_candidate(
                work_dir=config.work_dir,
                manifest=manifest,
            )
        return build_seed_aware_candidate(
            config=config,
            stage=stage,
            manifest=manifest,
        )
    except ValueError as exc:
        raise CollectionInvariantError(str(exc)) from exc


def _candidate_publication_target(
    *,
    work_dir: Path,
    stage: str,
    manifest: ManifestReadResult,
    candidate: CollectionCandidate,
    collection_mode: MLFFCollectMode,
    expected_target: ResultManifestTarget,
) -> tuple[ResultManifestTarget, CompatibilityManifestEvidence | None]:
    try:
        target, compatibility_evidence = _no_data_result_target(
            work_dir=work_dir,
            stage=stage,
            manifest=manifest,
            candidate=candidate,
            collection_mode=collection_mode,
        )
    except ValueError as exc:
        raise CollectionInvariantError(str(exc)) from exc

    if (
        target.kind is not expected_target.kind
        or Path(target.path).resolve(strict=False)
        != Path(expected_target.path).resolve(strict=False)
    ):
        raise CollectionInvariantError(
            "candidate publication target changed after the session lock was acquired"
        )
    return target, compatibility_evidence


def _recovered_collection_result(
    session: PublicationSession,
) -> CollectResult:
    recovered = session.recovered_result
    if recovered is None:
        raise CollectionInvariantError("publication session has no recovered transaction")
    collect = session.recovered_collect_record()

    raw_status = collect.get("status")
    try:
        status = CollectStatus(raw_status)
    except (TypeError, ValueError) as exc:
        raise CollectionInvariantError(
            f"recovered collect status is invalid: {raw_status!r}"
        ) from exc
    if status not in {CollectStatus.COMPLETE, CollectStatus.DEGRADED}:
        raise CollectionInvariantError(
            "recovered collect status must be complete or degraded"
        )

    frame_count = collect.get("frames")
    if not _is_nonnegative_int(frame_count) or frame_count == 0:
        raise CollectionInvariantError(
            "recovered collect frames must be a positive integer"
        )
    coverage_declined = collect.get("coverage_declined", False)
    if not isinstance(coverage_declined, bool):
        raise CollectionInvariantError(
            "recovered collect coverage_declined must be a bool"
        )

    previous = _previous_coverage(collect)
    counts = (
        previous.sources_attempted,
        previous.sources_complete,
        previous.sources_partial,
        previous.sources_skipped,
        previous.sources_failed,
    )
    if any(count is None for count in counts):
        raise CollectionInvariantError(
            "recovered collect source summary is incomplete"
        )
    try:
        evidence = RecoveredCollectionEvidence(
            transaction_id=recovered.transaction_id,
            status=status,
            frame_count=frame_count,
            sources_attempted=int(previous.sources_attempted),
            sources_complete=int(previous.sources_complete),
            sources_partial=int(previous.sources_partial),
            sources_skipped=int(previous.sources_skipped),
            sources_failed=int(previous.sources_failed),
        )
    except ValueError as exc:
        raise CollectionInvariantError(
            f"recovered collect source summary is invalid: {exc}"
        ) from exc
    return CollectResult(
        status=status,
        recovered_evidence=evidence,
        publication_committed=True,
        coverage_declined=coverage_declined,
    )


def orchestrate_collect(
    *,
    config_path: Path,
    stage: str,
    collection_mode: MLFFCollectMode,
    transaction_id: str,
) -> CollectResult:
    """Collect and publish one stage under a single recovery-aware session."""
    if not isinstance(stage, str):
        raise TypeError("stage must be a string")
    if stage not in COLLECT_OUTPUTS:
        return _fatal_collection_result(f"Unknown collect stage: {stage!r}")
    if not isinstance(collection_mode, MLFFCollectMode):
        raise TypeError("collection_mode must be an MLFFCollectMode")
    if not isinstance(transaction_id, str) or not transaction_id.strip():
        raise ValueError("transaction_id must be a non-empty string")

    try:
        config = load_config(config_path)
    except (ConfigError, OSError, UnicodeError, yaml.YAMLError) as exc:
        return _fatal_collection_result(f"config error: {exc}")

    work_dir = Path(config.work_dir)
    try:
        manifest = read_manifest(work_dir, stage)
    except (ValueError, UnicodeError) as exc:
        return _fatal_collection_result(f"manifest error: {exc}")

    try:
        target = _orchestration_result_target(
            config=config,
            stage=stage,
            manifest=manifest,
            collection_mode=collection_mode,
        )
    except CollectionInvariantError as exc:
        return _fatal_collection_result(exc)

    try:
        work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _fatal_collection_result(f"could not prepare collection work_dir: {exc}")

    final_output = work_dir / COLLECT_OUTPUTS[stage]
    candidate: CollectionCandidate | None = None
    try:
        with PublicationSession(
            work_dir=work_dir,
            stage=stage,
            final_output=final_output,
            target=target,
        ) as session:
            if session.recovered_result is not None:
                return _recovered_collection_result(session)

            if target.kind is ResultManifestTargetKind.MD_COMPATIBILITY:
                _validated_previous_collect_record(session)

            candidate = _build_orchestration_candidate(
                config=config,
                stage=stage,
                manifest=manifest,
                collection_mode=collection_mode,
            )
            publication_target, compatibility_evidence = (
                _candidate_publication_target(
                    work_dir=work_dir,
                    stage=stage,
                    manifest=manifest,
                    candidate=candidate,
                    collection_mode=collection_mode,
                    expected_target=target,
                )
            )
            if candidate.frame_count == 0:
                return _publish_no_data_in_session(
                    session,
                    work_dir=work_dir,
                    stage=stage,
                    final_output=final_output,
                    target=publication_target,
                    manifest=manifest,
                    candidate=candidate,
                    compatibility_evidence=compatibility_evidence,
                    transaction_id=transaction_id,
                )
            return _publish_nonzero_in_session(
                session,
                work_dir=work_dir,
                stage=stage,
                final_output=final_output,
                target=publication_target,
                manifest=manifest,
                candidate=candidate,
                collection_mode=collection_mode,
                compatibility_evidence=compatibility_evidence,
                transaction_id=transaction_id,
            )
    except (CollectLockError, CollectionInvariantError, PublicationError) as exc:
        return _fatal_collection_result(exc, candidate=candidate)


def run_collect(config_path: Path, stage: str) -> None:
    if stage not in COLLECT_OUTPUTS:
        raise ValueError(f"Unknown collect stage: {stage}")

    config = load_config(config_path)
    manifest = _require_stage_manifest(config, stage)
    collectors = {
        "rlx": collect_rlx,
        "md": collect_md,
        "validation": collect_validation,
    }
    dataset, manifest = collectors[stage](config, manifest)

    output_path = config.work_dir / COLLECT_OUTPUTS[stage]
    manifest.collect["output"] = _display_path(config.work_dir, output_path)
    if dataset.n_configs > 0:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        dataset.save_extxyz(output_path)
        manifest.collect["written"] = True
        manifest.collect["removed_stale_output"] = False
    else:
        removed_stale_output = output_path.exists()
        if removed_stale_output:
            output_path.unlink()
        manifest.collect["written"] = False
        manifest.collect["removed_stale_output"] = removed_stale_output
    write_manifest(config.work_dir, manifest)


def collect_rlx(config: DPmoireLiteConfig, manifest: Manifest) -> tuple[Dataset, Manifest]:
    manifest = _prepare_manifest(config, manifest, "rlx")
    return _collect_outcars(config, manifest, freq=config.outcar_collect_freq)


def collect_md(config: DPmoireLiteConfig, manifest: Manifest) -> tuple[Dataset, Manifest]:
    manifest = _prepare_manifest(config, manifest, "md")
    if config.vasp_ml:
        return _collect_md_ml(config, manifest)
    return _collect_outcars(config, manifest, freq=config.outcar_collect_freq)


def collect_validation(config: DPmoireLiteConfig, manifest: Manifest) -> tuple[Dataset, Manifest]:
    manifest = _prepare_manifest(config, manifest, "validation")
    return _collect_outcars(config, manifest, freq=1)


def collect_mlab_source(*, work_dir: Path, source_path: Path) -> SourceResult:
    source_path = Path(source_path)
    relative_source_path = relative_to_workdir(work_dir, source_path)

    try:
        parsed = parse_mlab(source_path)
    except MlabParseError as error:
        return SourceResult(
            source_path=relative_source_path,
            source_kind=SourceKind.MLAB,
            status=SourceStatus.FAILED,
            complete_count=0,
            reason=error.reason,
            discarded_configuration_number=error.configuration_number,
            discarded_block=error.block,
            line_number=error.line_number,
        )

    if parsed.status == "complete":
        status = SourceStatus.COMPLETE
        reason = None
    elif parsed.status == "partial":
        status = SourceStatus.PARTIAL
        reason = parsed.discarded_reason
    else:
        raise ValueError(f"unknown ML_ABN parser status: {parsed.status!r}")

    return SourceResult(
        source_path=relative_source_path,
        source_kind=SourceKind.MLAB,
        status=status,
        complete_count=parsed.complete_count,
        parsed_configurations=parsed.configurations,
        reason=reason,
        discarded_configuration_number=parsed.discarded_configuration_number,
        discarded_block=parsed.discarded_block,
    )


def collect_full_dedup_mlab_sources(
    *,
    work_dir: Path,
    source_paths,
) -> FullDedupSourceFoldResult:
    if isinstance(source_paths, (str, bytes, bytearray)):
        raise TypeError("source_paths must be a non-string iterable")
    try:
        paths = tuple(source_paths)
    except TypeError as exc:
        raise TypeError("source_paths must be an iterable") from exc
    if any(not isinstance(source_path, Path) for source_path in paths):
        raise TypeError("source_paths must contain only Path values")

    for source_path in paths:
        if source_path.name != "ML_ABN":
            raise ValueError("full-dedup source paths must have basename ML_ABN")
        relative_to_workdir(work_dir, source_path)

    source_results: list[SourceResult] = []
    fold_sources = []
    for source_path in paths:
        source_result = collect_mlab_source(
            work_dir=work_dir,
            source_path=source_path,
        )
        if not isinstance(source_result, SourceResult):
            raise TypeError("collect_mlab_source must return a SourceResult")
        if source_result.source_kind is not SourceKind.MLAB:
            raise ValueError("full-dedup source result must be an MLAB source")
        if source_result.status not in {
            SourceStatus.COMPLETE,
            SourceStatus.PARTIAL,
            SourceStatus.FAILED,
        }:
            raise ValueError("full-dedup source result has an invalid status")

        source_results.append(source_result)
        configurations = (
            source_result.parsed_configurations
            if source_result.status
            in {SourceStatus.COMPLETE, SourceStatus.PARTIAL}
            else ()
        )
        fold_sources.append((source_result.source_path, configurations))

    dedup = fold_exact_configurations(tuple(fold_sources))
    return FullDedupSourceFoldResult(
        source_results=tuple(source_results),
        dedup=dedup,
    )


def build_full_dedup_candidate(
    *,
    work_dir: Path,
    manifest: ManifestReadResult,
) -> CollectionCandidate:
    work_dir = Path(work_dir)
    source_inventory = build_mlff_source_inventory(
        work_dir=work_dir,
        mode=MLFFCollectMode.FULL_DEDUP,
        manifest=manifest,
    )
    if not isinstance(source_inventory, SourceInventory):
        raise TypeError("build_mlff_source_inventory must return a SourceInventory")

    source_paths: list[Path] = []
    inventory_slots: list[Path | None] = []
    skipped_results: list[SourceResult | None] = []

    for declared_directory in source_inventory.directories:
        directory = _candidate_directory_path(work_dir, declared_directory)
        if not directory.is_dir():
            inventory_slots.append(None)
            skipped_results.append(
                _skipped_candidate_source(
                    declared_directory,
                    SourceKind.MLAB,
                    f"Missing declared directory: {declared_directory}",
                )
            )
            continue

        source_path = directory / "ML_ABN"
        relative_source_path = relative_to_workdir(work_dir, source_path)
        if not source_path.is_file():
            inventory_slots.append(None)
            skipped_results.append(
                _skipped_candidate_source(
                    relative_source_path,
                    SourceKind.MLAB,
                    f"Missing ML_ABN source: {relative_source_path}",
                )
            )
            continue

        source_paths.append(source_path)
        inventory_slots.append(source_path)
        skipped_results.append(None)

    folded = collect_full_dedup_mlab_sources(
        work_dir=work_dir,
        source_paths=tuple(source_paths),
    )
    if not isinstance(folded, FullDedupSourceFoldResult):
        raise TypeError(
            "collect_full_dedup_mlab_sources must return a "
            "FullDedupSourceFoldResult"
        )
    if len(folded.source_results) != len(source_paths):
        raise ValueError("full-dedup source results do not match source paths")

    merged_source_results: list[SourceResult] = []
    merged_per_source: list[SourceDedupStats] = []
    folded_index = 0
    for source_path, skipped_result in zip(
        inventory_slots,
        skipped_results,
        strict=True,
    ):
        if source_path is None:
            if skipped_result is None:
                raise ValueError("missing inventory slot has no skipped result")
            merged_source_results.append(skipped_result)
            merged_per_source.append(
                SourceDedupStats(
                    source_path=skipped_result.source_path,
                    seen=0,
                    retained=0,
                    duplicates_removed=0,
                )
            )
            continue

        source_result = folded.source_results[folded_index]
        source_stats = folded.dedup.stats.per_source[folded_index]
        folded_index += 1
        merged_source_results.append(source_result)
        merged_per_source.append(source_stats)

    if folded_index != len(folded.source_results):
        raise ValueError("not all full-dedup source results were merged")

    dedup_stats = replace(
        folded.dedup.stats,
        per_source=tuple(merged_per_source),
    )
    return CollectionCandidate(
        source_results=tuple(merged_source_results),
        accepted_frames=folded.dedup.accepted_frames,
        expected_directories=source_inventory.directories,
        collection_mode=MLFFCollectMode.FULL_DEDUP,
        source_inventory=source_inventory,
        dedup_stats=dedup_stats,
    )


def collect_current_mlab_source(
    *,
    work_dir: Path,
    source_path: Path,
    manifest: Manifest,
) -> SourceResult:
    seed_count, expected_digest = _current_seed_evidence(manifest)
    return _collect_mlab_seed_prefix(
        work_dir=work_dir,
        source_path=source_path,
        seed_count=seed_count,
        expected_digest=expected_digest,
    )


def collect_legacy_mlab_source(
    *,
    work_dir: Path,
    source_path: Path,
    manifest: ManifestReadResult,
) -> SourceResult:
    _validate_legacy_manifest_result(manifest)

    source_path = Path(source_path)
    relative_source_path = relative_to_workdir(work_dir, source_path)
    seed_path = Path(work_dir) / "init_mlff" / "ML_ABN"
    if not seed_path.is_file():
        return _legacy_seed_failure(
            relative_source_path,
            "legacy seed evidence missing: init_mlff/ML_ABN",
        )

    try:
        parsed_seed = parse_mlab(seed_path)
    except MlabParseError as error:
        details = [error.reason]
        if error.configuration_number is not None:
            details.append(f"configuration {error.configuration_number}")
        if error.block:
            details.append(f"block {error.block}")
        if error.line_number is not None:
            details.append(f"line {error.line_number}")
        return _legacy_seed_failure(
            relative_source_path,
            "legacy seed evidence at init_mlff/ML_ABN is invalid: "
            + ", ".join(details),
        )

    if parsed_seed.status == "partial":
        reason = parsed_seed.discarded_reason or "parser returned partial evidence"
        return _legacy_seed_failure(
            relative_source_path,
            "legacy seed evidence at init_mlff/ML_ABN must be complete: " + reason,
        )
    if parsed_seed.status != "complete":
        raise ValueError(f"unknown ML_ABN parser status: {parsed_seed.status!r}")

    seed_count = parsed_seed.complete_count
    seed_identity = seed_prefix_identity(parsed_seed.configurations)
    warnings.warn(
        "Legacy seed evidence was rebuilt from init_mlff/ML_ABN using "
        "mlab-seed-v1; the final prefix is being verified.",
        UserWarning,
        stacklevel=2,
    )
    return _collect_mlab_seed_prefix(
        work_dir=work_dir,
        source_path=source_path,
        seed_count=seed_count,
        expected_digest=seed_identity.sha256,
    )


def collect_outcar_source(
    *,
    work_dir: Path,
    selection: OutcarSelection,
    freq: int,
) -> SourceResult:
    if isinstance(freq, bool) or not isinstance(freq, int) or freq <= 0:
        raise ValueError("freq must be a positive integer")
    if not isinstance(selection, OutcarSelection):
        raise TypeError("selection must be an OutcarSelection")

    source_path = Path(selection.path)
    relative_source_path = relative_to_workdir(work_dir, source_path)
    complete_count = 0
    sampled_frames = []
    iterator_error: ParseError | ValueError | UnicodeDecodeError | None = None

    with open_outcar_frames(source_path) as frames:
        try:
            for raw_index, frame in enumerate(frames):
                complete_count += 1
                if raw_index % freq == 0:
                    sampled_frames.append(frame)
        except (ParseError, ValueError, UnicodeDecodeError) as error:
            iterator_error = error

    common = {
        "source_path": relative_source_path,
        "source_kind": SourceKind.OUTCAR,
        "complete_count": complete_count,
        "pattern": selection.pattern,
        "pattern_index": selection.pattern_index,
        "order": selection.order,
    }

    if iterator_error is not None:
        if isinstance(iterator_error, UnicodeDecodeError):
            reason = f"OUTCAR UTF-8 decoding failed: {iterator_error}"
            line_number = None
        else:
            reason = (
                "OUTCAR parser failed after "
                f"{complete_count} complete frame(s): {iterator_error}"
            )
            line_number = _find_outcar_tail_evidence(
                source_path,
                complete_count,
            )
        return SourceResult(
            **common,
            status=SourceStatus.FAILED,
            accepted_frames=(),
            discarded_frame_index=complete_count,
            line_number=line_number,
            reason=reason,
        )

    tail_line = _find_outcar_tail_evidence(source_path, complete_count)
    if tail_line is not None:
        reason = (
            "OUTCAR ended at EOF after an incomplete ionic step started; "
            f"structural evidence at line {tail_line}"
        )
        if complete_count == 0:
            return SourceResult(
                **common,
                status=SourceStatus.FAILED,
                accepted_frames=(),
                discarded_frame_index=0,
                line_number=tail_line,
                reason=reason,
            )
        return SourceResult(
            **common,
            status=SourceStatus.PARTIAL,
            accepted_frames=tuple(sampled_frames),
            discarded_frame_index=complete_count,
            line_number=tail_line,
            reason=reason,
        )

    return SourceResult(
        **common,
        status=SourceStatus.COMPLETE,
        accepted_frames=tuple(sampled_frames),
    )


def build_seed_aware_candidate(
    *,
    config: DPmoireLiteConfig,
    stage: str,
    manifest: ManifestReadResult,
) -> CollectionCandidate:
    if not isinstance(config, DPmoireLiteConfig):
        raise TypeError("config must be a DPmoireLiteConfig")
    if not isinstance(stage, str) or stage not in COLLECT_OUTPUTS:
        raise ValueError(f"Unknown collect stage: {stage!r}")

    expected_directories = _candidate_manifest_directories(
        manifest,
        stage=stage,
    )
    is_mlab_mode = stage == "md" and config.vasp_ml
    if is_mlab_mode and manifest.kind == "current":
        current_manifest = manifest.manifest
        if current_manifest is None:
            raise ValueError("current manifest result has no Manifest")
        _current_seed_evidence(current_manifest)

    work_dir = Path(config.work_dir)
    source_results: list[SourceResult] = []
    accepted_frames = []
    source_kind = SourceKind.MLAB if is_mlab_mode else SourceKind.OUTCAR
    outcar_freq = 1 if stage == "validation" else config.outcar_collect_freq

    for declared_directory in expected_directories:
        directory = _candidate_directory_path(work_dir, declared_directory)
        if not directory.is_dir():
            source_results.append(
                _skipped_candidate_source(
                    declared_directory,
                    source_kind,
                    f"Missing declared directory: {declared_directory}",
                )
            )
            continue

        if is_mlab_mode:
            source_path = directory / "ML_ABN"
            relative_source_path = f"{declared_directory}/ML_ABN"
            if not source_path.is_file():
                source_results.append(
                    _skipped_candidate_source(
                        relative_source_path,
                        SourceKind.MLAB,
                        f"Missing ML_ABN source: {relative_source_path}",
                    )
                )
                continue

            if manifest.kind == "current":
                current_manifest = manifest.manifest
                if current_manifest is None:
                    raise ValueError("current manifest result has no Manifest")
                source_result = collect_current_mlab_source(
                    work_dir=work_dir,
                    source_path=source_path,
                    manifest=current_manifest,
                )
            else:
                source_result = collect_legacy_mlab_source(
                    work_dir=work_dir,
                    source_path=source_path,
                    manifest=manifest,
                )
            source_results.append(source_result)
            _append_candidate_payload(source_result, accepted_frames)
            continue

        selections = find_outcar_series(directory, config.outcar_patterns)
        if not selections:
            source_results.append(
                _skipped_candidate_source(
                    declared_directory,
                    SourceKind.OUTCAR,
                    "No configured OUTCAR match in declared directory: "
                    f"{declared_directory}",
                )
            )
            continue

        for selection in selections:
            source_result = collect_outcar_source(
                work_dir=work_dir,
                selection=selection,
                freq=outcar_freq,
            )
            source_results.append(source_result)
            _append_candidate_payload(source_result, accepted_frames)

    return CollectionCandidate(
        source_results=tuple(source_results),
        accepted_frames=tuple(accepted_frames),
        expected_directories=expected_directories,
    )


def _candidate_manifest_directories(
    manifest: ManifestReadResult,
    *,
    stage: str,
) -> tuple[str, ...]:
    if not isinstance(manifest, ManifestReadResult):
        raise TypeError("manifest must be a ManifestReadResult")

    if manifest.kind == "current":
        current_manifest = manifest.manifest
        if not isinstance(current_manifest, Manifest):
            raise ValueError("current manifest result has no valid Manifest")
        if not isinstance(manifest.raw_data, Mapping):
            raise ValueError("current manifest result has no raw manifest data")
        if current_manifest.schema_version != 2:
            raise ValueError("current manifest must use schema_version 2")
        if current_manifest.stage != stage:
            raise ValueError(
                f"current manifest stage {current_manifest.stage!r} does not "
                f"match requested stage {stage!r}"
            )
        raw_schema_version = manifest.raw_data.get("schema_version")
        raw_stage = manifest.raw_data.get("stage")
        if raw_schema_version != 2 or raw_stage != current_manifest.stage:
            raise ValueError("current manifest result is internally inconsistent")
        directories = current_manifest.directories
    elif manifest.kind == "legacy":
        if manifest.manifest is not None or not isinstance(
            manifest.raw_data,
            Mapping,
        ):
            raise ValueError("legacy manifest result is internally inconsistent")
        raw_stage = manifest.raw_data.get("stage")
        if raw_stage is not None and raw_stage != stage:
            raise ValueError(
                f"legacy manifest stage {raw_stage!r} does not match "
                f"requested stage {stage!r}"
            )
        directories = manifest.raw_data.get("directories", [])
    else:
        raise ValueError(
            "manifest must be a current or legacy ManifestReadResult"
        )

    if not isinstance(directories, (list, tuple)):
        raise ValueError("manifest directories must be a sequence")
    normalized_directories = tuple(directories)
    for directory in normalized_directories:
        _validate_relative_source_path(directory)
    return normalized_directories


def _candidate_directory_path(work_dir: Path, relative_directory: str) -> Path:
    directory = Path(work_dir) / relative_directory
    relative_to_workdir(work_dir, directory)
    return directory


def _skipped_candidate_source(
    source_path: str,
    source_kind: SourceKind,
    reason: str,
) -> SourceResult:
    return SourceResult(
        source_path=source_path,
        source_kind=source_kind,
        status=SourceStatus.SKIPPED,
        complete_count=0,
        reason=reason,
    )


def _append_candidate_payload(
    source_result: SourceResult,
    accepted_frames: list,
) -> None:
    if source_result.status not in {
        SourceStatus.COMPLETE,
        SourceStatus.PARTIAL,
    }:
        return
    if source_result.source_kind is SourceKind.OUTCAR:
        accepted_frames.extend(source_result.accepted_frames)
        return
    accepted_frames.extend(
        atoms_from_mlab_configuration(configuration)
        for configuration in source_result.parsed_configurations
    )


def _validate_legacy_manifest_result(manifest: ManifestReadResult) -> None:
    if not isinstance(manifest, ManifestReadResult):
        raise ValueError("legacy collection requires a ManifestReadResult")
    if (
        manifest.kind != "legacy"
        or manifest.manifest is not None
        or not isinstance(manifest.raw_data, Mapping)
    ):
        raise ValueError("legacy collection requires a valid legacy ManifestReadResult")


def _legacy_seed_failure(source_path: str, reason: str) -> SourceResult:
    return SourceResult(
        source_path=source_path,
        source_kind=SourceKind.MLAB,
        status=SourceStatus.FAILED,
        complete_count=0,
        reason=reason,
    )


def _collect_mlab_seed_prefix(
    *,
    work_dir: Path,
    source_path: Path,
    seed_count: int,
    expected_digest: str,
) -> SourceResult:
    raw_result = collect_mlab_source(work_dir=work_dir, source_path=source_path)

    if raw_result.status is SourceStatus.FAILED:
        return raw_result

    if raw_result.complete_count < seed_count:
        reason = (
            "source is shorter than the initial seed: "
            f"{raw_result.complete_count} complete configurations available, "
            f"{seed_count} required"
        )
        if raw_result.reason:
            reason = f"{reason}; {raw_result.reason}"
        return replace(
            raw_result,
            status=SourceStatus.FAILED,
            accepted_frames=(),
            parsed_configurations=(),
            reason=reason,
            seed_identity=None,
        )

    actual_identity = seed_prefix_identity(
        raw_result.parsed_configurations,
        n_configurations=seed_count,
    )
    if actual_identity.sha256 != expected_digest:
        reason = (
            "seed prefix mismatch: "
            f"expected {expected_digest}, actual {actual_identity.sha256}"
        )
        if raw_result.reason:
            reason = f"{reason}; {raw_result.reason}"
        return replace(
            raw_result,
            status=SourceStatus.FAILED,
            accepted_frames=(),
            parsed_configurations=(),
            reason=reason,
            seed_identity=actual_identity,
        )

    return replace(
        raw_result,
        parsed_configurations=raw_result.parsed_configurations[seed_count:],
        seed_identity=actual_identity,
    )


def _current_seed_evidence(manifest: Manifest) -> tuple[int, str]:
    if not isinstance(manifest, Manifest) or manifest.schema_version != 2:
        raise ValueError("manifest must be a current Manifest v2")
    if manifest.stage != "md":
        raise ValueError("manifest.stage must be 'md' for current ML_ABN collection")

    seed = manifest.mlff_seed
    if not isinstance(seed, Mapping):
        raise ValueError("manifest.mlff_seed must be a mapping")

    configurations = seed.get("configurations")
    if (
        isinstance(configurations, bool)
        or not isinstance(configurations, int)
        or configurations <= 0
    ):
        raise ValueError("mlff_seed.configurations must be a positive integer")

    if seed.get("digest_schema") != "mlab-seed-v1":
        raise ValueError("mlff_seed.digest_schema must be 'mlab-seed-v1'")

    digest = seed.get("seed_prefix_sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError(
            "mlff_seed.seed_prefix_sha256 must be a 64-character lowercase "
            "hexadecimal string"
        )
    return configurations, digest


def _collect_md_ml(config: DPmoireLiteConfig, manifest: Manifest) -> tuple[Dataset, Manifest]:
    dataset = Dataset()
    source_count = 0

    for directory in _manifest_directories(config, manifest):
        if not directory.exists():
            _record(manifest.skipped, config.work_dir, directory, "Missing directory")
            continue

        ml_abn = directory / "ML_ABN"
        if not ml_abn.exists():
            _record(manifest.skipped, config.work_dir, ml_abn, "Missing ML_ABN")
            continue

        skip_configs = 0
        ml_ab = directory / "ML_AB"
        if ml_ab.exists():
            try:
                skip_configs = count_ml_ab_configs(ml_ab)
            except Exception as exc:
                _record(manifest.failed, config.work_dir, ml_ab, f"Could not read ML_AB count: {exc}")
                continue

        source_count += 1
        try:
            dataset.load_ml_ab(ml_abn, skip_configs=skip_configs)
        except Exception as exc:
            _record(manifest.failed, config.work_dir, ml_abn, f"Failed to parse ML_ABN: {exc}")

    _set_collect_summary(config, manifest, dataset, source_count)
    return dataset, manifest


def _collect_outcars(config: DPmoireLiteConfig, manifest: Manifest, freq: int) -> tuple[Dataset, Manifest]:
    dataset = Dataset()
    source_count = 0

    for directory in _manifest_directories(config, manifest):
        if not directory.exists():
            _record(manifest.skipped, config.work_dir, directory, "Missing directory")
            continue

        outcars = find_outcar_series(directory, config.outcar_patterns)
        if not outcars:
            _record(manifest.skipped, config.work_dir, directory, "No OUTCAR files matched configured patterns")
            continue

        for outcar in outcars:
            source_count += 1
            try:
                dataset.load_outcar(outcar, freq=freq)
            except Exception as exc:
                _record(manifest.failed, config.work_dir, outcar, f"Failed to parse OUTCAR: {exc}")

    _set_collect_summary(config, manifest, dataset, source_count)
    return dataset, manifest


def _prepare_manifest(config: DPmoireLiteConfig, manifest: Manifest, stage: str) -> Manifest:
    del config
    manifest.stage = stage
    manifest.collect = {}
    manifest.skipped = []
    manifest.failed = []
    return manifest


def _require_stage_manifest(config: DPmoireLiteConfig, stage: str) -> Manifest:
    path = manifest_path(config.work_dir, stage)
    result = read_manifest(config.work_dir, stage)
    if result.kind == "missing":
        raise RuntimeError(
            f"Missing {stage} manifest at {path}; collect requires a completed stage manifest. "
            "The stage may be an incomplete build; complete or delete and rebuild it first."
        )
    if result.kind == "legacy":
        raise RuntimeError(
            f"Legacy {stage} manifest at {path} is not accepted by collect; "
            "a current Manifest v2 is required."
        )
    if result.manifest is None:
        raise RuntimeError(f"Invalid {stage} manifest at {path}; no manifest data was loaded.")
    return result.manifest


def _manifest_directories(config: DPmoireLiteConfig, manifest: Manifest) -> list[Path]:
    directories: list[Path] = []
    for directory in manifest.directories:
        path = Path(directory)
        directories.append(path if path.is_absolute() else config.work_dir / path)
    return directories


def _set_collect_summary(
    config: DPmoireLiteConfig,
    manifest: Manifest,
    dataset: Dataset,
    source_count: int,
) -> None:
    manifest.collect.update(
        {
            "frames": dataset.n_configs,
            "sources": source_count,
            "directories": len(manifest.directories),
            "collected_at": datetime.now().isoformat(timespec="seconds"),
            "output": COLLECT_OUTPUTS[manifest.stage],
        }
    )


def _record(records: list[dict[str, str]], work_dir: Path, path: Path, reason: str) -> None:
    records.append({"path": _display_path(work_dir, path), "reason": reason})


def _display_path(work_dir: Path, path: Path) -> str:
    path = Path(path)
    try:
        return relative_to_workdir(work_dir, path)
    except ValueError:
        return path.as_posix()
