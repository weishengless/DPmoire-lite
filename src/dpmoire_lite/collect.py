from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import DPmoireLiteConfig, load_config
from .dataset import Dataset, count_ml_ab_configs
from .manifest import Manifest, read_manifest, write_manifest
from .outcar import find_outcar_series
from .paths import relative_to_workdir, stage_dir
from .structures import generate_stackings


COLLECT_OUTPUTS = {
    "rlx": "rlx_data.extxyz",
    "md": "MD_data.extxyz",
    "validation": "valid.extxyz",
}


def run_collect(config_path: Path, stage: str) -> None:
    if stage not in COLLECT_OUTPUTS:
        raise ValueError(f"Unknown collect stage: {stage}")

    config = load_config(config_path)
    manifest = read_manifest(config.work_dir, stage) or _new_manifest(config, stage)
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
    manifest.stage = stage
    if not manifest.directories:
        manifest.directories = _derived_directories(config, stage)
    manifest.collect = {}
    manifest.skipped = []
    manifest.failed = []
    return manifest


def _new_manifest(config: DPmoireLiteConfig, stage: str) -> Manifest:
    return Manifest(
        stage=stage,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        config_summary={
            "vasp_ml": config.vasp_ml,
            "outcar_collect_freq": config.outcar_collect_freq,
            "n_sectors": list(config.n_sectors),
            "include_monolayer_md": config.include_monolayer_md,
        },
        directories=_derived_directories(config, stage),
    )


def _derived_directories(config: DPmoireLiteConfig, stage: str) -> list[str]:
    if stage in {"rlx", "md"}:
        directories = [f"{stage}/{i}_{j}" for i, j in generate_stackings(config.n_sectors)]
        if stage == "md" and config.include_monolayer_md:
            directories.extend(["md/top_layer", "md/bot_layer"])
        return directories

    validation_root = stage_dir(config.work_dir, "validation")
    if not validation_root.exists():
        return []
    return [
        _display_path(config.work_dir, path)
        for path in sorted(validation_root.iterdir())
        if path.is_dir()
    ]


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
