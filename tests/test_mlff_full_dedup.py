import pytest

from dpmoire_lite import collect_models as models


def _require(name):
    value = getattr(models, name, None)
    assert value is not None, f"{name} is not implemented"
    return value


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
