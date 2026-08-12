from dataclasses import replace
from importlib import import_module, util
from pathlib import Path
import shutil

import numpy as np
import pytest
import yaml

from dpmoire_lite import collect as collect_module
from dpmoire_lite import collect_models as models
from dpmoire_lite import dataset as dataset_module
from dpmoire_lite.manifest import (
    Manifest,
    ManifestReadResult,
    read_manifest,
    write_manifest,
)
from dpmoire_lite.mlab import config_identity, parse_mlab
from dpmoire_lite.paths import manifest_path


def _require(name):
    value = getattr(models, name, None)
    assert value is not None, f"{name} is not implemented"
    return value


def _exact_fold_api():
    spec = util.find_spec("dpmoire_lite.mlff_collect")
    assert spec is not None, "dpmoire_lite.mlff_collect is not implemented"
    module = import_module("dpmoire_lite.mlff_collect")
    fold = getattr(module, "fold_exact_configurations", None)
    assert callable(fold), "fold_exact_configurations is not implemented"
    return module, fold


def _inventory_api():
    module = import_module("dpmoire_lite.mlff_collect")
    builder = getattr(module, "build_mlff_source_inventory", None)
    assert callable(builder), "build_mlff_source_inventory is not implemented"
    return builder


def _full_dedup_source_api():
    collector = getattr(
        collect_module,
        "collect_full_dedup_mlab_sources",
        None,
    )
    assert callable(
        collector
    ), "collect_full_dedup_mlab_sources is not implemented"
    return collector


def _full_dedup_candidate_api():
    builder = getattr(
        collect_module,
        "build_full_dedup_candidate",
        None,
    )
    assert callable(
        builder
    ), "build_full_dedup_candidate is not implemented"
    return builder


_MLAB_FIXTURE_DIR = Path(__file__).parent / "data" / "mlab"
_MISSING_SCAN_WARNING = (
    "MD manifest is missing; full-dedup used a bounded direct-child legacy scan "
    "with unknown source coverage."
)


def _fixture_configuration(name: str):
    return parse_mlab(_MLAB_FIXTURE_DIR / name).configurations[0]


def _write_legacy_md_manifest(work_dir: Path, directories: list[str]) -> Path:
    path = manifest_path(work_dir, "md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "stage": "md",
                "generated_at": "2026-07-13T12:00:00",
                "directories": directories,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_placeholder_mlabn(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("synthetic inventory placeholder\n", encoding="utf-8")


def _copy_mlab_source(
    work_dir: Path,
    source_name: str,
    fixture_name: str,
    filename: str = "ML_ABN",
) -> Path:
    path = work_dir / "md" / source_name / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_MLAB_FIXTURE_DIR / fixture_name, path)
    return path


def _snapshot_work_tree(work_dir: Path):
    return {
        path.relative_to(work_dir).as_posix(): (
            path.is_dir(),
            path.read_bytes() if path.is_file() else None,
        )
        for path in work_dir.rglob("*")
    }


def test_mlff_collect_mode_values_are_stable():
    mode = _require("MLFFCollectMode")

    assert tuple(item.value for item in mode) == ("seed-aware", "full-dedup")
    assert mode.SEED_AWARE.value == "seed-aware"
    assert mode.FULL_DEDUP.value == "full-dedup"


def test_seed_aware_is_the_core_default():
    default_mode = _require("DEFAULT_MLFF_COLLECT_MODE")
    mode = _require("MLFFCollectMode")

    assert default_mode is mode.SEED_AWARE


def test_source_inventory_requires_normalized_ordered_relative_paths():
    inventory = _require("SourceInventory")

    with pytest.raises((TypeError, ValueError)):
        inventory(
            manifest_kind="current",
            directory_discovery="declared",
            directories="md/0_0",
            coverage_known=True,
        )

    value = inventory(
        manifest_kind="current",
        directory_discovery="declared",
        directories=["md/0_0", "md/0_0", "md/0_1"],
        coverage_known=True,
        warnings=["warning", "warning"],
    )
    assert value.directories == ("md/0_0", "md/0_0", "md/0_1")
    assert value.warnings == ("warning", "warning")

    for bad_path in ("", "../escape", r"md\0_0", "/absolute"):
        with pytest.raises((TypeError, ValueError)):
            inventory(
                manifest_kind="current",
                directory_discovery="declared",
                directories=[bad_path],
                coverage_known=True,
            )


def test_source_inventory_marks_declared_coverage_known():
    inventory = _require("SourceInventory")

    current = inventory(
        manifest_kind="current",
        directory_discovery="declared",
        directories=["md/0_0", "md/0_0"],
        coverage_known=True,
    )
    legacy = inventory(
        manifest_kind="legacy",
        directory_discovery="declared",
        directories=("md/0_0",),
        coverage_known=True,
    )
    missing = inventory(
        manifest_kind="missing",
        directory_discovery="legacy-scan",
        directories=[],
        coverage_known=False,
        warnings=("legacy scan",),
    )

    assert current.coverage_known is True
    assert current.directory_discovery == "declared"
    assert legacy.coverage_known is True
    assert legacy.directory_discovery == "declared"
    assert missing.coverage_known is False
    assert missing.directory_discovery == "legacy-scan"

    with pytest.raises((TypeError, ValueError)):
        inventory(
            manifest_kind="current",
            directory_discovery="legacy-scan",
            directories=[],
            coverage_known=False,
        )


def test_dedup_stats_require_seen_equal_unique_plus_duplicates():
    dedup_stats = _require("DedupStats")
    source_stats = _require("SourceDedupStats")

    source = source_stats(
        source_path="md/0_0/ML_ABN",
        seen=3,
        retained=2,
        duplicates_removed=1,
    )
    value = dedup_stats(
        seen=3,
        unique=2,
        duplicates_removed=1,
        candidate_frame_count=2,
        per_source=(source,),
    )
    assert value.seen == value.unique + value.duplicates_removed

    with pytest.raises((TypeError, ValueError)):
        dedup_stats(
            seen=3,
            unique=3,
            duplicates_removed=1,
            candidate_frame_count=3,
            per_source=(
                source_stats(
                    source_path="md/0_0/ML_ABN",
                    seen=3,
                    retained=3,
                    duplicates_removed=0,
                ),
            ),
        )


def test_dedup_stats_require_unique_equal_candidate_frames():
    dedup_stats = _require("DedupStats")
    source_stats = _require("SourceDedupStats")

    source = source_stats(
        source_path="md/0_0/ML_ABN",
        seen=2,
        retained=1,
        duplicates_removed=1,
    )

    with pytest.raises((TypeError, ValueError)):
        dedup_stats(
            seen=2,
            unique=1,
            duplicates_removed=1,
            candidate_frame_count=2,
            per_source=(source,),
        )

    value = dedup_stats(
        seen=2,
        unique=1,
        duplicates_removed=1,
        candidate_frame_count=1,
        per_source=(source,),
    )
    assert value.unique == value.candidate_frame_count == 1


def test_dedup_schema_is_mlab_config_v1():
    dedup_stats = _require("DedupStats")
    source_stats = _require("SourceDedupStats")

    source = source_stats(
        source_path="md/0_0/ML_ABN",
        seen=1,
        retained=1,
        duplicates_removed=0,
    )
    value = dedup_stats(
        seen=1,
        unique=1,
        duplicates_removed=0,
        candidate_frame_count=1,
        per_source=(source,),
    )
    assert value.schema == "mlab-config-v1"

    with pytest.raises((TypeError, ValueError)):
        dedup_stats(
            seen=1,
            unique=1,
            duplicates_removed=0,
            candidate_frame_count=1,
            per_source=(source,),
            schema="mlab-seed-v1",
        )


def test_full_dedup_removes_identical_configurations_within_one_source():
    _, fold = _exact_fold_api()

    configuration = _fixture_configuration("complete_vasp_651.mlab")
    result = fold(
        (("md/0_0/ML_ABN", (configuration, configuration)),),
    )

    assert result.accepted_configurations == (configuration,)
    assert len(result.accepted_frames) == 1
    assert result.stats.seen == 2
    assert result.stats.unique == 1
    assert result.stats.duplicates_removed == 1
    assert result.stats.candidate_frame_count == 1
    assert result.stats.per_source[0].retained == 1
    assert result.stats.per_source[0].duplicates_removed == 1


def test_full_dedup_removes_identical_configurations_across_sources():
    _, fold = _exact_fold_api()

    configuration = _fixture_configuration("complete_vasp_651.mlab")
    result = fold(
        (
            ("md/0_0/ML_ABN", (configuration,)),
            ("md/0_1/ML_ABN", (configuration,)),
        ),
    )

    assert len(result.accepted_configurations) == 1
    assert len(result.accepted_frames) == 1
    assert result.stats.seen == 2
    assert result.stats.unique == 1
    assert result.stats.duplicates_removed == 1
    assert result.stats.per_source[0].retained == 1
    assert result.stats.per_source[0].duplicates_removed == 0
    assert result.stats.per_source[1].retained == 0
    assert result.stats.per_source[1].duplicates_removed == 1


def test_full_dedup_keeps_deterministic_first_occurrence_and_provenance():
    _, fold = _exact_fold_api()

    configuration = _fixture_configuration("complete_vasp_651.mlab")
    first = replace(
        configuration,
        source_path=Path("private/parser/first.MLAB"),
        source_configuration_number=11,
        source_line=101,
    )
    second = replace(
        configuration,
        source_path=Path("private/parser/second.MLAB"),
        source_configuration_number=22,
        source_line=202,
    )
    result = fold(
        (
            ("md/first/ML_ABN", (first,)),
            ("md/second/ML_ABN", (second,)),
        ),
    )

    assert result.accepted_configurations[0] == first
    assert result.stats.unique == 1
    frame = result.accepted_frames[0]
    assert frame.info["dpmoire_source_path"] == "md/first/ML_ABN"
    assert frame.info["dpmoire_source_configuration"] == 11
    assert frame.info["dpmoire_source_line"] == 101
    assert not Path(frame.info["dpmoire_source_path"]).is_absolute()


def test_full_dedup_equivalent_float_text_uses_same_identity():
    _, fold = _exact_fold_api()

    format_a = _fixture_configuration("format_variant_a.mlab")
    format_b = _fixture_configuration("format_variant_b.mlab")
    result = fold(
        (
            ("md/format-a/ML_ABN", (format_a,)),
            ("md/format-b/ML_ABN", (format_b,)),
        ),
    )

    assert result.stats.seen == 2
    assert result.stats.unique == 1
    assert result.stats.duplicates_removed == 1
    assert result.accepted_configurations[0] == format_a


def test_full_dedup_keeps_changed_element_cell_position_energy_force_or_stress():
    _, fold = _exact_fold_api()

    configuration = _fixture_configuration("complete_vasp_651.mlab")
    element_variant = replace(configuration, elements=("S", "Pt"))
    cell_variant = replace(
        configuration,
        lattice=((4.5, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 12.0)),
    )
    position_variant = replace(
        configuration,
        positions=((0.25, 0.0, 1.0), (2.0, 2.0, 2.0)),
    )
    energy_variant = replace(configuration, energy=-1.0)
    force_variant = replace(
        configuration,
        forces=((0.2, 0.0, 0.0), (-0.1, 0.0, 0.0)),
    )
    stress_variant = replace(
        configuration,
        stress_kbar=(1.5, 2.0, 3.0, 4.0, 5.0, 6.0),
    )
    configurations = (
        configuration,
        element_variant,
        cell_variant,
        position_variant,
        energy_variant,
        force_variant,
        stress_variant,
    )
    result = fold((("md/variants/ML_ABN", configurations),))

    assert result.accepted_configurations == configurations
    assert len(result.accepted_frames) == 7
    assert result.stats.seen == 7
    assert result.stats.unique == 7
    assert result.stats.duplicates_removed == 0


def test_full_dedup_normalizes_negative_zero():
    _, fold = _exact_fold_api()

    configuration = _fixture_configuration("complete_vasp_651.mlab")
    negative_zero = replace(
        configuration,
        positions=((-0.0, 0.0, 1.0), (2.0, 2.0, 2.0)),
    )
    result = fold(
        (("md/negative-zero/ML_ABN", (configuration, negative_zero)),),
    )

    assert len(result.accepted_configurations) == 1
    assert len(result.accepted_frames) == 1
    assert result.stats.seen == 2
    assert result.stats.unique == 1
    assert result.stats.duplicates_removed == 1


def test_duplicate_is_not_converted_to_ase(monkeypatch):
    module, fold = _exact_fold_api()

    real_converter = dataset_module.atoms_from_mlab_configuration
    calls = []

    def spy(configuration):
        calls.append(configuration)
        return real_converter(configuration)

    monkeypatch.setattr(module, "atoms_from_mlab_configuration", spy)
    configuration = _fixture_configuration("complete_vasp_651.mlab")
    result = fold(
        (("md/converter/ML_ABN", (configuration, configuration)),),
    )

    assert len(calls) == 1
    assert len(result.accepted_frames) == 1
    expected = real_converter(configuration)
    frame = result.accepted_frames[0]
    assert frame.get_chemical_symbols() == expected.get_chemical_symbols()
    np.testing.assert_allclose(frame.cell.array, expected.cell.array)
    np.testing.assert_allclose(frame.positions, expected.positions)
    assert frame.get_potential_energy() == pytest.approx(
        expected.get_potential_energy()
    )
    np.testing.assert_allclose(frame.get_forces(), expected.get_forces())
    np.testing.assert_allclose(frame.get_stress(), expected.get_stress())


def test_identity_called_once_per_accepted_complete_configuration(monkeypatch):
    module, fold = _exact_fold_api()

    real_identity = config_identity
    calls = []

    def spy(configuration):
        calls.append(configuration)
        return real_identity(configuration)

    monkeypatch.setattr(module, "config_identity", spy)
    configuration = _fixture_configuration("complete_vasp_651.mlab")
    changed = replace(configuration, energy=-0.75)
    result = fold(
        (("md/identity/ML_ABN", (configuration, configuration, changed)),),
    )

    assert len(calls) == 3
    assert result.stats.seen == 3
    assert result.stats.unique == 2
    assert result.stats.duplicates_removed == 1
    assert len(result.accepted_frames) == 2


def test_current_manifest_inventory_uses_declared_order(tmp_path):
    build_inventory = _inventory_api()

    work_dir = tmp_path / "work"
    directories = ["md/0_10", "md/0_2", "md/0_2", "md/0_1"]
    write_manifest(
        work_dir,
        Manifest(
            stage="md",
            generated_at="2026-07-13T12:00:00",
            directories=directories,
        ),
    )
    manifest_file = manifest_path(work_dir, "md")
    original_bytes = manifest_file.read_bytes()
    _write_placeholder_mlabn(work_dir / "md" / "decoy" / "ML_ABN")

    manifest = read_manifest(work_dir, "md")
    inventory = build_inventory(
        work_dir=work_dir,
        mode=models.MLFFCollectMode.SEED_AWARE,
        manifest=manifest,
    )

    assert manifest.kind == "current"
    assert inventory.manifest_kind == "current"
    assert inventory.directory_discovery == "declared"
    assert inventory.coverage_known is True
    assert inventory.directories == tuple(directories)
    assert inventory.warnings == ()
    assert "md/decoy" not in inventory.directories
    assert manifest_file.read_bytes() == original_bytes


def test_legacy_manifest_inventory_uses_validated_declared_order(tmp_path):
    build_inventory = _inventory_api()

    work_dir = tmp_path / "work"
    directories = ["md/0_10", "md/0_2", "md/0_2", "md/0_1"]
    manifest_file = _write_legacy_md_manifest(work_dir, directories)
    original_bytes = manifest_file.read_bytes()
    _write_placeholder_mlabn(work_dir / "md" / "scan-decoy" / "ML_ABN")

    manifest = read_manifest(work_dir, "md")
    inventory = build_inventory(
        work_dir=work_dir,
        mode=models.MLFFCollectMode.SEED_AWARE,
        manifest=manifest,
    )

    assert manifest.kind == "legacy"
    assert manifest.manifest is None
    assert inventory.manifest_kind == "legacy"
    assert inventory.directory_discovery == "declared"
    assert inventory.coverage_known is True
    assert inventory.directories == tuple(directories)
    assert inventory.warnings == ()
    assert "md/scan-decoy" not in inventory.directories
    assert manifest_file.read_bytes() == original_bytes


def test_missing_manifest_is_rejected_for_seed_aware(tmp_path):
    build_inventory = _inventory_api()

    work_dir = tmp_path / "work"
    _write_placeholder_mlabn(work_dir / "md" / "0_0" / "ML_ABN")
    manifest = read_manifest(work_dir, "md")

    assert manifest.kind == "missing"
    with pytest.raises(ValueError):
        build_inventory(
            work_dir=work_dir,
            mode=models.MLFFCollectMode.SEED_AWARE,
            manifest=manifest,
        )
    assert not manifest_path(work_dir, "md").exists()


def test_missing_manifest_full_dedup_scans_direct_md_children(tmp_path):
    build_inventory = _inventory_api()

    work_dir = tmp_path / "work"
    for name in ("0_10", "0_2", "0_1"):
        _write_placeholder_mlabn(work_dir / "md" / name / "ML_ABN")
    manifest = read_manifest(work_dir, "md")

    inventory = build_inventory(
        work_dir=work_dir,
        mode=models.MLFFCollectMode.FULL_DEDUP,
        manifest=manifest,
    )

    assert inventory.manifest_kind == "missing"
    assert inventory.directory_discovery == "legacy-scan"
    assert inventory.coverage_known is False
    assert inventory.directories == ("md/0_1", "md/0_2", "md/0_10")
    assert not manifest_path(work_dir, "md").exists()


def test_legacy_scan_uses_natural_order(tmp_path):
    build_inventory = _inventory_api()

    work_dir = tmp_path / "work"
    for name in ("0_10", "0_2", "0_1"):
        _write_placeholder_mlabn(work_dir / "md" / name / "ML_ABN")
    manifest = read_manifest(work_dir, "md")

    inventory = build_inventory(
        work_dir=work_dir,
        mode=models.MLFFCollectMode.FULL_DEDUP,
        manifest=manifest,
    )

    assert inventory.directories == ("md/0_1", "md/0_2", "md/0_10")


def test_legacy_scan_accepts_named_monolayer_directory(tmp_path):
    build_inventory = _inventory_api()

    work_dir = tmp_path / "work"
    _write_placeholder_mlabn(work_dir / "md" / "monolayer" / "ML_ABN")
    manifest = read_manifest(work_dir, "md")

    inventory = build_inventory(
        work_dir=work_dir,
        mode=models.MLFFCollectMode.FULL_DEDUP,
        manifest=manifest,
    )

    assert inventory.directories == ("md/monolayer",)


def test_legacy_scan_requires_exact_mlabn_filename(tmp_path):
    build_inventory = _inventory_api()

    work_dir = tmp_path / "work"
    md_root = work_dir / "md"
    _write_placeholder_mlabn(md_root / "only-ml-ab" / "ML_AB")
    _write_placeholder_mlabn(md_root / "suffix-zero" / "ML_ABN0")
    _write_placeholder_mlabn(md_root / "suffix-one" / "ML_ABN1")
    _write_placeholder_mlabn(md_root / "backup" / "ML_ABN.bak")
    (md_root / "mlabn-directory" / "ML_ABN").mkdir(parents=True)
    _write_placeholder_mlabn(md_root / "legal" / "ML_ABN")
    manifest = read_manifest(work_dir, "md")

    inventory = build_inventory(
        work_dir=work_dir,
        mode=models.MLFFCollectMode.FULL_DEDUP,
        manifest=manifest,
    )

    assert inventory.directories == ("md/legal",)


def test_legacy_scan_ignores_files_backups_and_nested_descendants(tmp_path):
    build_inventory = _inventory_api()

    work_dir = tmp_path / "work"
    md_root = work_dir / "md"
    md_root.mkdir(parents=True, exist_ok=True)
    (md_root / "root-ML_ABN").write_text("not a directory\n", encoding="utf-8")
    (md_root / "backup.extxyz").write_text("backup\n", encoding="utf-8")
    _write_placeholder_mlabn(md_root / "outer" / "nested" / "ML_ABN")
    _write_placeholder_mlabn(md_root / "legal" / "ML_ABN")
    manifest = read_manifest(work_dir, "md")

    inventory = build_inventory(
        work_dir=work_dir,
        mode=models.MLFFCollectMode.FULL_DEDUP,
        manifest=manifest,
    )

    assert inventory.directories == ("md/legal",)


def test_legacy_scan_rejects_resolved_path_escape(tmp_path, monkeypatch):
    build_inventory = _inventory_api()

    work_dir = tmp_path / "work"
    escaped_directory = work_dir / "md" / "escaped"
    escaped_mlabn = escaped_directory / "ML_ABN"
    _write_placeholder_mlabn(escaped_mlabn)
    manifest = read_manifest(work_dir, "md")
    outside = (tmp_path / "outside").resolve()
    real_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path in {escaped_directory, escaped_mlabn}:
            return outside / path.name
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)

    with pytest.raises(ValueError, match="work_dir|escape"):
        build_inventory(
            work_dir=work_dir,
            mode=models.MLFFCollectMode.FULL_DEDUP,
            manifest=manifest,
        )


def test_invalid_or_unsupported_manifest_never_falls_back_to_scan(tmp_path):
    build_inventory = _inventory_api()

    work_dir = tmp_path / "work"
    _write_placeholder_mlabn(work_dir / "md" / "direct" / "ML_ABN")
    manifest_file = manifest_path(work_dir, "md")
    manifest_file.parent.mkdir(parents=True, exist_ok=True)

    manifest_file.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "stage": "md",
                "directories": "md/direct",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        read_manifest(work_dir, "md")

    manifest_file.write_text(
        yaml.safe_dump(
            {"schema_version": 99, "stage": "md", "directories": []},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        read_manifest(work_dir, "md")

    with pytest.raises(ValueError):
        build_inventory(
            work_dir=work_dir,
            mode=models.MLFFCollectMode.FULL_DEDUP,
            manifest=ManifestReadResult(kind="unknown"),
        )
    with pytest.raises(ValueError):
        build_inventory(
            work_dir=work_dir,
            mode=models.MLFFCollectMode.FULL_DEDUP,
            manifest=ManifestReadResult(
                kind="current",
                manifest=None,
                raw_data={"schema_version": 2, "stage": "md"},
            ),
        )
    with pytest.raises(ValueError, match="directories.*sequence"):
        build_inventory(
            work_dir=work_dir,
            mode=models.MLFFCollectMode.FULL_DEDUP,
            manifest=ManifestReadResult(
                kind="legacy",
                manifest=None,
                raw_data={
                    "stage": "md",
                    "directories": {"md/direct": True},
                },
            ),
        )


def test_missing_scan_marks_coverage_unknown_and_records_warning(tmp_path):
    build_inventory = _inventory_api()

    work_dir = tmp_path / "missing-work"
    manifest = read_manifest(work_dir, "md")
    before = tuple(
        sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    )

    inventory = build_inventory(
        work_dir=work_dir,
        mode=models.MLFFCollectMode.FULL_DEDUP,
        manifest=manifest,
    )

    after = tuple(
        sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    )
    assert inventory.directories == ()
    assert inventory.manifest_kind == "missing"
    assert inventory.directory_discovery == "legacy-scan"
    assert inventory.coverage_known is False
    assert inventory.warnings == (_MISSING_SCAN_WARNING,)
    assert not manifest_path(work_dir, "md").exists()
    assert before == after


def test_full_dedup_complete_source_commits_unique_configurations(tmp_path):
    collect_full_dedup_mlab_sources = _full_dedup_source_api()

    work_dir = tmp_path / "work"
    final_path = _copy_mlab_source(
        work_dir,
        "complete",
        "complete_multi.mlab",
    )
    result = collect_full_dedup_mlab_sources(
        work_dir=work_dir,
        source_paths=(final_path,),
    )

    source_result = result.source_results[0]
    assert [item.source_path for item in result.source_results] == [
        "md/complete/ML_ABN"
    ]
    assert source_result.status is models.SourceStatus.COMPLETE
    assert source_result.complete_count == 2
    assert source_result.accepted_count == 2
    assert len(source_result.parsed_configurations) == 2
    assert result.dedup.accepted_configurations == (
        source_result.parsed_configurations
    )
    assert len(result.dedup.accepted_frames) == 2
    assert isinstance(
        result.dedup,
        import_module("dpmoire_lite.mlff_collect").ExactDedupFoldResult,
    )

    stats = result.dedup.stats
    assert (stats.seen, stats.unique, stats.duplicates_removed) == (2, 2, 0)
    assert stats.candidate_frame_count == 2
    per_source = stats.per_source[0]
    assert per_source.source_path == source_result.source_path
    assert (per_source.seen, per_source.retained, per_source.duplicates_removed) == (
        2,
        2,
        0,
    )


def test_full_dedup_partial_source_commits_only_complete_prefix(tmp_path):
    collect_full_dedup_mlab_sources = _full_dedup_source_api()

    work_dir = tmp_path / "work"
    final_path = _copy_mlab_source(
        work_dir,
        "partial",
        "tail_position_crop.mlab",
    )
    result = collect_full_dedup_mlab_sources(
        work_dir=work_dir,
        source_paths=(final_path,),
    )

    source_result = result.source_results[0]
    assert source_result.status is models.SourceStatus.PARTIAL
    assert source_result.complete_count == 1
    assert source_result.accepted_count == 1
    assert len(source_result.parsed_configurations) == 1
    assert source_result.discarded_configuration_number == 2
    assert source_result.discarded_block == "positions"
    assert source_result.reason
    assert result.dedup.accepted_configurations == (
        source_result.parsed_configurations
    )
    assert (result.dedup.stats.seen, result.dedup.stats.unique) == (1, 1)
    assert result.dedup.stats.duplicates_removed == 0
    assert result.dedup.stats.per_source[0].source_path == source_result.source_path
    assert (
        result.dedup.stats.per_source[0].seen,
        result.dedup.stats.per_source[0].retained,
        result.dedup.stats.per_source[0].duplicates_removed,
    ) == (1, 1, 0)


def test_full_dedup_failed_source_commits_no_frames_or_identities(
    tmp_path,
    monkeypatch,
):
    collect_full_dedup_mlab_sources = _full_dedup_source_api()

    module = import_module("dpmoire_lite.mlff_collect")
    real_identity = module.config_identity
    identity_calls = []

    def spy(configuration):
        identity_calls.append(configuration)
        return real_identity(configuration)

    monkeypatch.setattr(module, "config_identity", spy)
    work_dir = tmp_path / "work"
    final_path = _copy_mlab_source(
        work_dir,
        "failed",
        "internal_corruption.mlab",
    )
    result = collect_full_dedup_mlab_sources(
        work_dir=work_dir,
        source_paths=(final_path,),
    )

    source_result = result.source_results[0]
    assert source_result.status is models.SourceStatus.FAILED
    assert source_result.complete_count == 0
    assert source_result.accepted_count == 0
    assert source_result.parsed_configurations == ()
    assert source_result.accepted_frames == ()
    assert source_result.reason
    assert identity_calls == []
    assert len(result.dedup.accepted_frames) == 0
    assert len(result.dedup.accepted_configurations) == 0
    assert (
        result.dedup.stats.seen,
        result.dedup.stats.unique,
        result.dedup.stats.duplicates_removed,
        result.dedup.stats.candidate_frame_count,
    ) == (0, 0, 0, 0)
    assert result.dedup.stats.per_source[0].source_path == source_result.source_path
    assert (
        result.dedup.stats.per_source[0].seen,
        result.dedup.stats.per_source[0].retained,
        result.dedup.stats.per_source[0].duplicates_removed,
    ) == (0, 0, 0)


def test_failed_source_identity_does_not_hide_later_valid_frame(
    tmp_path,
    monkeypatch,
):
    collect_full_dedup_mlab_sources = _full_dedup_source_api()

    module = import_module("dpmoire_lite.mlff_collect")
    real_identity = module.config_identity
    identity_calls = []

    def spy(configuration):
        identity_calls.append(configuration)
        return real_identity(configuration)

    monkeypatch.setattr(module, "config_identity", spy)
    work_dir = tmp_path / "work"
    failed_path = _copy_mlab_source(
        work_dir,
        "failed",
        "internal_corruption.mlab",
    )
    valid_path = _copy_mlab_source(
        work_dir,
        "valid",
        "complete_multi.mlab",
    )
    result = collect_full_dedup_mlab_sources(
        work_dir=work_dir,
        source_paths=(failed_path, valid_path),
    )

    source_results = result.source_results
    assert [source_result.source_path for source_result in source_results] == [
        "md/failed/ML_ABN",
        "md/valid/ML_ABN",
    ]
    assert [source_result.status for source_result in source_results] == [
        models.SourceStatus.FAILED,
        models.SourceStatus.COMPLETE,
    ]
    assert len(identity_calls) == 2
    assert source_results[0].accepted_count == 0
    assert source_results[1].accepted_count == 2
    assert result.dedup.accepted_configurations == (
        source_results[1].parsed_configurations
    )
    assert len(result.dedup.accepted_frames) == 2
    assert (result.dedup.stats.seen, result.dedup.stats.unique) == (2, 2)
    assert result.dedup.stats.duplicates_removed == 0
    assert [
        (item.seen, item.retained, item.duplicates_removed)
        for item in result.dedup.stats.per_source
    ] == [(0, 0, 0), (2, 2, 0)]


def test_full_dedup_common_seed_is_retained_once(tmp_path):
    collect_full_dedup_mlab_sources = _full_dedup_source_api()

    work_dir = tmp_path / "work"
    seed_path = _copy_mlab_source(
        work_dir,
        "seed",
        "complete_vasp_651.mlab",
    )
    restart_path = _copy_mlab_source(
        work_dir,
        "restart",
        "complete_multi.mlab",
    )
    result = collect_full_dedup_mlab_sources(
        work_dir=work_dir,
        source_paths=(seed_path, restart_path),
    )

    source_results = result.source_results
    assert [source_result.status for source_result in source_results] == [
        models.SourceStatus.COMPLETE,
        models.SourceStatus.COMPLETE,
    ]
    assert [source_result.accepted_count for source_result in source_results] == [
        1,
        2,
    ]
    assert config_identity(
        source_results[0].parsed_configurations[0]
    ).sha256 == config_identity(source_results[1].parsed_configurations[0]).sha256
    assert result.dedup.accepted_configurations == (
        source_results[0].parsed_configurations[0],
        source_results[1].parsed_configurations[1],
    )
    assert (
        result.dedup.stats.seen,
        result.dedup.stats.unique,
        result.dedup.stats.duplicates_removed,
    ) == (3, 2, 1)
    assert [
        (item.seen, item.retained, item.duplicates_removed)
        for item in result.dedup.stats.per_source
    ] == [(1, 1, 0), (2, 1, 1)]


def test_full_dedup_fresh_start_retains_every_unique_final_configuration(
    tmp_path,
):
    collect_full_dedup_mlab_sources = _full_dedup_source_api()

    work_dir = tmp_path / "work"
    final_path = _copy_mlab_source(
        work_dir,
        "fresh",
        "complete_multi.mlab",
    )
    result = collect_full_dedup_mlab_sources(
        work_dir=work_dir,
        source_paths=(final_path,),
    )

    source_result = result.source_results[0]
    assert source_result.status is models.SourceStatus.COMPLETE
    assert source_result.complete_count == 2
    assert source_result.accepted_count == 2
    assert result.dedup.accepted_configurations == (
        source_result.parsed_configurations
    )
    assert (
        result.dedup.stats.seen,
        result.dedup.stats.unique,
        result.dedup.stats.duplicates_removed,
    ) == (2, 2, 0)
    assert len(result.dedup.accepted_frames) == 2


def test_restart_time_ml_ab_count_is_never_used_by_full_dedup(
    tmp_path,
):
    collect_full_dedup_mlab_sources = _full_dedup_source_api()

    work_dir = tmp_path / "work"
    final_path = _copy_mlab_source(
        work_dir,
        "restart",
        "complete_multi.mlab",
    )
    current_ml_ab = _copy_mlab_source(
        work_dir,
        "restart",
        "complete_vasp_651.mlab",
        filename="ML_AB",
    )
    current_ml_ab.write_bytes(b"unrelated restart input must not be parsed\n")
    before = current_ml_ab.read_bytes()

    result = collect_full_dedup_mlab_sources(
        work_dir=work_dir,
        source_paths=(final_path,),
    )

    assert result.source_results[0].status is models.SourceStatus.COMPLETE
    assert result.source_results[0].accepted_count == 2
    assert result.dedup.stats.seen == 2
    assert result.dedup.stats.unique == 2
    assert current_ml_ab.read_bytes() == before


def test_full_dedup_reads_only_final_mlabn(tmp_path, monkeypatch):
    collect_full_dedup_mlab_sources = _full_dedup_source_api()

    work_dir = tmp_path / "work"
    final_path = _copy_mlab_source(
        work_dir,
        "final-only",
        "complete_multi.mlab",
    )
    _copy_mlab_source(
        work_dir,
        "final-only",
        "complete_vasp_651.mlab",
        filename="ML_AB",
    )
    _copy_mlab_source(
        work_dir,
        "final-only",
        "complete_vasp_651.mlab",
        filename="ML_ABN0",
    )
    _copy_mlab_source(
        work_dir,
        "final-only",
        "complete_vasp_651.mlab",
        filename="ML_ABN1",
    )

    real_parse = collect_module.parse_mlab
    parsed_paths = []

    def spy(path):
        parsed_paths.append(Path(path))
        return real_parse(path)

    monkeypatch.setattr(collect_module, "parse_mlab", spy)
    result = collect_full_dedup_mlab_sources(
        work_dir=work_dir,
        source_paths=(final_path,),
    )

    assert parsed_paths == [final_path]
    assert result.source_results[0].source_path == "md/final-only/ML_ABN"
    assert result.source_results[0].status is models.SourceStatus.COMPLETE
    assert result.dedup.stats.seen == 2
    assert result.dedup.stats.unique == 2


def test_distinct_seed_configurations_are_retained(tmp_path):
    collect_full_dedup_mlab_sources = _full_dedup_source_api()

    work_dir = tmp_path / "work"
    first_path = _copy_mlab_source(
        work_dir,
        "first",
        "complete_vasp_641.mlab",
    )
    second_path = _copy_mlab_source(
        work_dir,
        "second",
        "complete_vasp_651.mlab",
    )
    first_configuration = _fixture_configuration("complete_vasp_641.mlab")
    second_configuration = _fixture_configuration("complete_vasp_651.mlab")
    assert config_identity(first_configuration).sha256 != config_identity(
        second_configuration
    ).sha256

    result = collect_full_dedup_mlab_sources(
        work_dir=work_dir,
        source_paths=(first_path, second_path),
    )

    assert [source_result.source_path for source_result in result.source_results] == [
        "md/first/ML_ABN",
        "md/second/ML_ABN",
    ]
    assert [source_result.status for source_result in result.source_results] == [
        models.SourceStatus.COMPLETE,
        models.SourceStatus.COMPLETE,
    ]
    assert result.dedup.accepted_configurations == (
        result.source_results[0].parsed_configurations[0],
        result.source_results[1].parsed_configurations[0],
    )
    assert (
        result.dedup.stats.seen,
        result.dedup.stats.unique,
        result.dedup.stats.duplicates_removed,
    ) == (2, 2, 0)
    assert [
        (item.seen, item.retained, item.duplicates_removed)
        for item in result.dedup.stats.per_source
    ] == [(1, 1, 0), (1, 1, 0)]


def test_full_dedup_candidate_preserves_inventory_order(tmp_path):
    build_full_dedup_candidate = _full_dedup_candidate_api()

    work_dir = tmp_path / "work"
    directories = ["md/z-last", "md/a-first", "md/z-last"]
    write_manifest(
        work_dir,
        Manifest(
            stage="md",
            generated_at="2026-07-13T12:00:00",
            directories=directories,
        ),
    )
    _copy_mlab_source(work_dir, "z-last", "complete_vasp_651.mlab")
    _copy_mlab_source(work_dir, "a-first", "complete_vasp_651.mlab")

    candidate = build_full_dedup_candidate(
        work_dir=work_dir,
        manifest=read_manifest(work_dir, "md"),
    )

    assert isinstance(candidate, models.CollectionCandidate)
    assert candidate.expected_directories == tuple(directories)
    assert candidate.source_inventory.directories == tuple(directories)
    assert candidate.source_inventory.manifest_kind == "current"
    expected_paths = (
        "md/z-last/ML_ABN",
        "md/a-first/ML_ABN",
        "md/z-last/ML_ABN",
    )
    assert [item.source_path for item in candidate.source_results] == list(
        expected_paths
    )
    assert [item.source_path for item in candidate.dedup_stats.per_source] == list(
        expected_paths
    )

    with pytest.raises((TypeError, ValueError)):
        replace(
            candidate,
            expected_directories=("md/a-first", "md/z-last", "md/z-last"),
        )


def test_full_dedup_candidate_counts_match_source_sums(tmp_path):
    build_full_dedup_candidate = _full_dedup_candidate_api()

    work_dir = tmp_path / "work"
    directories = [
        "md/complete",
        "md/partial",
        "md/failed",
        "md/missing-dir",
        "md/no-file",
    ]
    write_manifest(
        work_dir,
        Manifest(
            stage="md",
            generated_at="2026-07-13T12:00:00",
            directories=directories,
        ),
    )
    _copy_mlab_source(work_dir, "complete", "complete_multi.mlab")
    _copy_mlab_source(work_dir, "partial", "tail_position_crop.mlab")
    _copy_mlab_source(work_dir, "failed", "internal_corruption.mlab")
    (work_dir / "md" / "no-file").mkdir(parents=True, exist_ok=True)

    candidate = build_full_dedup_candidate(
        work_dir=work_dir,
        manifest=read_manifest(work_dir, "md"),
    )
    statuses = [item.status for item in candidate.source_results]
    assert statuses == [
        models.SourceStatus.COMPLETE,
        models.SourceStatus.PARTIAL,
        models.SourceStatus.FAILED,
        models.SourceStatus.SKIPPED,
        models.SourceStatus.SKIPPED,
    ]
    assert len(candidate.source_results) == len(candidate.dedup_stats.per_source)

    for source_result, source_stats in zip(
        candidate.source_results,
        candidate.dedup_stats.per_source,
    ):
        if source_result.status in {
            models.SourceStatus.COMPLETE,
            models.SourceStatus.PARTIAL,
        }:
            assert source_stats.seen == source_result.accepted_count
        else:
            assert (
                source_stats.seen,
                source_stats.retained,
                source_stats.duplicates_removed,
            ) == (0, 0, 0)

    assert candidate.dedup_stats.seen == sum(
        item.accepted_count for item in candidate.source_results
    )
    assert candidate.dedup_stats.unique == sum(
        item.retained for item in candidate.dedup_stats.per_source
    )
    assert candidate.dedup_stats.duplicates_removed == sum(
        item.duplicates_removed for item in candidate.dedup_stats.per_source
    )
    assert candidate.expected_directory_count == 5
    assert candidate.sources_complete == 1
    assert candidate.sources_partial == 1
    assert candidate.sources_failed == 1
    assert candidate.sources_skipped == 2
    assert candidate.sources_attempted == 3

    bad_per_source = tuple(
        replace(stats, source_path="md/not-aligned/ML_ABN")
        if index == 0
        else stats
        for index, stats in enumerate(candidate.dedup_stats.per_source)
    )
    bad_stats = models.DedupStats(
        seen=candidate.dedup_stats.seen,
        unique=candidate.dedup_stats.unique,
        duplicates_removed=candidate.dedup_stats.duplicates_removed,
        candidate_frame_count=candidate.dedup_stats.candidate_frame_count,
        per_source=bad_per_source,
        schema=candidate.dedup_stats.schema,
    )
    with pytest.raises((TypeError, ValueError)):
        replace(candidate, dedup_stats=bad_stats)


def test_full_dedup_candidate_frames_equal_unique_count(tmp_path):
    build_full_dedup_candidate = _full_dedup_candidate_api()

    work_dir = tmp_path / "work"
    directories = ["md/seed", "md/restart"]
    write_manifest(
        work_dir,
        Manifest(
            stage="md",
            generated_at="2026-07-13T12:00:00",
            directories=directories,
        ),
    )
    _copy_mlab_source(work_dir, "seed", "complete_vasp_651.mlab")
    _copy_mlab_source(work_dir, "restart", "complete_multi.mlab")

    candidate = build_full_dedup_candidate(
        work_dir=work_dir,
        manifest=read_manifest(work_dir, "md"),
    )
    stats = candidate.dedup_stats
    assert (stats.seen, stats.unique, stats.duplicates_removed) == (3, 2, 1)
    assert candidate.frame_count == len(candidate.accepted_frames)
    assert candidate.frame_count == stats.unique
    assert candidate.frame_count == stats.candidate_frame_count
    assert len(candidate.accepted_frames) == 2
    assert candidate.accepted_frames[0].info["dpmoire_source_path"] == (
        "md/seed/ML_ABN"
    )

    with pytest.raises((TypeError, ValueError)):
        replace(candidate, accepted_frames=candidate.accepted_frames[:-1])
    with pytest.raises((TypeError, ValueError)):
        replace(
            candidate,
            accepted_frames=candidate.accepted_frames
            + (candidate.accepted_frames[0],),
        )


def test_duplicates_alone_do_not_mark_source_or_candidate_degraded(tmp_path):
    build_full_dedup_candidate = _full_dedup_candidate_api()

    work_dir = tmp_path / "work"
    directories = ["md/one", "md/two"]
    write_manifest(
        work_dir,
        Manifest(
            stage="md",
            generated_at="2026-07-13T12:00:00",
            directories=directories,
        ),
    )
    _copy_mlab_source(work_dir, "one", "complete_vasp_651.mlab")
    _copy_mlab_source(work_dir, "two", "complete_vasp_651.mlab")

    candidate = build_full_dedup_candidate(
        work_dir=work_dir,
        manifest=read_manifest(work_dir, "md"),
    )

    assert [item.status for item in candidate.source_results] == [
        models.SourceStatus.COMPLETE,
        models.SourceStatus.COMPLETE,
    ]
    assert candidate.sources_complete == 2
    assert candidate.sources_partial == 0
    assert candidate.sources_skipped == 0
    assert candidate.sources_failed == 0
    assert candidate.source_inventory.coverage_known is True
    assert (
        candidate.dedup_stats.seen,
        candidate.dedup_stats.unique,
        candidate.dedup_stats.duplicates_removed,
    ) == (2, 1, 1)
    assert not hasattr(candidate, "status")
    assert not hasattr(candidate, "collect_status")


def test_missing_scan_candidate_preserves_coverage_unknown(tmp_path):
    build_full_dedup_candidate = _full_dedup_candidate_api()

    work_dir = tmp_path / "work"
    for name in ("0_10", "0_2", "0_1"):
        _copy_mlab_source(work_dir, name, "complete_vasp_651.mlab")

    candidate = build_full_dedup_candidate(
        work_dir=work_dir,
        manifest=read_manifest(work_dir, "md"),
    )

    assert candidate.source_inventory.manifest_kind == "missing"
    assert candidate.source_inventory.directory_discovery == "legacy-scan"
    assert candidate.source_inventory.coverage_known is False
    assert candidate.source_inventory.warnings == (_MISSING_SCAN_WARNING,)
    assert candidate.expected_directories == (
        "md/0_1",
        "md/0_2",
        "md/0_10",
    )
    assert [item.source_path for item in candidate.source_results] == [
        "md/0_1/ML_ABN",
        "md/0_2/ML_ABN",
        "md/0_10/ML_ABN",
    ]
    assert [item.source_path for item in candidate.dedup_stats.per_source] == [
        "md/0_1/ML_ABN",
        "md/0_2/ML_ABN",
        "md/0_10/ML_ABN",
    ]
    assert not manifest_path(work_dir, "md").exists()


def test_candidate_records_input_layout_discovery_mode_and_schema(tmp_path):
    build_full_dedup_candidate = _full_dedup_candidate_api()

    work_dir = tmp_path / "work"
    directories = ["md/legacy"]
    manifest_file = _write_legacy_md_manifest(work_dir, directories)
    original_bytes = manifest_file.read_bytes()
    _copy_mlab_source(work_dir, "legacy", "complete_vasp_651.mlab")
    manifest = read_manifest(work_dir, "md")

    candidate = build_full_dedup_candidate(
        work_dir=work_dir,
        manifest=manifest,
    )

    assert candidate.collection_mode is models.MLFFCollectMode.FULL_DEDUP
    assert candidate.source_inventory.manifest_kind == "legacy"
    assert candidate.source_inventory.directory_discovery == "declared"
    assert candidate.source_inventory.coverage_known is True
    assert candidate.source_inventory.warnings == ()
    assert candidate.dedup_stats.schema == "mlab-config-v1"
    assert candidate.expected_directories == tuple(directories)
    assert manifest_file.read_bytes() == original_bytes

    with pytest.raises((TypeError, ValueError)):
        replace(candidate, collection_mode=None)


def test_candidate_creation_writes_no_output_manifest_or_journal(tmp_path):
    build_full_dedup_candidate = _full_dedup_candidate_api()

    work_dir = tmp_path / "work"
    _copy_mlab_source(work_dir, "scan", "complete_vasp_651.mlab")
    sentinels = {
        work_dir / "MD_data.extxyz": b"output-sentinel",
        work_dir / "md" / "compatibility-result.yaml": b"result-sentinel",
        work_dir / "md" / "journal.json": b"journal-sentinel",
        work_dir / "md" / "candidate.tmp": b"candidate-sentinel",
        work_dir / "backups" / "collect" / "previous.bak": b"backup-sentinel",
    }
    for path, payload in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    manifest = read_manifest(work_dir, "md")
    before = _snapshot_work_tree(work_dir)
    candidate = build_full_dedup_candidate(
        work_dir=work_dir,
        manifest=manifest,
    )
    after = _snapshot_work_tree(work_dir)

    assert isinstance(candidate, models.CollectionCandidate)
    assert after == before
    assert not manifest_path(work_dir, "md").exists()


def test_candidate_cost_is_one_identity_per_complete_configuration(
    tmp_path,
    monkeypatch,
):
    build_full_dedup_candidate = _full_dedup_candidate_api()

    mlff_collect = import_module("dpmoire_lite.mlff_collect")
    real_identity = mlff_collect.config_identity
    calls = []

    def spy(configuration):
        calls.append(configuration)
        return real_identity(configuration)

    monkeypatch.setattr(mlff_collect, "config_identity", spy)

    work_dir = tmp_path / "work"
    directories = ["md/one", "md/two", "md/failed", "md/missing"]
    write_manifest(
        work_dir,
        Manifest(
            stage="md",
            generated_at="2026-07-13T12:00:00",
            directories=directories,
        ),
    )
    _copy_mlab_source(work_dir, "one", "complete_vasp_651.mlab")
    _copy_mlab_source(work_dir, "two", "complete_multi.mlab")
    _copy_mlab_source(work_dir, "failed", "internal_corruption.mlab")

    candidate = build_full_dedup_candidate(
        work_dir=work_dir,
        manifest=read_manifest(work_dir, "md"),
    )
    accepted_total = sum(
        item.accepted_count
        for item in candidate.source_results
        if item.status
        in {models.SourceStatus.COMPLETE, models.SourceStatus.PARTIAL}
    )

    assert len(calls) == accepted_total
    assert len(calls) == candidate.dedup_stats.seen
    assert accepted_total == 3
    assert [item.status for item in candidate.source_results] == [
        models.SourceStatus.COMPLETE,
        models.SourceStatus.COMPLETE,
        models.SourceStatus.FAILED,
        models.SourceStatus.SKIPPED,
    ]
    assert [
        (item.seen, item.retained, item.duplicates_removed)
        for item in candidate.dedup_stats.per_source
    ][-2:] == [(0, 0, 0), (0, 0, 0)]
