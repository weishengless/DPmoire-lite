from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath

from ase import Atoms

from .mlab import MlabConfiguration, MlabIdentity


class SourceKind(str, Enum):
    MLAB = "mlab"
    OUTCAR = "outcar"


class SourceStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED = "failed"


class CollectStatus(str, Enum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    NO_DATA = "no_data"
    FATAL = "fatal"


class MLFFCollectMode(str, Enum):
    SEED_AWARE = "seed-aware"
    FULL_DEDUP = "full-dedup"


DEFAULT_MLFF_COLLECT_MODE = MLFFCollectMode.SEED_AWARE


@dataclass(frozen=True)
class SourceInventory:
    manifest_kind: str
    directory_discovery: str
    directories: tuple[str, ...]
    coverage_known: bool
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.manifest_kind not in {"current", "legacy", "missing"}:
            raise ValueError("manifest_kind must be current, legacy, or missing")
        if self.directory_discovery not in {"declared", "legacy-scan"}:
            raise ValueError(
                "directory_discovery must be declared or legacy-scan"
            )
        if not isinstance(self.coverage_known, bool):
            raise TypeError("coverage_known must be a bool")

        if self.manifest_kind in {"current", "legacy"}:
            if self.directory_discovery != "declared" or not self.coverage_known:
                raise ValueError(
                    "current and legacy inventories require declared, known coverage"
                )
        elif self.directory_discovery != "legacy-scan" or self.coverage_known:
            raise ValueError(
                "missing inventories require legacy-scan, unknown coverage"
            )

        directories = _normalize_iterable("directories", self.directories)
        for directory in directories:
            _validate_relative_source_path(directory)

        warnings = _normalize_iterable("warnings", self.warnings)
        for warning in warnings:
            if not isinstance(warning, str) or not warning.strip():
                raise ValueError("warnings must contain non-empty strings")

        object.__setattr__(self, "directories", directories)
        object.__setattr__(self, "warnings", warnings)


@dataclass(frozen=True)
class SourceDedupStats:
    source_path: str
    seen: int
    retained: int
    duplicates_removed: int

    def __post_init__(self) -> None:
        _validate_relative_source_path(self.source_path)
        _validate_nonnegative_integer("seen", self.seen)
        _validate_nonnegative_integer("retained", self.retained)
        _validate_nonnegative_integer(
            "duplicates_removed",
            self.duplicates_removed,
        )
        if self.seen != self.retained + self.duplicates_removed:
            raise ValueError(
                "source seen must equal retained plus duplicates_removed"
            )


@dataclass(frozen=True)
class DedupStats:
    seen: int
    unique: int
    duplicates_removed: int
    candidate_frame_count: int
    per_source: tuple[SourceDedupStats, ...]
    schema: str = "mlab-config-v1"

    def __post_init__(self) -> None:
        _validate_nonnegative_integer("seen", self.seen)
        _validate_nonnegative_integer("unique", self.unique)
        _validate_nonnegative_integer(
            "duplicates_removed",
            self.duplicates_removed,
        )
        _validate_nonnegative_integer(
            "candidate_frame_count",
            self.candidate_frame_count,
        )
        if self.schema != "mlab-config-v1":
            raise ValueError("schema must be mlab-config-v1")

        per_source = _normalize_iterable("per_source", self.per_source)
        if any(not isinstance(stats, SourceDedupStats) for stats in per_source):
            raise TypeError("per_source must contain only SourceDedupStats values")
        object.__setattr__(self, "per_source", per_source)

        if self.seen != self.unique + self.duplicates_removed:
            raise ValueError("seen must equal unique plus duplicates_removed")
        if self.unique != self.candidate_frame_count:
            raise ValueError("unique must equal candidate_frame_count")
        if sum(stats.seen for stats in per_source) != self.seen:
            raise ValueError("per-source seen counts must equal aggregate seen")
        if sum(stats.retained for stats in per_source) != self.unique:
            raise ValueError(
                "per-source retained counts must equal aggregate unique"
            )
        if sum(stats.duplicates_removed for stats in per_source) != (
            self.duplicates_removed
        ):
            raise ValueError(
                "per-source duplicate counts must equal aggregate duplicates_removed"
            )


@dataclass(frozen=True)
class SourceResult:
    source_path: str
    source_kind: SourceKind
    status: SourceStatus
    complete_count: int
    accepted_frames: tuple[Atoms, ...] = ()
    parsed_configurations: tuple[MlabConfiguration, ...] = ()
    reason: str | None = None
    discarded_configuration_number: int | None = None
    discarded_frame_index: int | None = None
    discarded_block: str | None = None
    line_number: int | None = None
    pattern: str | None = None
    pattern_index: int | None = None
    order: int | None = None
    seed_identity: MlabIdentity | None = None

    def __post_init__(self) -> None:
        _validate_relative_source_path(self.source_path)
        if not isinstance(self.source_kind, SourceKind):
            raise TypeError("source_kind must be a SourceKind")
        if not isinstance(self.status, SourceStatus):
            raise TypeError("status must be a SourceStatus")
        _validate_nonnegative_integer("complete_count", self.complete_count)

        supplied_frames = tuple(self.accepted_frames)
        if any(not isinstance(frame, Atoms) for frame in supplied_frames):
            raise TypeError("accepted_frames must contain only ASE Atoms")
        frames = tuple(copy.deepcopy(frame) for frame in supplied_frames)
        configurations = tuple(self.parsed_configurations)
        if any(
            not isinstance(configuration, MlabConfiguration)
            for configuration in configurations
        ):
            raise TypeError(
                "parsed_configurations must contain only MlabConfiguration values"
            )

        object.__setattr__(self, "accepted_frames", frames)
        object.__setattr__(self, "parsed_configurations", configurations)

        if frames and configurations:
            raise ValueError(
                "source result cannot expose both frames and parsed configurations"
            )
        if self.source_kind is SourceKind.MLAB and frames:
            raise ValueError("MLAB source result cannot expose accepted frames")
        if self.source_kind is SourceKind.OUTCAR and configurations:
            raise ValueError(
                "OUTCAR source result cannot expose parsed configurations"
            )
        if self.status is SourceStatus.FAILED and frames:
            raise ValueError("failed source cannot expose accepted frames")
        if self.status is SourceStatus.FAILED and configurations:
            raise ValueError(
                "failed source cannot expose parsed configurations"
            )
        if self.status is SourceStatus.SKIPPED and self.accepted_count:
            raise ValueError("skipped source cannot expose accepted payload")
        if self.complete_count < self.accepted_count:
            raise ValueError(
                "complete_count cannot be smaller than accepted_count"
            )
        if self.status in {
            SourceStatus.PARTIAL,
            SourceStatus.SKIPPED,
            SourceStatus.FAILED,
        } and (not isinstance(self.reason, str) or not self.reason.strip()):
            raise ValueError(f"{self.status.value} source requires a reason")
        if self.reason is not None and not isinstance(self.reason, str):
            raise TypeError("reason must be a string or None")

        _validate_optional_positive_integer(
            "discarded_configuration_number",
            self.discarded_configuration_number,
        )
        _validate_optional_nonnegative_integer(
            "discarded_frame_index",
            self.discarded_frame_index,
        )
        _validate_optional_positive_integer("line_number", self.line_number)
        _validate_optional_nonnegative_integer("pattern_index", self.pattern_index)
        _validate_optional_nonnegative_integer("order", self.order)
        if self.discarded_block is not None and (
            not isinstance(self.discarded_block, str)
            or not self.discarded_block.strip()
        ):
            raise ValueError("discarded_block must be a non-empty string or None")
        if self.pattern is not None and (
            not isinstance(self.pattern, str) or not self.pattern
        ):
            raise ValueError("pattern must be a non-empty string or None")
        if (self.pattern is None) != (self.pattern_index is None):
            raise ValueError("pattern and pattern_index must be provided together")
        if self.seed_identity is not None and not isinstance(
            self.seed_identity,
            MlabIdentity,
        ):
            raise TypeError("seed_identity must be an MlabIdentity or None")

    @property
    def accepted_count(self) -> int:
        return len(self.accepted_frames) + len(self.parsed_configurations)

    def as_diagnostic(self) -> dict[str, object]:
        diagnostic: dict[str, object] = {
            "path": self.source_path,
            "kind": self.source_kind.value,
            "status": self.status.value,
            "complete_count": self.complete_count,
            "accepted_count": self.accepted_count,
        }
        optional_values = (
            ("reason", self.reason),
            ("discarded_configuration", self.discarded_configuration_number),
            ("discarded_frame", self.discarded_frame_index),
            ("discarded_block", self.discarded_block),
            ("line_number", self.line_number),
            ("pattern", self.pattern),
            ("pattern_index", self.pattern_index),
            ("order", self.order),
        )
        for key, value in optional_values:
            if value is not None:
                diagnostic[key] = value
        if self.seed_identity is not None:
            diagnostic["seed_identity"] = {
                "schema": self.seed_identity.schema,
                "sha256": self.seed_identity.sha256,
            }
        return diagnostic


@dataclass(frozen=True)
class CollectionCandidate:
    source_results: tuple[SourceResult, ...]
    accepted_frames: tuple[Atoms, ...]
    expected_directories: tuple[str, ...] = ()
    collection_mode: MLFFCollectMode | None = None
    source_inventory: SourceInventory | None = None
    dedup_stats: DedupStats | None = None

    def __post_init__(self) -> None:
        source_results = tuple(self.source_results)
        if any(
            not isinstance(source_result, SourceResult)
            for source_result in source_results
        ):
            raise TypeError("source_results must contain only SourceResult values")
        frames = tuple(copy.deepcopy(frame) for frame in self.accepted_frames)
        if any(not isinstance(frame, Atoms) for frame in frames):
            raise TypeError("accepted_frames must contain only ASE Atoms")
        expected_directories = tuple(self.expected_directories)
        for directory in expected_directories:
            _validate_relative_source_path(directory)

        object.__setattr__(
            self,
            "source_results",
            tuple(copy.deepcopy(source_result) for source_result in source_results),
        )
        object.__setattr__(self, "accepted_frames", frames)
        object.__setattr__(self, "expected_directories", expected_directories)

        source_accepted_count = sum(
            source_result.accepted_count for source_result in source_results
        )
        metadata_present = any(
            value is not None
            for value in (
                self.collection_mode,
                self.source_inventory,
                self.dedup_stats,
            )
        )
        if not metadata_present:
            if len(frames) != source_accepted_count:
                raise ValueError(
                    f"candidate frame count {len(frames)} does not equal "
                    f"source accepted count {source_accepted_count}"
                )
            return

        if not all(
            value is not None
            for value in (
                self.collection_mode,
                self.source_inventory,
                self.dedup_stats,
            )
        ):
            raise ValueError(
                "full-dedup candidate metadata must be supplied together"
            )
        if self.collection_mode is not MLFFCollectMode.FULL_DEDUP:
            raise ValueError(
                "full-dedup candidate collection_mode must be FULL_DEDUP"
            )
        if not isinstance(self.source_inventory, SourceInventory):
            raise TypeError("source_inventory must be a SourceInventory")
        if not isinstance(self.dedup_stats, DedupStats):
            raise TypeError("dedup_stats must be a DedupStats")
        if expected_directories != self.source_inventory.directories:
            raise ValueError(
                "candidate expected_directories must match source inventory"
            )

        for source_result in source_results:
            if source_result.source_kind is not SourceKind.MLAB:
                raise ValueError(
                    "full-dedup candidate source results must be MLAB sources"
                )
            if source_result.status not in {
                SourceStatus.COMPLETE,
                SourceStatus.PARTIAL,
                SourceStatus.SKIPPED,
                SourceStatus.FAILED,
            }:
                raise ValueError(
                    "full-dedup candidate source results must be finalized"
                )

        per_source = self.dedup_stats.per_source
        if len(per_source) != len(source_results):
            raise ValueError(
                "full-dedup candidate per-source stats must align with sources"
            )
        for source_result, source_stats in zip(
            source_results,
            per_source,
            strict=True,
        ):
            if source_stats.source_path != source_result.source_path:
                raise ValueError(
                    "full-dedup candidate per-source paths must align"
                )
            if source_result.status in {
                SourceStatus.COMPLETE,
                SourceStatus.PARTIAL,
            }:
                if source_stats.seen != source_result.accepted_count:
                    raise ValueError(
                        "complete and partial source seen counts must equal "
                        "accepted_count"
                    )
            elif (
                source_stats.seen,
                source_stats.retained,
                source_stats.duplicates_removed,
            ) != (0, 0, 0):
                raise ValueError(
                    "skipped and failed sources must have zero dedup statistics"
                )

        if self.dedup_stats.seen != source_accepted_count:
            raise ValueError(
                "dedup seen count must equal finalized source accepted counts"
            )
        if len(frames) != self.dedup_stats.unique:
            raise ValueError(
                "candidate frame count must equal dedup unique count"
            )
        if len(frames) != self.dedup_stats.candidate_frame_count:
            raise ValueError(
                "candidate frame count must equal dedup candidate frame count"
            )

    @property
    def frame_count(self) -> int:
        return len(self.accepted_frames)

    @property
    def expected_directory_count(self) -> int:
        return len(self.expected_directories)

    @property
    def sources_complete(self) -> int:
        return sum(
            source_result.status is SourceStatus.COMPLETE
            for source_result in self.source_results
        )

    @property
    def sources_partial(self) -> int:
        return sum(
            source_result.status is SourceStatus.PARTIAL
            for source_result in self.source_results
        )

    @property
    def sources_skipped(self) -> int:
        return sum(
            source_result.status is SourceStatus.SKIPPED
            for source_result in self.source_results
        )

    @property
    def sources_failed(self) -> int:
        return sum(
            source_result.status is SourceStatus.FAILED
            for source_result in self.source_results
        )

    @property
    def sources_attempted(self) -> int:
        return self.sources_complete + self.sources_partial + self.sources_failed


@dataclass(frozen=True)
class RecoveredCollectionEvidence:
    """Typed summary of a transaction committed during session recovery."""

    transaction_id: str
    status: CollectStatus
    frame_count: int
    sources_attempted: int
    sources_complete: int
    sources_partial: int
    sources_skipped: int
    sources_failed: int

    def __post_init__(self) -> None:
        if not isinstance(self.transaction_id, str) or not self.transaction_id.strip():
            raise ValueError("transaction_id must be a non-empty string")
        if not isinstance(self.status, CollectStatus):
            raise TypeError("status must be a CollectStatus")
        if self.status not in {CollectStatus.COMPLETE, CollectStatus.DEGRADED}:
            raise ValueError(
                "recovered collection status must be complete or degraded"
            )
        _validate_nonnegative_integer("frame_count", self.frame_count)
        if self.frame_count == 0:
            raise ValueError("recovered collection evidence requires accepted frames")

        for name in (
            "sources_attempted",
            "sources_complete",
            "sources_partial",
            "sources_skipped",
            "sources_failed",
        ):
            _validate_nonnegative_integer(name, getattr(self, name))
        if self.sources_attempted != (
            self.sources_complete + self.sources_partial + self.sources_failed
        ):
            raise ValueError(
                "sources_attempted must equal complete + partial + failed"
            )

    @property
    def source_count(self) -> int:
        return (
            self.sources_complete
            + self.sources_partial
            + self.sources_skipped
            + self.sources_failed
        )


@dataclass(frozen=True)
class CollectResult:
    status: CollectStatus
    candidate: CollectionCandidate | None = None
    recovered_evidence: RecoveredCollectionEvidence | None = None
    publication_committed: bool = False
    coverage_declined: bool = False
    warnings: tuple[str, ...] = ()
    fatal_diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, CollectStatus):
            raise TypeError("status must be a CollectStatus")
        if self.candidate is not None and not isinstance(
            self.candidate,
            CollectionCandidate,
        ):
            raise TypeError("candidate must be a CollectionCandidate or None")
        if self.recovered_evidence is not None and not isinstance(
            self.recovered_evidence,
            RecoveredCollectionEvidence,
        ):
            raise TypeError(
                "recovered_evidence must be RecoveredCollectionEvidence or None"
            )
        if not isinstance(self.publication_committed, bool):
            raise TypeError("publication_committed must be a bool")
        if not isinstance(self.coverage_declined, bool):
            raise TypeError("coverage_declined must be a bool")

        warnings = _normalize_iterable("warnings", self.warnings)
        if any(
            not isinstance(warning, str) or not warning.strip()
            for warning in warnings
        ):
            raise ValueError("warnings must contain non-empty strings")
        object.__setattr__(self, "warnings", warnings)
        if self.candidate is not None:
            object.__setattr__(self, "candidate", copy.deepcopy(self.candidate))

        if self.status is CollectStatus.FATAL:
            if self.recovered_evidence is not None:
                raise ValueError("fatal result cannot carry recovered evidence")
            if (
                not isinstance(self.fatal_diagnostic, str)
                or not self.fatal_diagnostic.strip()
            ):
                raise ValueError("fatal result requires a non-empty diagnostic")
            if self.publication_committed:
                raise ValueError("fatal result cannot claim publication success")
            return

        if self.fatal_diagnostic is not None:
            raise ValueError("nonfatal result cannot carry a fatal diagnostic")
        evidence_count = int(self.candidate is not None) + int(
            self.recovered_evidence is not None
        )
        if evidence_count != 1:
            raise ValueError(
                "nonfatal result requires exactly one candidate or recovered evidence"
            )
        if not self.publication_committed:
            raise ValueError("nonfatal result requires publication commitment")
        if self.recovered_evidence is not None:
            if self.status is not self.recovered_evidence.status:
                raise ValueError(
                    "result status must match recovered collection evidence"
                )
            return
        candidate = self.candidate
        if candidate is None:
            raise ValueError("nonfatal candidate result requires a candidate")
        if self.status in {CollectStatus.COMPLETE, CollectStatus.DEGRADED}:
            if candidate.frame_count == 0:
                raise ValueError(
                    "complete and degraded results require accepted frames"
                )
        elif (
            self.status is CollectStatus.NO_DATA
            and candidate.frame_count != 0
        ):
            raise ValueError("no_data result requires zero accepted frames")

    @property
    def accepted_frame_count(self) -> int:
        if self.candidate is not None:
            return self.candidate.frame_count
        if self.recovered_evidence is not None:
            return self.recovered_evidence.frame_count
        return 0

    @property
    def source_results(self) -> tuple[SourceResult, ...]:
        return self.candidate.source_results if self.candidate is not None else ()

    @property
    def source_count(self) -> int:
        if self.recovered_evidence is not None:
            return self.recovered_evidence.source_count
        return len(self.source_results)

    @property
    def sources_complete(self) -> int:
        if self.candidate is not None:
            return self.candidate.sources_complete
        if self.recovered_evidence is not None:
            return self.recovered_evidence.sources_complete
        return 0

    @property
    def sources_partial(self) -> int:
        if self.candidate is not None:
            return self.candidate.sources_partial
        if self.recovered_evidence is not None:
            return self.recovered_evidence.sources_partial
        return 0

    @property
    def sources_skipped(self) -> int:
        if self.candidate is not None:
            return self.candidate.sources_skipped
        if self.recovered_evidence is not None:
            return self.recovered_evidence.sources_skipped
        return 0

    @property
    def sources_failed(self) -> int:
        if self.candidate is not None:
            return self.candidate.sources_failed
        if self.recovered_evidence is not None:
            return self.recovered_evidence.sources_failed
        return 0

    @property
    def sources_attempted(self) -> int:
        if self.candidate is not None:
            return self.candidate.sources_attempted
        if self.recovered_evidence is not None:
            return self.recovered_evidence.sources_attempted
        return 0


def _validate_relative_source_path(source_path: str) -> None:
    if not isinstance(source_path, str) or not source_path:
        raise ValueError("source_path must be a workdir-relative POSIX path")
    posix_path = PurePosixPath(source_path)
    windows_path = PureWindowsPath(source_path)
    if (
        "\\" in source_path
        or posix_path.is_absolute()
        or bool(windows_path.drive)
        or posix_path.as_posix() != source_path
        or source_path == "."
        or ".." in posix_path.parts
    ):
        raise ValueError("source_path must be a workdir-relative POSIX path")


def _normalize_iterable(name: str, value: object) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be a non-string iterable")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{name} must be an iterable") from exc


def _validate_nonnegative_integer(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _validate_optional_nonnegative_integer(name: str, value: int | None) -> None:
    if value is not None:
        _validate_nonnegative_integer(name, value)


def _validate_optional_positive_integer(name: str, value: int | None) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
    ):
        raise ValueError(f"{name} must be a positive integer or None")
