from dataclasses import replace
from importlib import import_module, util
from pathlib import Path

import numpy as np
import pytest

from dpmoire_lite import collect_models as models
from dpmoire_lite import dataset as dataset_module
from dpmoire_lite.mlab import config_identity, parse_mlab


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


_MLAB_FIXTURE_DIR = Path(__file__).parent / "data" / "mlab"


def _fixture_configuration(name: str):
    return parse_mlab(_MLAB_FIXTURE_DIR / name).configurations[0]


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
