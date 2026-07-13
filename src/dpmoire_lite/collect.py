from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import warnings

from ase.io import ParseError

from .config import DPmoireLiteConfig, load_config
from .collect_models import SourceKind, SourceResult, SourceStatus
from .dataset import Dataset, count_ml_ab_configs
from .manifest import Manifest, ManifestReadResult, read_manifest, write_manifest
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
