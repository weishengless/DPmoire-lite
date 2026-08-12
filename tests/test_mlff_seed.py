from dataclasses import FrozenInstanceError, replace
import hashlib
import math
from pathlib import Path

import pytest

from dpmoire_lite.mlab import config_identity, parse_mlab, seed_prefix_identity
from dpmoire_lite.mlff_seed import SeedPrefixVerifier


FIXTURE_ROOT = Path(__file__).parent / "data" / "mlab"


def _configurations(name: str):
    return parse_mlab(FIXTURE_ROOT / name).configurations


def _install_reference(work_dir: Path, name: str) -> tuple[Path, bytes]:
    path = work_dir / "init_mlff" / "ML_ABN"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (FIXTURE_ROOT / name).read_bytes()
    path.write_bytes(payload)
    return path, payload


def _current_evidence(name: str) -> dict[str, object]:
    payload = (FIXTURE_ROOT / name).read_bytes()
    configurations = _configurations(name)
    return {
        "source": "init_mlff/ML_ABN",
        "configurations": len(configurations),
        "digest_schema": "mlab-seed-v1",
        "seed_prefix_sha256": seed_prefix_identity(configurations).sha256,
        "ml_ab_sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_exact_prefix_returns_exact_and_zero_deltas():
    reference = _configurations("seed_input_vasp_651.mlab")

    result = SeedPrefixVerifier(reference).verify(reference)

    assert result.schema == "vasp-seed-prefix-equivalence-v1"
    assert result.outcome == "exact"
    assert result.expected_exact_identity == result.actual_exact_identity
    assert result.first_mismatch is None
    assert result.max_deltas.lattice.absolute == 0.0
    assert result.max_deltas.lattice.scaled == 0.0
    assert result.max_deltas.positions.absolute == 0.0
    assert result.max_deltas.positions.scaled == 0.0
    assert result.max_deltas.energy.absolute == 0.0
    assert result.max_deltas.energy.scaled == 0.0
    assert result.max_deltas.forces.absolute == 0.0
    assert result.max_deltas.forces.scaled == 0.0
    assert result.max_deltas.stress_kbar.absolute == 0.0
    assert result.max_deltas.stress_kbar.scaled == 0.0


def test_vasp_641_rewrite_returns_vasp_equivalent():
    reference = _configurations("seed_input_vasp_641.mlab")
    rewritten = _configurations("seed_rewrite_vasp_641.mlab")

    result = SeedPrefixVerifier(reference).verify(rewritten)

    assert result.schema == "vasp-seed-prefix-equivalence-v1"
    assert result.outcome == "vasp_equivalent"
    assert result.expected_exact_identity != result.actual_exact_identity
    assert result.first_mismatch is None
    assert result.max_deltas.positions.absolute == pytest.approx(
        8.673617379884036e-19,
        rel=0.0,
        abs=1e-30,
    )
    assert result.max_deltas.forces.absolute == pytest.approx(
        6.938893903907228e-18,
        rel=0.0,
        abs=1e-28,
    )
    assert result.max_deltas.lattice.absolute == 0.0
    assert result.max_deltas.energy.absolute == 0.0
    assert result.max_deltas.stress_kbar.absolute == 0.0


def test_vasp_651_rewrite_returns_vasp_equivalent():
    reference = _configurations("seed_input_vasp_651.mlab")
    rewritten = _configurations("seed_rewrite_vasp_651.mlab")

    result = SeedPrefixVerifier(reference).verify(rewritten)

    assert result.outcome == "vasp_equivalent"
    assert result.expected_exact_identity.sha256 == (
        "9ddfc45b1da0801b8f596fb96dada2904cbf41e4f106d6c060819959fbc005cc"
    )
    assert result.actual_exact_identity.sha256 == (
        "8eb05cb9b5ba5adac5ecf7a852e5289a783e9b740b4f508af1586a8be912fc9e"
    )
    assert result.max_deltas.positions.absolute == pytest.approx(
        1.012523398458143e-13,
        rel=0.0,
        abs=1e-28,
    )
    assert result.max_deltas.positions.scaled == pytest.approx(
        1.012523398458143e-13,
        rel=0.0,
        abs=1e-28,
    )
    assert result.max_deltas.forces.absolute == pytest.approx(
        1.3877787807814457e-17,
        rel=0.0,
        abs=1e-28,
    )


def test_vasp_equivalence_uses_fixed_v1_scaled_rule():
    baseline = _configurations("seed_input_vasp_651.mlab")[0]
    reference = replace(baseline, energy=2_000_000.0)
    rewritten = replace(baseline, energy=2_000_000.000001)

    result = SeedPrefixVerifier((reference,)).verify((rewritten,))

    assert result.outcome == "vasp_equivalent"
    assert result.max_deltas.energy.absolute > 1e-12
    assert result.max_deltas.energy.scaled < 1e-12


def test_numeric_delta_at_threshold_is_accepted():
    baseline = _configurations("seed_input_vasp_651.mlab")[0]
    reference = replace(baseline, energy=0.0)
    rewritten = replace(baseline, energy=1e-12)

    result = SeedPrefixVerifier((reference,)).verify((rewritten,))

    assert result.outcome == "vasp_equivalent"
    assert result.max_deltas.energy.absolute == 1e-12
    assert result.max_deltas.energy.scaled == 1e-12


def test_numeric_delta_above_threshold_is_mismatch():
    baseline = _configurations("seed_input_vasp_651.mlab")[0]
    reference = replace(baseline, energy=0.0)
    above_threshold = math.nextafter(1e-12, math.inf)
    rewritten = replace(baseline, energy=above_threshold)

    result = SeedPrefixVerifier((reference,)).verify((rewritten,))

    assert result.outcome == "mismatch"
    assert result.first_mismatch.configuration_index == 0
    assert result.first_mismatch.field == "energy"
    assert result.first_mismatch.component is None
    assert result.first_mismatch.expected == 0.0
    assert result.first_mismatch.actual == above_threshold
    assert result.first_mismatch.absolute == above_threshold
    assert result.first_mismatch.scaled == above_threshold
    assert result.first_mismatch.reason == "numeric_delta_exceeds_v1"
    assert result.max_deltas.energy.absolute == above_threshold
    assert result.max_deltas.energy.scaled == above_threshold


def test_element_count_atom_or_configuration_order_change_is_mismatch():
    first, second = _configurations("complete_multi.mlab")
    variants = (
        ((replace(first, elements=("N", "Pt")), second), "elements", (0,)),
        ((replace(first, counts=(2, 0)), second), "counts", (0,)),
        (
            (
                replace(
                    first,
                    positions=tuple(reversed(first.positions)),
                    forces=tuple(reversed(first.forces)),
                ),
                second,
            ),
            "positions",
            (0, 0),
        ),
        ((second, first), "positions", (0, 2)),
    )

    for rewritten, expected_field, expected_component in variants:
        result = SeedPrefixVerifier((first, second)).verify(rewritten)

        assert result.outcome == "mismatch"
        assert result.first_mismatch is not None
        assert result.first_mismatch.configuration_index == 0
        assert result.first_mismatch.field == expected_field
        assert result.first_mismatch.component == expected_component


def test_each_numeric_field_reports_deterministic_component_context():
    baseline = _configurations("seed_input_vasp_651.mlab")[0]
    variants = (
        (
            replace(
                baseline,
                lattice=((4.0, 1e-6, 0.0), *baseline.lattice[1:]),
            ),
            "lattice",
            (0, 1),
        ),
        (
            replace(
                baseline,
                positions=(baseline.positions[0], (2.0, 2.0, 2.000001)),
            ),
            "positions",
            (1, 2),
        ),
        (replace(baseline, energy=baseline.energy + 1e-6), "energy", None),
        (
            replace(
                baseline,
                forces=(baseline.forces[0], (-0.1, 1e-6, 0.0)),
            ),
            "forces",
            (1, 1),
        ),
        (
            replace(
                baseline,
                stress_kbar=(1.0, 2.0, 3.0, 4.0, 5.000001, 6.0),
            ),
            "stress_kbar",
            (4,),
        ),
    )

    for rewritten, expected_field, expected_component in variants:
        result = SeedPrefixVerifier((baseline,)).verify((rewritten,))

        assert result.outcome == "mismatch"
        assert result.first_mismatch.configuration_index == 0
        assert result.first_mismatch.field == expected_field
        assert result.first_mismatch.component == expected_component
        assert result.first_mismatch.reason == "numeric_delta_exceeds_v1"
        assert getattr(result.max_deltas, expected_field).absolute > 0.0

    first, second = _configurations("complete_multi.mlab")
    earlier_numeric = replace(first, energy=first.energy + 1e-6)
    later_structural = replace(second, elements=("N", "Pt"))

    ordered_result = SeedPrefixVerifier((first, second)).verify(
        (earlier_numeric, later_structural)
    )

    assert ordered_result.outcome == "mismatch"
    assert ordered_result.first_mismatch.configuration_index == 0
    assert ordered_result.first_mismatch.field == "energy"


def test_short_prefix_is_mismatch():
    first, second = _configurations("complete_multi.mlab")

    result = SeedPrefixVerifier((first, second)).verify((first,))

    assert result.outcome == "mismatch"
    assert result.configurations == 2
    assert result.actual_exact_identity == seed_prefix_identity((first,))
    assert result.first_mismatch.configuration_index is None
    assert result.first_mismatch.field == "configurations"
    assert result.first_mismatch.component is None
    assert result.first_mismatch.expected == 2
    assert result.first_mismatch.actual == 1
    assert result.first_mismatch.absolute is None
    assert result.first_mismatch.scaled is None
    assert result.first_mismatch.reason == "short_prefix"
    assert result.max_deltas == result.max_deltas.zero()


def test_equivalence_returns_no_approximate_digest():
    reference = _configurations("seed_input_vasp_651.mlab")
    rewritten = _configurations("seed_rewrite_vasp_651.mlab")

    result = SeedPrefixVerifier(reference).verify(rewritten)

    assert result.outcome == "vasp_equivalent"
    assert result.expected_exact_identity.schema == "mlab-seed-v1"
    assert result.actual_exact_identity.schema == "mlab-seed-v1"
    assert result.expected_exact_identity != result.actual_exact_identity
    assert not hasattr(result, "digest")
    assert not hasattr(result, "approximate_identity")
    assert not hasattr(result, "equivalence_identity")
    with pytest.raises(FrozenInstanceError):
        result.outcome = "exact"
    with pytest.raises(FrozenInstanceError):
        result.max_deltas.positions.absolute = 0.0


def test_exact_seed_and_config_identity_outputs_are_unchanged():
    reference = _configurations("seed_input_vasp_651.mlab")[0]
    rewritten = _configurations("seed_rewrite_vasp_651.mlab")[0]
    expected_reference_sha256 = (
        "9ddfc45b1da0801b8f596fb96dada2904cbf41e4f106d6c060819959fbc005cc"
    )
    expected_rewritten_sha256 = (
        "8eb05cb9b5ba5adac5ecf7a852e5289a783e9b740b4f508af1586a8be912fc9e"
    )

    before = (
        seed_prefix_identity((reference,)),
        seed_prefix_identity((rewritten,)),
        config_identity(reference),
        config_identity(rewritten),
    )
    SeedPrefixVerifier((reference,)).verify((rewritten,))
    after = (
        seed_prefix_identity((reference,)),
        seed_prefix_identity((rewritten,)),
        config_identity(reference),
        config_identity(rewritten),
    )

    assert before == after
    assert before[0].schema == before[1].schema == "mlab-seed-v1"
    assert before[2].schema == before[3].schema == "mlab-config-v1"
    assert before[0].sha256 == before[2].sha256 == expected_reference_sha256
    assert before[1].sha256 == before[3].sha256 == expected_rewritten_sha256


def test_current_adapter_exact_match_does_not_read_reference(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    reference = _configurations("seed_input_vasp_651.mlab")
    evidence = {
        "source": "init_mlff/ML_ABN",
        "configurations": 1,
        "digest_schema": "mlab-seed-v1",
        "seed_prefix_sha256": (
            "9ddfc45b1da0801b8f596fb96dada2904cbf41e4f106d6c060819959fbc005cc"
        ),
        "ml_ab_sha256": "0" * 64,
    }

    verifier = SeedPrefixVerifier.from_current_manifest(
        work_dir=work_dir,
        evidence=evidence,
    )
    result = verifier.verify(reference)

    assert not (work_dir / "init_mlff" / "ML_ABN").exists()
    assert result.outcome == "exact"
    assert result.expected_exact_identity == result.actual_exact_identity
    assert result.reference is None
    assert result.reference_failure is None


def test_current_adapter_mismatch_loads_reference_once_across_sources(tmp_path):
    work_dir = tmp_path / "work"
    reference_path, payload = _install_reference(
        work_dir,
        "seed_input_vasp_651.mlab",
    )
    evidence = _current_evidence("seed_input_vasp_651.mlab")
    rewritten = _configurations("seed_rewrite_vasp_651.mlab")
    verifier = SeedPrefixVerifier.from_current_manifest(
        work_dir=work_dir,
        evidence=evidence,
    )

    first = verifier.verify(rewritten)
    reference_path.unlink()
    second = verifier.verify(
        _configurations("seed_rewrite_postseed_vasp_651.mlab")
    )

    assert first == second
    assert first.outcome == "vasp_equivalent"
    assert first.reference.source == "init_mlff/ML_ABN"
    assert first.reference.raw_sha256 == hashlib.sha256(payload).hexdigest()
    assert first.reference.trust == "manifest-v2"
    assert first.reference_failure is None


def test_current_adapter_requires_contained_source_path(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    evidence = _current_evidence("seed_input_vasp_651.mlab")
    unsafe_sources = (
        "../outside/ML_ABN",
        str((tmp_path / "outside" / "ML_ABN").resolve()),
        "C:/outside/ML_ABN",
    )

    for source in unsafe_sources:
        invalid = {**evidence, "source": source}
        with pytest.raises(ValueError, match=r"mlff_seed\.source.*safe relative"):
            SeedPrefixVerifier.from_current_manifest(
                work_dir=work_dir,
                evidence=invalid,
            )


def test_current_adapter_validates_exact_evidence_shape_without_reference_io(
    tmp_path,
):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    evidence = _current_evidence("seed_input_vasp_651.mlab")
    invalid_cases = (
        ({"configurations": True}, r"mlff_seed\.configurations"),
        ({"configurations": 0}, r"mlff_seed\.configurations"),
        ({"configurations": 1.0}, r"mlff_seed\.configurations"),
        ({"digest_schema": "approximate"}, r"mlff_seed\.digest_schema"),
        ({"seed_prefix_sha256": "0" * 63}, r"mlff_seed\.seed_prefix_sha256"),
        ({"seed_prefix_sha256": "A" * 64}, r"mlff_seed\.seed_prefix_sha256"),
    )

    with pytest.raises(ValueError, match="must be a mapping"):
        SeedPrefixVerifier.from_current_manifest(
            work_dir=work_dir,
            evidence=[],
        )
    for update, expected_message in invalid_cases:
        with pytest.raises(ValueError, match=expected_message):
            SeedPrefixVerifier.from_current_manifest(
                work_dir=work_dir,
                evidence={**evidence, **update},
            )

    assert not (work_dir / "init_mlff" / "ML_ABN").exists()


def test_current_adapter_requires_recorded_raw_sha256(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    evidence = _current_evidence("seed_input_vasp_651.mlab")
    invalid_values = (None, "0" * 63, "0" * 63 + "G", "A" * 64)

    for value in invalid_values:
        invalid = {**evidence, "ml_ab_sha256": value}
        with pytest.raises(ValueError, match=r"mlff_seed\.ml_ab_sha256.*lowercase"):
            SeedPrefixVerifier.from_current_manifest(
                work_dir=work_dir,
                evidence=invalid,
            )

    reference_path, payload = _install_reference(
        work_dir,
        "seed_input_vasp_651.mlab",
    )
    assert reference_path.is_file()
    mismatched = {**evidence, "ml_ab_sha256": "0" * 64}
    verifier = SeedPrefixVerifier.from_current_manifest(
        work_dir=work_dir,
        evidence=mismatched,
    )

    result = verifier.verify(_configurations("seed_rewrite_vasp_651.mlab"))

    assert result.outcome == "mismatch"
    assert result.reference_failure.code == "raw_sha256_mismatch"
    assert result.reference_failure.expected == "0" * 64
    assert result.reference_failure.actual == hashlib.sha256(payload).hexdigest()


def test_current_adapter_requires_complete_reference_count_and_exact_identity(
    tmp_path,
):
    cases = (
        (
            "incomplete",
            "tail_position_crop.mlab",
            _current_evidence("tail_position_crop.mlab"),
            _configurations("seed_rewrite_vasp_651.mlab"),
            "reference_incomplete",
        ),
        (
            "count",
            "seed_input_vasp_651.mlab",
            {
                **_current_evidence("seed_input_vasp_651.mlab"),
                "configurations": 2,
                "seed_prefix_sha256": "0" * 64,
            },
            _configurations("seed_rewrite_postseed_vasp_651.mlab"),
            "configuration_count_mismatch",
        ),
        (
            "identity",
            "seed_input_vasp_651.mlab",
            {
                **_current_evidence("seed_input_vasp_651.mlab"),
                "seed_prefix_sha256": "0" * 64,
            },
            _configurations("seed_rewrite_vasp_651.mlab"),
            "exact_identity_mismatch",
        ),
    )

    for directory, fixture, evidence, final, expected_code in cases:
        work_dir = tmp_path / directory
        _install_reference(work_dir, fixture)
        verifier = SeedPrefixVerifier.from_current_manifest(
            work_dir=work_dir,
            evidence=evidence,
        )

        result = verifier.verify(final)

        assert result.outcome == "mismatch"
        assert result.first_mismatch.field == "reference"
        assert result.first_mismatch.reason == "reference_failure"
        assert result.reference is None
        assert result.reference_failure.code == expected_code


def test_current_adapter_returns_structured_reference_failure(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    evidence = _current_evidence("seed_input_vasp_651.mlab")
    rewritten = _configurations("seed_rewrite_vasp_651.mlab")
    verifier = SeedPrefixVerifier.from_current_manifest(
        work_dir=work_dir,
        evidence=evidence,
    )

    first = verifier.verify(rewritten)
    _install_reference(work_dir, "seed_input_vasp_651.mlab")
    second = verifier.verify(
        _configurations("seed_rewrite_postseed_vasp_651.mlab")
    )

    assert first == second
    assert first.outcome == "mismatch"
    assert first.expected_exact_identity.sha256 == evidence["seed_prefix_sha256"]
    assert first.actual_exact_identity.sha256 != evidence["seed_prefix_sha256"]
    assert first.first_mismatch.reason == "reference_failure"
    assert first.reference is None
    assert first.reference_failure.code == "source_missing"
    assert first.reference_failure.source == "init_mlff/ML_ABN"
    assert str(tmp_path) not in first.reference_failure.reason
    with pytest.raises(FrozenInstanceError):
        first.reference_failure.code = "changed"


def test_legacy_adapter_uses_only_complete_init_mlff_mlabn(tmp_path):
    valid_work = tmp_path / "valid"
    _install_reference(valid_work, "seed_input_vasp_651.mlab")
    rewritten = _configurations("seed_rewrite_vasp_651.mlab")
    valid_verifier = SeedPrefixVerifier.from_legacy_work_dir(work_dir=valid_work)

    with pytest.warns(UserWarning, match=r"Legacy seed evidence.*init_mlff/ML_ABN"):
        valid_result = valid_verifier.verify(rewritten)

    assert valid_result.outcome == "vasp_equivalent"
    assert valid_result.reference.source == "init_mlff/ML_ABN"

    partial_work = tmp_path / "partial"
    partial_path, _ = _install_reference(partial_work, "tail_position_crop.mlab")
    assert partial_path.name == "ML_ABN"
    (partial_path.parent / "ML_AB").write_bytes(
        (FIXTURE_ROOT / "seed_input_vasp_651.mlab").read_bytes()
    )
    partial_verifier = SeedPrefixVerifier.from_legacy_work_dir(
        work_dir=partial_work,
    )

    partial_result = partial_verifier.verify(rewritten)

    assert partial_result.outcome == "mismatch"
    assert partial_result.reference is None
    assert partial_result.reference_failure.code == "reference_incomplete"


def test_legacy_adapter_records_legacy_rebuilt_trust(tmp_path):
    work_dir = tmp_path / "work"
    reference_path, payload = _install_reference(
        work_dir,
        "seed_input_vasp_651.mlab",
    )
    verifier = SeedPrefixVerifier.from_legacy_work_dir(work_dir=work_dir)
    rewritten = _configurations("seed_rewrite_vasp_651.mlab")

    with pytest.warns(UserWarning) as recorded:
        first = verifier.verify(rewritten)
        reference_path.unlink()
        second = verifier.verify(rewritten)

    assert len(recorded) == 1
    assert "Legacy seed evidence" in str(recorded[0].message)
    assert "init_mlff/ML_ABN" in str(recorded[0].message)
    assert "mlab-seed-v1" in str(recorded[0].message)
    assert first == second
    assert first.outcome == "vasp_equivalent"
    assert first.reference.source == "init_mlff/ML_ABN"
    assert first.reference.raw_sha256 == hashlib.sha256(payload).hexdigest()
    assert first.reference.trust == "legacy-rebuilt"
    assert first.reference_failure is None


def test_legacy_adapter_missing_or_partial_reference_fails_closed(tmp_path):
    rewritten = _configurations("seed_rewrite_vasp_651.mlab")
    cases = (
        ("missing", None, "source_missing"),
        ("partial", "tail_force_crop.mlab", "reference_incomplete"),
    )

    for directory, fixture, expected_code in cases:
        work_dir = tmp_path / directory
        work_dir.mkdir()
        if fixture is not None:
            _install_reference(work_dir, fixture)
        verifier = SeedPrefixVerifier.from_legacy_work_dir(work_dir=work_dir)

        result = verifier.verify(rewritten)

        assert result.outcome == "mismatch"
        assert result.configurations is None
        assert result.expected_exact_identity is None
        assert result.actual_exact_identity is None
        assert result.first_mismatch.field == "reference"
        assert result.reference is None
        assert result.reference_failure.code == expected_code
        assert result.reference_failure.source == "init_mlff/ML_ABN"
