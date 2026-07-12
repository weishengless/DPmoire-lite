from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import DPmoireLiteConfig, load_config
from .dataset import Dataset, count_ml_ab_configs
from .manifest import Manifest, read_manifest, write_manifest
from .outcar import find_outcar_series
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
