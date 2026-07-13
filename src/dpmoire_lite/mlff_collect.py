from __future__ import annotations

import copy
from dataclasses import dataclass

from ase import Atoms

from .collect_models import (
    DedupStats,
    SourceDedupStats,
    _validate_relative_source_path,
)
from .dataset import atoms_from_mlab_configuration
from .mlab import MlabConfiguration, MlabIdentity, config_identity


@dataclass(frozen=True)
class ExactDedupFoldResult:
    accepted_configurations: tuple[MlabConfiguration, ...]
    accepted_frames: tuple[Atoms, ...]
    stats: DedupStats

    def __post_init__(self) -> None:
        configurations = _normalize_tuple(
            "accepted_configurations",
            self.accepted_configurations,
        )
        if any(
            not isinstance(configuration, MlabConfiguration)
            for configuration in configurations
        ):
            raise TypeError(
                "accepted_configurations must contain only MlabConfiguration values"
            )

        supplied_frames = _normalize_tuple("accepted_frames", self.accepted_frames)
        if any(not isinstance(frame, Atoms) for frame in supplied_frames):
            raise TypeError("accepted_frames must contain only ASE Atoms")
        frames = tuple(copy.deepcopy(frame) for frame in supplied_frames)

        if not isinstance(self.stats, DedupStats):
            raise TypeError("stats must be a DedupStats")
        if len(configurations) != self.stats.unique:
            raise ValueError(
                "accepted configuration count must equal stats.unique"
            )
        if len(frames) != self.stats.unique:
            raise ValueError("accepted frame count must equal stats.unique")

        object.__setattr__(self, "accepted_configurations", configurations)
        object.__setattr__(self, "accepted_frames", frames)


def fold_exact_configurations(sources) -> ExactDedupFoldResult:
    seen_digests: set[str] = set()
    accepted_configurations: list[MlabConfiguration] = []
    accepted_frames: list[Atoms] = []
    per_source: list[SourceDedupStats] = []

    for source_path, configurations in sources:
        _validate_relative_source_path(source_path)
        source_seen = 0
        source_retained = 0
        source_duplicates = 0

        for configuration in configurations:
            if not isinstance(configuration, MlabConfiguration):
                raise TypeError(
                    "sources must contain only MlabConfiguration values"
                )
            source_seen += 1
            identity = config_identity(configuration)
            if not isinstance(identity, MlabIdentity):
                raise TypeError("config_identity must return an MlabIdentity")
            if identity.schema != "mlab-config-v1":
                raise ValueError("config_identity must use mlab-config-v1")

            if identity.sha256 in seen_digests:
                source_duplicates += 1
                continue

            seen_digests.add(identity.sha256)
            source_retained += 1
            accepted_configurations.append(configuration)

            frame = atoms_from_mlab_configuration(configuration)
            if not isinstance(frame, Atoms):
                raise TypeError(
                    "atoms_from_mlab_configuration must return ASE Atoms"
                )
            frame.info.update(
                {
                    "dpmoire_source_path": source_path,
                    "dpmoire_source_configuration": (
                        configuration.source_configuration_number
                    ),
                    "dpmoire_source_line": configuration.source_line,
                }
            )
            accepted_frames.append(frame)

        per_source.append(
            SourceDedupStats(
                source_path=source_path,
                seen=source_seen,
                retained=source_retained,
                duplicates_removed=source_duplicates,
            )
        )

    stats = DedupStats(
        seen=sum(source.seen for source in per_source),
        unique=len(accepted_configurations),
        duplicates_removed=sum(
            source.duplicates_removed for source in per_source
        ),
        candidate_frame_count=len(accepted_frames),
        per_source=tuple(per_source),
    )
    return ExactDedupFoldResult(
        accepted_configurations=tuple(accepted_configurations),
        accepted_frames=tuple(accepted_frames),
        stats=stats,
    )


def _normalize_tuple(name: str, value: object) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be a non-string iterable")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{name} must be an iterable") from exc
