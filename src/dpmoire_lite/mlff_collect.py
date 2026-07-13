from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import re

from ase import Atoms

from .collect_models import (
    DedupStats,
    MLFFCollectMode,
    SourceInventory,
    SourceDedupStats,
    _validate_relative_source_path,
)
from .dataset import atoms_from_mlab_configuration
from .manifest import MANIFEST_SCHEMA_VERSION, Manifest, ManifestReadResult
from .mlab import MlabConfiguration, MlabIdentity, config_identity
from .paths import relative_to_workdir


_MISSING_SCAN_WARNING = (
    "MD manifest is missing; full-dedup used a bounded direct-child legacy scan "
    "with unknown source coverage."
)
_NATURAL_TOKEN_RE = re.compile(r"(\d+)")


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


def build_mlff_source_inventory(
    *,
    work_dir: Path,
    mode: MLFFCollectMode,
    manifest: ManifestReadResult,
) -> SourceInventory:
    if not isinstance(mode, MLFFCollectMode):
        raise TypeError("mode must be an MLFFCollectMode")

    _validate_manifest_wrapper(manifest)

    if manifest.kind == "current":
        current_manifest = manifest.manifest
        if not isinstance(current_manifest, Manifest):
            raise ValueError("current manifest result must contain a Manifest")
        return SourceInventory(
            manifest_kind="current",
            directory_discovery="declared",
            directories=current_manifest.directories,
            coverage_known=True,
        )

    if manifest.kind == "legacy":
        raw_data = manifest.raw_data
        if not isinstance(raw_data, Mapping):
            raise ValueError("legacy manifest result must contain raw mapping data")
        directories = raw_data.get("directories", ())
        if not isinstance(directories, (list, tuple)):
            raise ValueError(
                "legacy manifest directories must be a sequence"
            )
        return SourceInventory(
            manifest_kind="legacy",
            directory_discovery="declared",
            directories=directories,
            coverage_known=True,
        )

    if mode is MLFFCollectMode.SEED_AWARE:
        raise ValueError("missing MD manifest cannot be used for seed-aware")
    return _scan_missing_inventory(Path(work_dir))


def _validate_manifest_wrapper(manifest: ManifestReadResult) -> None:
    if not isinstance(manifest, ManifestReadResult):
        raise TypeError("manifest must be a ManifestReadResult")

    if manifest.kind not in {"current", "legacy", "missing"}:
        raise ValueError("manifest has an unknown kind")

    if manifest.kind == "current":
        if not isinstance(manifest.manifest, Manifest):
            raise ValueError("current manifest result must contain a Manifest")
        if not isinstance(manifest.raw_data, Mapping):
            raise ValueError("current manifest result must contain raw mapping data")
        if manifest.manifest.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError("current manifest must use the current schema")
        if manifest.manifest.stage != "md":
            raise ValueError("current manifest stage must be md")
        if manifest.raw_data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ValueError("current manifest raw schema is inconsistent")
        if manifest.raw_data.get("stage") != "md":
            raise ValueError("current manifest raw stage is inconsistent")
        return

    if manifest.kind == "legacy":
        if manifest.manifest is not None:
            raise ValueError("legacy manifest result must not contain a Manifest")
        if not isinstance(manifest.raw_data, Mapping):
            raise ValueError("legacy manifest result must contain raw mapping data")
        if "stage" in manifest.raw_data and manifest.raw_data["stage"] != "md":
            raise ValueError("legacy manifest stage must be md")
        return

    if manifest.manifest is not None or manifest.raw_data is not None:
        raise ValueError("missing manifest result must contain no manifest data")


def _scan_missing_inventory(work_dir: Path) -> SourceInventory:
    md_root = work_dir / "md"
    _inventory_relative_path(work_dir, md_root)

    if not md_root.exists():
        return _missing_scan_inventory(())
    if not md_root.is_dir():
        raise NotADirectoryError(md_root)

    selected: list[tuple[str, str]] = []
    for child in md_root.iterdir():
        _inventory_relative_path(work_dir, child)
        if not child.is_dir():
            continue

        candidate = child / "ML_ABN"
        _inventory_relative_path(work_dir, candidate)
        if candidate.is_file():
            selected.append(
                (child.name, _inventory_relative_path(work_dir, child))
            )

    selected.sort(key=lambda item: (_natural_name_key(item[0]), item[0]))
    return _missing_scan_inventory(tuple(relative_path for _, relative_path in selected))


def _missing_scan_inventory(directories: tuple[str, ...]) -> SourceInventory:
    return SourceInventory(
        manifest_kind="missing",
        directory_discovery="legacy-scan",
        directories=directories,
        coverage_known=False,
        warnings=(_MISSING_SCAN_WARNING,),
    )


def _inventory_relative_path(work_dir: Path, path: Path) -> str:
    try:
        return relative_to_workdir(work_dir, path)
    except ValueError as exc:
        raise ValueError(f"path escapes work_dir: {path}") from exc


def _natural_name_key(name: str):
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in _NATURAL_TOKEN_RE.split(name)
    )


def _normalize_tuple(name: str, value: object) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be a non-string iterable")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{name} must be an iterable") from exc
