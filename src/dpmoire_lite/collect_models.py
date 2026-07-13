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
        if len(frames) != source_accepted_count:
            raise ValueError(
                f"candidate frame count {len(frames)} does not equal "
                f"source accepted count {source_accepted_count}"
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
