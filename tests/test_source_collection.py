import hashlib
import shutil
import warnings
from pathlib import Path

import numpy as np
import pytest
import yaml
from ase import Atoms
from ase.io.vasp import read_vasp_out
from ase.units import GPa

from dpmoire_lite import collect as collect_module
from dpmoire_lite import collect_models as models
from dpmoire_lite.config import load_config
from dpmoire_lite import dataset as dataset_module
from dpmoire_lite.manifest import Manifest, read_manifest, write_manifest
from dpmoire_lite.mlab import (
    MlabConfiguration,
    MlabIdentity,
    parse_mlab,
    seed_prefix_identity,
)
from dpmoire_lite.outcar import OutcarSelection, open_outcar_frames
from dpmoire_lite.paths import manifest_path, relative_to_workdir


def _frame(x_position: float = 0.0) -> Atoms:
    return Atoms(
        "H",
        positions=[[x_position, 0.0, 0.0]],
        cell=[4.0, 4.0, 4.0],
        pbc=True,
    )


def _mlab_configuration() -> MlabConfiguration:
    return MlabConfiguration(
        source_path=Path("md/0_0/ML_ABN"),
        source_configuration_number=1,
        source_line=8,
        elements=("H",),
        counts=(1,),
        n_atoms=1,
        lattice=((4.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 4.0)),
        positions=((0.0, 0.0, 0.0),),
        energy=-1.0,
        forces=((0.0, 0.0, 0.0),),
        stress_kbar=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _outcar_result(source_path: str, x_position: float) -> models.SourceResult:
    return models.SourceResult(
        source_path=source_path,
        source_kind=models.SourceKind.OUTCAR,
        status=models.SourceStatus.COMPLETE,
        complete_count=1,
        accepted_frames=(_frame(x_position),),
    )


def test_source_status_values_are_stable():
    assert tuple(status.value for status in models.SourceStatus) == (
        "complete",
        "partial",
        "skipped",
        "failed",
    )


def test_source_result_owns_frames_until_accepted():
    frame = _frame()
    supplied_frames = [frame]

    result = models.SourceResult(
        source_path="rlx/0_0/OUTCAR",
        source_kind=models.SourceKind.OUTCAR,
        status=models.SourceStatus.COMPLETE,
        complete_count=1,
        accepted_frames=supplied_frames,
    )
    frame.positions[0, 0] = 9.0
    supplied_frames.clear()

    assert isinstance(result.accepted_frames, tuple)
    assert result.accepted_count == 1
    assert result.accepted_frames[0] is not frame
    assert result.accepted_frames[0].positions[0, 0] == pytest.approx(0.0)


def test_mlab_source_result_owns_parsed_payload_until_accepted():
    configuration = _mlab_configuration()
    supplied_configurations = [configuration]

    result = models.SourceResult(
        source_path="md/0_0/ML_ABN",
        source_kind=models.SourceKind.MLAB,
        status=models.SourceStatus.COMPLETE,
        complete_count=1,
        parsed_configurations=supplied_configurations,
    )
    supplied_configurations.clear()

    assert result.accepted_frames == ()
    assert result.parsed_configurations == (configuration,)
    assert result.accepted_count == 1


def test_failed_source_result_cannot_expose_accepted_frames():
    with pytest.raises(
        ValueError,
        match="failed source cannot expose accepted frames",
    ):
        models.SourceResult(
            source_path="rlx/0_0/OUTCAR",
            source_kind=models.SourceKind.OUTCAR,
            status=models.SourceStatus.FAILED,
            complete_count=1,
            accepted_frames=(_frame(),),
            reason="internal parser corruption",
        )


def test_failed_source_result_cannot_expose_parsed_payload():
    with pytest.raises(
        ValueError,
        match="failed source cannot expose parsed configurations",
    ):
        models.SourceResult(
            source_path="md/0_0/ML_ABN",
            source_kind=models.SourceKind.MLAB,
            status=models.SourceStatus.FAILED,
            complete_count=1,
            parsed_configurations=(_mlab_configuration(),),
            reason="internal parser corruption",
        )


def test_collection_candidate_frame_count_equals_source_sum():
    sources = (
        _outcar_result("rlx/0_0/OUTCAR", 0.0),
        _outcar_result("rlx/0_1/OUTCAR", 1.0),
    )
    candidate = models.CollectionCandidate(
        source_results=sources,
        accepted_frames=(_frame(0.0), _frame(1.0)),
    )

    assert candidate.frame_count == 2
    with pytest.raises(
        ValueError,
        match="candidate frame count 1 does not equal source accepted count 2",
    ):
        models.CollectionCandidate(
            source_results=sources,
            accepted_frames=(_frame(0.0),),
        )


def test_collection_candidate_preserves_declared_source_order():
    sources = (
        _outcar_result("rlx/z-last/OUTCAR", 0.0),
        _outcar_result("rlx/a-first/OUTCAR", 1.0),
    )

    candidate = models.CollectionCandidate(
        source_results=sources,
        accepted_frames=(_frame(0.0), _frame(1.0)),
    )

    assert tuple(
        result.source_path for result in candidate.source_results
    ) == (
        "rlx/z-last/OUTCAR",
        "rlx/a-first/OUTCAR",
    )


def test_source_diagnostics_use_workdir_relative_paths(tmp_path):
    work_dir = tmp_path / "work"
    mlab_path = work_dir / "md" / "0_0" / "ML_ABN"
    source_path = relative_to_workdir(work_dir, mlab_path)
    result = models.SourceResult(
        source_path=source_path,
        source_kind=models.SourceKind.MLAB,
        status=models.SourceStatus.FAILED,
        complete_count=3,
        reason="unexpected EOF in stress block",
        discarded_configuration_number=4,
        discarded_block="stress",
        line_number=81,
        seed_identity=MlabIdentity(
            schema="mlab-seed-v1",
            sha256="a" * 64,
        ),
    )

    diagnostic = result.as_diagnostic()

    assert list(diagnostic) == [
        "path",
        "kind",
        "status",
        "complete_count",
        "accepted_count",
        "reason",
        "discarded_configuration",
        "discarded_block",
        "line_number",
        "seed_identity",
    ]
    assert diagnostic == {
        "path": "md/0_0/ML_ABN",
        "kind": "mlab",
        "status": "failed",
        "complete_count": 3,
        "accepted_count": 0,
        "reason": "unexpected EOF in stress block",
        "discarded_configuration": 4,
        "discarded_block": "stress",
        "line_number": 81,
        "seed_identity": {
            "schema": "mlab-seed-v1",
            "sha256": "a" * 64,
        },
    }
    assert yaml.safe_load(yaml.safe_dump(diagnostic)) == diagnostic

    outcar_result = models.SourceResult(
        source_path="rlx/0_0/OUTCAR",
        source_kind=models.SourceKind.OUTCAR,
        status=models.SourceStatus.FAILED,
        complete_count=1,
        reason="internal parser corruption",
        discarded_frame_index=1,
        line_number=123,
        pattern="^OUTCAR$",
        pattern_index=3,
        order=0,
    )
    assert outcar_result.as_diagnostic() == {
        "path": "rlx/0_0/OUTCAR",
        "kind": "outcar",
        "status": "failed",
        "complete_count": 1,
        "accepted_count": 0,
        "reason": "internal parser corruption",
        "discarded_frame": 1,
        "line_number": 123,
        "pattern": "^OUTCAR$",
        "pattern_index": 3,
        "order": 0,
    }

    with pytest.raises(ValueError, match="workdir-relative POSIX path"):
        models.SourceResult(
            source_path=mlab_path.as_posix(),
            source_kind=models.SourceKind.MLAB,
            status=models.SourceStatus.SKIPPED,
            complete_count=0,
            reason="missing ML_ABN",
        )


def _collect_mlab_source(fixture_name: str):
    collector = getattr(collect_module, "collect_mlab_source", None)
    assert callable(collector), "collect_mlab_source is not implemented"
    work_dir = Path(__file__).parent
    source_path = work_dir / "data" / "mlab" / fixture_name
    return collector(work_dir=work_dir, source_path=source_path)


def test_complete_mlab_source_contributes_all_new_configurations():
    result = _collect_mlab_source("complete_multi.mlab")

    assert result.source_path == "data/mlab/complete_multi.mlab"
    assert result.source_kind is models.SourceKind.MLAB
    assert result.status is models.SourceStatus.COMPLETE
    assert result.complete_count == result.accepted_count == 2
    assert result.accepted_frames == ()
    assert result.reason is None
    assert tuple(
        configuration.source_configuration_number
        for configuration in result.parsed_configurations
    ) == (1, 2)


def test_tail_partial_mlab_contributes_complete_prefix_and_metadata():
    result = _collect_mlab_source("tail_position_crop.mlab")

    assert result.status is models.SourceStatus.PARTIAL
    assert result.complete_count == result.accepted_count == 1
    assert len(result.parsed_configurations) == 1
    assert result.parsed_configurations[0].source_configuration_number == 1
    assert result.accepted_frames == ()
    assert result.discarded_configuration_number == 2
    assert result.discarded_block == "positions"
    assert result.reason is not None
    assert "end of file" in result.reason.lower()


def test_first_frame_truncated_mlab_is_failed_with_zero_frames():
    result = _collect_mlab_source("first_incomplete.mlab")

    assert result.status is models.SourceStatus.FAILED
    assert result.complete_count == result.accepted_count == 0
    assert result.accepted_frames == ()
    assert result.parsed_configurations == ()
    assert result.discarded_configuration_number == 1
    assert result.discarded_block == "positions"
    assert result.line_number is not None
    assert result.reason is not None
    assert "first configuration incomplete" in result.reason


def test_internal_corruption_mlab_is_failed_with_zero_frames():
    result = _collect_mlab_source("internal_corruption.mlab")

    assert result.status is models.SourceStatus.FAILED
    assert result.complete_count == result.accepted_count == 0
    assert result.accepted_frames == ()
    assert result.parsed_configurations == ()
    assert result.discarded_configuration_number == 2
    assert result.discarded_block == "positions"
    assert result.line_number is not None
    assert result.reason is not None
    assert "internal corruption" in result.reason


def test_mlab_source_failure_does_not_mutate_existing_candidate():
    existing_source = _outcar_result("md/existing/OUTCAR", 0.5)
    candidate = models.CollectionCandidate(
        source_results=(existing_source,),
        accepted_frames=(_frame(0.5),),
    )
    before_source = candidate.source_results[0]
    before_frame = candidate.accepted_frames[0]
    before_symbols = tuple(before_frame.get_chemical_symbols())
    before_cell = before_frame.get_cell().array.copy()
    before_positions = before_frame.get_positions().copy()

    result = _collect_mlab_source("internal_corruption.mlab")

    assert result.status is models.SourceStatus.FAILED
    assert result.accepted_count == 0
    assert result.parsed_configurations == ()
    assert candidate.frame_count == 1
    assert candidate.source_results[0] is before_source
    assert candidate.source_results[0].accepted_count == 1
    assert tuple(candidate.accepted_frames[0].get_chemical_symbols()) == before_symbols
    np.testing.assert_allclose(candidate.accepted_frames[0].get_cell().array, before_cell)
    np.testing.assert_allclose(
        candidate.accepted_frames[0].get_positions(), before_positions
    )


def test_mlab_source_ase_properties_match_parsed_values():
    source_path = Path(__file__).parent / "data" / "mlab" / "complete_vasp_651.mlab"
    configuration = parse_mlab(source_path).configurations[0]
    adapter = getattr(dataset_module, "atoms_from_mlab_configuration", None)
    assert callable(adapter), "atoms_from_mlab_configuration is not implemented"

    atoms = adapter(configuration)

    expected_symbols = tuple(
        element
        for element, count in zip(configuration.elements, configuration.counts)
        for _ in range(count)
    )
    assert tuple(atoms.get_chemical_symbols()) == expected_symbols
    np.testing.assert_allclose(atoms.get_cell().array, configuration.lattice)
    np.testing.assert_allclose(atoms.get_positions(), configuration.positions)
    assert atoms.get_potential_energy() == pytest.approx(configuration.energy)
    np.testing.assert_allclose(atoms.get_forces(), configuration.forces)

    xx, yy, zz, xy, yz, zx = configuration.stress_kbar
    expected_stress = -0.1 * GPa * np.array([xx, yy, zz, yz, zx, xy])
    np.testing.assert_allclose(atoms.get_stress(), expected_stress)


def _collect_current_mlab_source_api():
    collector = getattr(collect_module, "collect_current_mlab_source", None)
    assert callable(
        collector
    ), "collect_current_mlab_source is not implemented"
    return collector


def _mlab_fixture_path(name: str) -> Path:
    return Path(__file__).parent / "data" / "mlab" / name


def _runtime_mlab_source(tmp_path: Path, fixture_name: str) -> tuple[Path, Path]:
    work_dir = tmp_path / "work"
    source_path = work_dir / "md" / "0_0" / "ML_ABN"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_mlab_fixture_path(fixture_name), source_path)
    return work_dir, source_path


def _current_manifest_for_seed(
    configurations: tuple[MlabConfiguration, ...],
    *,
    seed_digest: str | None = None,
) -> Manifest:
    identity = seed_prefix_identity(configurations)
    return Manifest(
        schema_version=2,
        stage="md",
        generated_at="2026-07-13T00:00:00+00:00",
        directories=["md/0_0"],
        mlff_seed={
            "source": "init_mlff/ML_ABN",
            "configurations": len(configurations),
            "digest_schema": "mlab-seed-v1",
            "seed_prefix_sha256": seed_digest or identity.sha256,
            "ml_ab_sha256": hashlib.sha256(b"synthetic-ML_AB").hexdigest(),
            "ml_ff_sha256": hashlib.sha256(b"synthetic-ML_FF").hexdigest(),
        },
    )


def _copy_current_ml_ab(work_dir: Path, fixture_name: str) -> Path:
    ml_ab = work_dir / "md" / "0_0" / "ML_AB"
    shutil.copy2(_mlab_fixture_path(fixture_name), ml_ab)
    return ml_ab


def _prefix_result_signature(result: models.SourceResult):
    return (
        result.status,
        result.complete_count,
        result.accepted_count,
        tuple(
            configuration.source_configuration_number
            for configuration in result.parsed_configurations
        ),
        result.seed_identity,
    )


def _replace_declared_count(text: str, count: int) -> str:
    lines = text.splitlines(keepends=True)
    label_index = next(
        index
        for index, line in enumerate(lines)
        if line.strip() == "The number of configurations"
    )
    value_index = next(
        index
        for index in range(label_index + 1, len(lines))
        if lines[index].strip()
    )
    newline = "\n" if lines[value_index].endswith("\n") else ""
    lines[value_index] = f"{count}{newline}"
    return "".join(lines)


def test_seed_prefix_match_skips_exact_initial_count(tmp_path):
    collect_current_mlab_source = _collect_current_mlab_source_api()
    work_dir, source_path = _runtime_mlab_source(tmp_path, "complete_multi.mlab")
    parsed = parse_mlab(source_path)
    manifest = _current_manifest_for_seed(parsed.configurations[:1])

    result = collect_current_mlab_source(
        work_dir=work_dir,
        source_path=source_path,
        manifest=manifest,
    )

    assert result.source_path == "md/0_0/ML_ABN"
    assert result.status is models.SourceStatus.COMPLETE
    assert result.complete_count == 2
    assert result.accepted_count == 1
    assert result.accepted_frames == ()
    assert tuple(
        configuration.source_configuration_number
        for configuration in result.parsed_configurations
    ) == (2,)
    assert result.seed_identity == seed_prefix_identity(parsed.configurations[:1])


def test_current_vasp_rewritten_prefix_collects_post_seed_frames(tmp_path):
    collect_current_mlab_source = _collect_current_mlab_source_api()
    work_dir, source_path = _runtime_mlab_source(
        tmp_path,
        "seed_rewrite_postseed_vasp_651.mlab",
    )
    seed_path = work_dir / "init_mlff" / "ML_ABN"
    seed_path.parent.mkdir(parents=True)
    shutil.copy2(_mlab_fixture_path("seed_input_vasp_651.mlab"), seed_path)
    parsed_seed = parse_mlab(seed_path)
    parsed_final = parse_mlab(source_path)
    manifest = _current_manifest_for_seed(parsed_seed.configurations)
    manifest.mlff_seed["ml_ab_sha256"] = hashlib.sha256(
        seed_path.read_bytes()
    ).hexdigest()

    result = collect_current_mlab_source(
        work_dir=work_dir,
        source_path=source_path,
        manifest=manifest,
    )

    assert result.status is models.SourceStatus.COMPLETE
    assert result.complete_count == 2
    assert result.accepted_count == 1
    assert result.parsed_configurations == (parsed_final.configurations[1],)
    assert result.seed_identity == seed_prefix_identity(
        parsed_final.configurations,
        n_configurations=1,
    )
    assert result.seed_verification.outcome == "vasp_equivalent"
    assert result.seed_verification.reference.trust == "manifest-v2"


def test_source_result_exposes_structured_seed_verification(tmp_path):
    collect_current_mlab_source = _collect_current_mlab_source_api()
    work_dir, source_path = _runtime_mlab_source(
        tmp_path,
        "seed_rewrite_postseed_vasp_651.mlab",
    )
    seed_path = work_dir / "init_mlff" / "ML_ABN"
    seed_path.parent.mkdir(parents=True)
    shutil.copy2(_mlab_fixture_path("seed_input_vasp_651.mlab"), seed_path)
    parsed_seed = parse_mlab(seed_path)
    parsed_final = parse_mlab(source_path)
    manifest = _current_manifest_for_seed(parsed_seed.configurations)
    manifest.mlff_seed["ml_ab_sha256"] = hashlib.sha256(
        seed_path.read_bytes()
    ).hexdigest()

    result = collect_current_mlab_source(
        work_dir=work_dir,
        source_path=source_path,
        manifest=manifest,
    )

    diagnostic = result.as_diagnostic()
    verification = diagnostic["seed_verification"]
    assert list(verification) == [
        "status",
        "schema",
        "configurations",
        "expected_exact_sha256",
        "actual_exact_sha256",
        "reference",
        "max_deltas",
    ]
    assert verification["status"] == "vasp_equivalent"
    assert verification["schema"] == "vasp-seed-prefix-equivalence-v1"
    assert verification["configurations"] == 1
    assert verification["expected_exact_sha256"] == seed_prefix_identity(
        parsed_seed.configurations
    ).sha256
    assert verification["actual_exact_sha256"] == seed_prefix_identity(
        parsed_final.configurations,
        n_configurations=1,
    ).sha256
    assert verification["reference"] == {
        "source": "init_mlff/ML_ABN",
        "raw_sha256": hashlib.sha256(seed_path.read_bytes()).hexdigest(),
        "trust": "manifest-v2",
    }
    assert list(verification["max_deltas"]) == [
        "lattice",
        "positions",
        "energy",
        "forces",
        "stress_kbar",
    ]
    assert verification["max_deltas"]["lattice"] == {
        "absolute": 0.0,
        "scaled": 0.0,
    }
    assert verification["max_deltas"]["positions"]["absolute"] == pytest.approx(
        1.0125233984581428e-13
    )
    assert verification["max_deltas"]["positions"]["scaled"] == pytest.approx(
        3.462600963201334e-14
    )
    assert verification["max_deltas"]["forces"] == {
        "absolute": pytest.approx(1.3877787807814457e-17),
        "scaled": pytest.approx(1.3877787807814457e-17),
    }
    assert verification["max_deltas"]["energy"] == {
        "absolute": 0.0,
        "scaled": 0.0,
    }
    assert verification["max_deltas"]["stress_kbar"] == {
        "absolute": 0.0,
        "scaled": 0.0,
    }
    assert yaml.safe_load(yaml.safe_dump(diagnostic)) == diagnostic


def test_seed_prefix_mismatch_fails_source_with_zero_new_frames(tmp_path):
    collect_current_mlab_source = _collect_current_mlab_source_api()
    work_dir, source_path = _runtime_mlab_source(tmp_path, "complete_multi.mlab")
    parsed = parse_mlab(source_path)
    expected_digest = "0" * 64
    manifest = _current_manifest_for_seed(
        parsed.configurations[:1], seed_digest=expected_digest
    )

    result = collect_current_mlab_source(
        work_dir=work_dir,
        source_path=source_path,
        manifest=manifest,
    )

    actual_identity = seed_prefix_identity(parsed.configurations[:1])
    assert result.status is models.SourceStatus.FAILED
    assert result.complete_count == 2
    assert result.accepted_count == 0
    assert result.accepted_frames == ()
    assert result.parsed_configurations == ()
    assert result.reason is not None
    assert "seed prefix mismatch" in result.reason.lower()
    assert expected_digest in result.reason
    assert actual_identity.sha256 in result.reason
    assert result.seed_identity == actual_identity


def test_seed_prefix_shorter_than_initial_count_fails(tmp_path):
    collect_current_mlab_source = _collect_current_mlab_source_api()
    work_dir, source_path = _runtime_mlab_source(tmp_path, "complete_vasp_641.mlab")
    seed_parsed = parse_mlab(_mlab_fixture_path("complete_multi.mlab"))
    manifest = _current_manifest_for_seed(seed_parsed.configurations[:2])

    result = collect_current_mlab_source(
        work_dir=work_dir,
        source_path=source_path,
        manifest=manifest,
    )

    assert result.status is models.SourceStatus.FAILED
    assert result.complete_count == 1
    assert result.accepted_count == 0
    assert result.accepted_frames == ()
    assert result.parsed_configurations == ()
    assert result.seed_identity is None
    assert result.reason is not None
    assert "shorter" in result.reason.lower()
    assert "1" in result.reason
    assert "2" in result.reason


def test_seed_prefix_same_count_different_content_fails(tmp_path):
    collect_current_mlab_source = _collect_current_mlab_source_api()
    work_dir, source_path = _runtime_mlab_source(tmp_path, "complete_vasp_651.mlab")
    seed_source = parse_mlab(_mlab_fixture_path("complete_vasp_641.mlab"))
    manifest = _current_manifest_for_seed(seed_source.configurations)

    result = collect_current_mlab_source(
        work_dir=work_dir,
        source_path=source_path,
        manifest=manifest,
    )

    actual_identity = seed_prefix_identity(parse_mlab(source_path).configurations)
    assert result.status is models.SourceStatus.FAILED
    assert result.complete_count == 1
    assert result.accepted_count == 0
    assert result.accepted_frames == ()
    assert result.parsed_configurations == ()
    assert result.reason is not None
    assert "seed prefix mismatch" in result.reason.lower()
    assert result.seed_identity == actual_identity


def test_current_md_ml_ab_hash_change_is_ignored_for_restart(tmp_path):
    collect_current_mlab_source = _collect_current_mlab_source_api()
    work_dir, source_path = _runtime_mlab_source(tmp_path, "complete_multi.mlab")
    parsed = parse_mlab(source_path)
    manifest = _current_manifest_for_seed(parsed.configurations[:1])
    _copy_current_ml_ab(work_dir, "complete_vasp_641.mlab")

    first = collect_current_mlab_source(
        work_dir=work_dir,
        source_path=source_path,
        manifest=manifest,
    )

    _copy_current_ml_ab(work_dir, "complete_vasp_651.mlab")
    second = collect_current_mlab_source(
        work_dir=work_dir,
        source_path=source_path,
        manifest=manifest,
    )

    assert _prefix_result_signature(first) == _prefix_result_signature(second)
    assert first.seed_identity == seed_prefix_identity(parsed.configurations[:1])


def test_multiple_restart_growth_still_skips_only_initial_seed(tmp_path):
    collect_current_mlab_source = _collect_current_mlab_source_api()
    work_dir, source_path = _runtime_mlab_source(tmp_path, "complete_multi.mlab")
    parsed = parse_mlab(source_path)
    manifest = _current_manifest_for_seed(parsed.configurations[:1])
    _copy_current_ml_ab(work_dir, "complete_vasp_641.mlab")

    first = collect_current_mlab_source(
        work_dir=work_dir,
        source_path=source_path,
        manifest=manifest,
    )

    _copy_current_ml_ab(work_dir, "complete_multi.mlab")
    second = collect_current_mlab_source(
        work_dir=work_dir,
        source_path=source_path,
        manifest=manifest,
    )

    assert tuple(
        configuration.source_configuration_number
        for configuration in first.parsed_configurations
    ) == (2,)
    assert tuple(
        configuration.source_configuration_number
        for configuration in second.parsed_configurations
    ) == (2,)
    assert first.accepted_count == second.accepted_count == 1
    assert first.seed_identity == second.seed_identity


def test_seed_only_source_is_complete_with_zero_new_frames(tmp_path):
    collect_current_mlab_source = _collect_current_mlab_source_api()
    work_dir, source_path = _runtime_mlab_source(tmp_path, "complete_vasp_641.mlab")
    parsed = parse_mlab(source_path)
    manifest = _current_manifest_for_seed(parsed.configurations)

    result = collect_current_mlab_source(
        work_dir=work_dir,
        source_path=source_path,
        manifest=manifest,
    )

    assert result.status is models.SourceStatus.COMPLETE
    assert result.complete_count == 1
    assert result.accepted_count == 0
    assert result.parsed_configurations == ()
    assert result.accepted_frames == ()
    assert result.seed_identity == seed_prefix_identity(parsed.configurations)


def test_new_data_tail_partial_preserves_complete_new_frames(tmp_path):
    collect_current_mlab_source = _collect_current_mlab_source_api()
    work_dir, source_path = _runtime_mlab_source(tmp_path, "complete_multi.mlab")
    complete_text = _mlab_fixture_path("complete_multi.mlab").read_text(
        encoding="utf-8"
    )
    tail_text = _mlab_fixture_path("tail_position_crop.mlab").read_text(
        encoding="utf-8"
    )
    tail_block = tail_text[tail_text.index("Configuration num.      2") :].replace(
        "Configuration num.      2", "Configuration num.      3", 1
    )
    source_path.write_text(
        _replace_declared_count(complete_text, 3).rstrip()
        + "\n"
        + tail_block,
        encoding="utf-8",
    )
    parsed_seed = parse_mlab(_mlab_fixture_path("complete_multi.mlab"))
    manifest = _current_manifest_for_seed(parsed_seed.configurations[:1])

    result = collect_current_mlab_source(
        work_dir=work_dir,
        source_path=source_path,
        manifest=manifest,
    )

    assert result.status is models.SourceStatus.PARTIAL
    assert result.complete_count == 2
    assert result.accepted_count == 1
    assert result.accepted_frames == ()
    assert tuple(
        configuration.source_configuration_number
        for configuration in result.parsed_configurations
    ) == (2,)
    assert result.discarded_configuration_number == 3
    assert result.discarded_block == "positions"
    assert result.reason is not None
    assert "end of file" in result.reason.lower()
    assert result.seed_identity == seed_prefix_identity(
        parsed_seed.configurations[:1]
    )


def test_equivalent_tail_partial_keeps_complete_post_seed_frames(tmp_path):
    collect_current_mlab_source = _collect_current_mlab_source_api()
    work_dir, source_path = _runtime_mlab_source(
        tmp_path,
        "seed_rewrite_postseed_vasp_651.mlab",
    )
    complete_text = source_path.read_text(encoding="utf-8")
    tail_text = _mlab_fixture_path("tail_position_crop.mlab").read_text(
        encoding="utf-8"
    )
    tail_block = tail_text[tail_text.index("Configuration num.      2") :].replace(
        "Configuration num.      2",
        "Configuration num.      3",
        1,
    )
    source_path.write_text(
        _replace_declared_count(complete_text, 3).rstrip() + "\n" + tail_block,
        encoding="utf-8",
    )
    seed_path = work_dir / "init_mlff" / "ML_ABN"
    seed_path.parent.mkdir(parents=True)
    shutil.copy2(_mlab_fixture_path("seed_input_vasp_651.mlab"), seed_path)
    parsed_seed = parse_mlab(seed_path)
    manifest = _current_manifest_for_seed(parsed_seed.configurations)
    manifest.mlff_seed["ml_ab_sha256"] = hashlib.sha256(
        seed_path.read_bytes()
    ).hexdigest()

    result = collect_current_mlab_source(
        work_dir=work_dir,
        source_path=source_path,
        manifest=manifest,
    )

    assert result.status is models.SourceStatus.PARTIAL
    assert result.complete_count == 2
    assert result.accepted_count == 1
    assert tuple(
        configuration.source_configuration_number
        for configuration in result.parsed_configurations
    ) == (2,)
    assert result.discarded_configuration_number == 3
    assert result.discarded_block == "positions"
    assert "end of file" in result.reason.lower()
    assert result.seed_verification.outcome == "vasp_equivalent"


def _collect_legacy_mlab_source_api():
    collector = getattr(collect_module, "collect_legacy_mlab_source", None)
    assert callable(
        collector
    ), "collect_legacy_mlab_source is not implemented"
    return collector


def _legacy_manifest_result(work_dir: Path):
    path = manifest_path(work_dir, "md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "stage": "md",
                "directories": ["md/0_0"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = read_manifest(work_dir, "md")
    assert result.kind == "legacy"
    assert result.manifest is None
    assert isinstance(result.raw_data, dict)
    return result


def _legacy_runtime_source(
    tmp_path: Path,
    final_fixture: str,
    seed_fixture: str | None,
) -> tuple[Path, Path]:
    work_dir = tmp_path / "work"
    source_path = work_dir / "md" / "0_0" / "ML_ABN"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_mlab_fixture_path(final_fixture), source_path)
    if seed_fixture is not None:
        seed_path = work_dir / "init_mlff" / "ML_ABN"
        seed_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_mlab_fixture_path(seed_fixture), seed_path)
    return work_dir, source_path


def test_legacy_seed_identity_rebuilt_from_complete_init_mlff_mlabn(tmp_path):
    collect_legacy_mlab_source = _collect_legacy_mlab_source_api()
    work_dir, source_path = _legacy_runtime_source(
        tmp_path,
        "complete_vasp_651.mlab",
        "complete_vasp_651.mlab",
    )
    manifest = _legacy_manifest_result(work_dir)
    expected_identity = seed_prefix_identity(
        parse_mlab(work_dir / "init_mlff" / "ML_ABN").configurations
    )

    with pytest.warns(UserWarning) as observed:
        result = collect_legacy_mlab_source(
            work_dir=work_dir,
            source_path=source_path,
            manifest=manifest,
        )

    assert len(observed) == 1
    assert result.status is models.SourceStatus.COMPLETE
    assert result.complete_count == 1
    assert result.accepted_count == 0
    assert result.accepted_frames == ()
    assert result.parsed_configurations == ()
    assert result.seed_identity == expected_identity


def test_legacy_vasp_rewritten_prefix_collects_post_seed_frames(tmp_path):
    collect_legacy_mlab_source = _collect_legacy_mlab_source_api()
    work_dir, source_path = _legacy_runtime_source(
        tmp_path,
        "seed_rewrite_postseed_vasp_651.mlab",
        "seed_input_vasp_651.mlab",
    )
    manifest = _legacy_manifest_result(work_dir)
    parsed_final = parse_mlab(source_path)

    with pytest.warns(UserWarning) as observed:
        result = collect_legacy_mlab_source(
            work_dir=work_dir,
            source_path=source_path,
            manifest=manifest,
        )

    assert len(observed) == 1
    assert result.status is models.SourceStatus.COMPLETE
    assert result.complete_count == 2
    assert result.accepted_count == 1
    assert result.parsed_configurations == (parsed_final.configurations[1],)
    assert result.seed_identity == seed_prefix_identity(
        parsed_final.configurations,
        n_configurations=1,
    )
    assert result.seed_verification.outcome == "vasp_equivalent"
    assert result.seed_verification.reference.trust == "legacy-rebuilt"


def test_legacy_seed_rebuild_warns_and_verifies_final_prefix(tmp_path):
    collect_legacy_mlab_source = _collect_legacy_mlab_source_api()
    work_dir, source_path = _legacy_runtime_source(
        tmp_path,
        "complete_multi.mlab",
        "complete_vasp_651.mlab",
    )
    manifest = _legacy_manifest_result(work_dir)
    expected_identity = seed_prefix_identity(
        parse_mlab(work_dir / "init_mlff" / "ML_ABN").configurations
    )

    with pytest.warns(UserWarning) as observed:
        result = collect_legacy_mlab_source(
            work_dir=work_dir,
            source_path=source_path,
            manifest=manifest,
        )

    assert len(observed) == 1
    warning_message = str(observed[0].message).lower()
    assert "legacy" in warning_message
    assert "init_mlff/ml_abn" in warning_message
    assert "mlab-seed-v1" in warning_message
    assert "final prefix" in warning_message
    assert "verif" in warning_message
    assert result.status is models.SourceStatus.COMPLETE
    assert result.complete_count == 2
    assert result.accepted_count == 1
    assert result.accepted_frames == ()
    assert tuple(
        configuration.source_configuration_number
        for configuration in result.parsed_configurations
    ) == (2,)
    assert result.seed_identity == expected_identity


def test_legacy_missing_init_seed_fails_without_using_md_ml_ab(tmp_path):
    collect_legacy_mlab_source = _collect_legacy_mlab_source_api()
    work_dir, source_path = _legacy_runtime_source(
        tmp_path,
        "complete_multi.mlab",
        None,
    )
    _copy_current_ml_ab(work_dir, "complete_vasp_651.mlab")
    manifest = _legacy_manifest_result(work_dir)

    result = collect_legacy_mlab_source(
        work_dir=work_dir,
        source_path=source_path,
        manifest=manifest,
    )

    assert result.status is models.SourceStatus.FAILED
    assert result.complete_count == 2
    assert result.accepted_count == 0
    assert result.accepted_frames == ()
    assert result.parsed_configurations == ()
    assert result.seed_identity is None
    assert result.seed_verification.reference_failure.code == "source_missing"
    assert result.reason is not None
    assert "init_mlff/ML_ABN" in result.reason
    assert "legacy" in result.reason.lower()
    assert "missing" in result.reason.lower()


def test_legacy_partial_or_invalid_init_seed_fails(tmp_path):
    collect_legacy_mlab_source = _collect_legacy_mlab_source_api()
    cases = (
        ("partial", "tail_position_crop.mlab"),
        ("invalid", "internal_corruption.mlab"),
    )

    for case_name, seed_fixture in cases:
        work_dir, source_path = _legacy_runtime_source(
            tmp_path / case_name,
            "complete_vasp_651.mlab",
            seed_fixture,
        )
        manifest = _legacy_manifest_result(work_dir)
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("always")
            result = collect_legacy_mlab_source(
                work_dir=work_dir,
                source_path=source_path,
                manifest=manifest,
            )

        assert not any(
            "legacy" in str(item.message).lower() for item in observed
        )
        assert result.status is models.SourceStatus.FAILED
        assert result.complete_count == 1
        assert result.accepted_count == 0
        assert result.accepted_frames == ()
        assert result.parsed_configurations == ()
        assert result.seed_identity is None
        assert result.seed_verification.reference_failure.code in {
            "reference_incomplete",
            "parse_failed",
        }
        assert result.reason is not None
        assert "init_mlff/ML_ABN" in result.reason
        assert any(
            marker in result.reason.lower()
            for marker in ("complete", "parse", "invalid")
        )


def test_legacy_final_prefix_mismatch_fails_source(tmp_path):
    collect_legacy_mlab_source = _collect_legacy_mlab_source_api()
    work_dir, source_path = _legacy_runtime_source(
        tmp_path,
        "complete_vasp_651.mlab",
        "complete_vasp_641.mlab",
    )
    manifest = _legacy_manifest_result(work_dir)
    expected_identity = seed_prefix_identity(
        parse_mlab(work_dir / "init_mlff" / "ML_ABN").configurations
    )
    actual_identity = seed_prefix_identity(parse_mlab(source_path).configurations)

    with pytest.warns(UserWarning) as observed:
        result = collect_legacy_mlab_source(
            work_dir=work_dir,
            source_path=source_path,
            manifest=manifest,
        )

    assert len(observed) == 1
    assert result.status is models.SourceStatus.FAILED
    assert result.complete_count == 1
    assert result.accepted_count == 0
    assert result.accepted_frames == ()
    assert result.parsed_configurations == ()
    assert result.reason is not None
    assert "seed prefix mismatch" in result.reason.lower()
    assert expected_identity.sha256 in result.reason
    assert actual_identity.sha256 in result.reason
    assert result.seed_identity == actual_identity


def _collect_outcar_source_api():
    collector = getattr(collect_module, "collect_outcar_source", None)
    assert callable(collector), "collect_outcar_source is not implemented"
    return collector


def _outcar_fixture_path(name: str) -> Path:
    return Path(__file__).parent / "data" / "outcar" / name


def _runtime_outcar_source(
    tmp_path: Path,
    fixture_name: str,
    *,
    directory: str = "0_0",
    name: str = "OUTCAR",
) -> tuple[Path, Path]:
    work_dir = tmp_path / "work"
    source_path = work_dir / "md" / directory / name
    source_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_outcar_fixture_path(fixture_name), source_path)
    return work_dir, source_path


def _outcar_selection(
    path: Path,
    *,
    pattern: str = r"^OUTCAR$",
    pattern_index: int = 0,
    order: int = 0,
) -> OutcarSelection:
    return OutcarSelection(
        path=path,
        pattern=pattern,
        pattern_index=pattern_index,
        order=order,
    )


def _outcar_reference_frames(path: Path):
    with open_outcar_frames(path) as frames:
        streamed_frames = tuple(frames)
    batch_frames = read_vasp_out(str(path), index=":")
    assert len(streamed_frames) == len(batch_frames)
    return tuple(batch_frames)


def _assert_outcar_frame_matches(actual, expected) -> None:
    assert actual.get_chemical_symbols() == expected.get_chemical_symbols()
    np.testing.assert_allclose(actual.cell.array, expected.cell.array)
    np.testing.assert_allclose(actual.positions, expected.positions)
    assert actual.get_potential_energy() == pytest.approx(
        expected.get_potential_energy()
    )
    assert actual.calc.results["free_energy"] == pytest.approx(
        expected.calc.results["free_energy"]
    )
    np.testing.assert_allclose(actual.get_forces(), expected.get_forces())
    np.testing.assert_allclose(actual.get_stress(), expected.get_stress())


def test_complete_outcar_source_contributes_sampled_frames(tmp_path):
    collect_outcar_source = _collect_outcar_source_api()
    work_dir, source_path = _runtime_outcar_source(
        tmp_path,
        "complete_two_frame.OUTCAR",
    )
    selection = _outcar_selection(source_path)
    expected_frames = _outcar_reference_frames(source_path)

    assert "LOOP+" not in source_path.read_text(encoding="utf-8")
    result = collect_outcar_source(
        work_dir=work_dir,
        selection=selection,
        freq=1,
    )

    assert result.source_kind is models.SourceKind.OUTCAR
    assert result.status is models.SourceStatus.COMPLETE
    assert result.complete_count == 2
    assert result.accepted_count == 2
    assert len(result.accepted_frames) == 2
    for actual, expected in zip(
        result.accepted_frames,
        expected_frames,
        strict=True,
    ):
        _assert_outcar_frame_matches(actual, expected)


def test_tail_truncated_outcar_keeps_prior_sampled_frames_as_partial(tmp_path):
    collect_outcar_source = _collect_outcar_source_api()
    work_dir, source_path = _runtime_outcar_source(
        tmp_path,
        "tail_truncated_second_frame.OUTCAR",
    )
    selection = _outcar_selection(source_path)
    expected_first = _outcar_reference_frames(
        _outcar_fixture_path("complete_two_frame.OUTCAR")
    )[0]

    result = collect_outcar_source(
        work_dir=work_dir,
        selection=selection,
        freq=1,
    )

    assert result.source_kind is models.SourceKind.OUTCAR
    assert result.status is models.SourceStatus.PARTIAL
    assert result.complete_count == 1
    assert result.accepted_count == 1
    assert result.discarded_frame_index == 1
    assert result.line_number is not None
    assert result.reason is not None
    assert any(
        marker in result.reason.lower()
        for marker in ("eof", "end-of-file", "incomplete", "trunc")
    )
    _assert_outcar_frame_matches(result.accepted_frames[0], expected_first)


def test_outcar_first_frame_failure_contributes_zero_frames(tmp_path):
    collect_outcar_source = _collect_outcar_source_api()
    work_dir, source_path = _runtime_outcar_source(
        tmp_path,
        "complete_two_frame.OUTCAR",
    )
    lines = _outcar_fixture_path("complete_two_frame.OUTCAR").read_text(
        encoding="utf-8"
    ).splitlines(keepends=True)
    source_path.write_text("".join(lines[:11]), encoding="utf-8")
    selection = _outcar_selection(source_path)

    result = collect_outcar_source(
        work_dir=work_dir,
        selection=selection,
        freq=1,
    )

    assert result.status is models.SourceStatus.FAILED
    assert result.complete_count == 0
    assert result.accepted_count == 0
    assert result.accepted_frames == ()
    assert result.discarded_frame_index == 0
    assert result.reason


def test_outcar_internal_corruption_does_not_salvage_prefix(tmp_path):
    collect_outcar_source = _collect_outcar_source_api()
    work_dir, source_path = _runtime_outcar_source(
        tmp_path,
        "internal_corruption_second_frame.OUTCAR",
    )
    selection = _outcar_selection(source_path)

    result = collect_outcar_source(
        work_dir=work_dir,
        selection=selection,
        freq=1,
    )

    assert result.status is models.SourceStatus.FAILED
    assert result.complete_count == 1
    assert result.accepted_count == 0
    assert result.accepted_frames == ()
    assert result.discarded_frame_index == 1
    assert result.line_number is not None
    assert result.reason


def test_outcar_invalid_utf8_is_failed_not_partial(tmp_path):
    collect_outcar_source = _collect_outcar_source_api()
    work_dir = tmp_path / "work"
    source_path = work_dir / "md" / "0_0" / "OUTCAR"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"\xff")
    selection = _outcar_selection(source_path)

    result = collect_outcar_source(
        work_dir=work_dir,
        selection=selection,
        freq=1,
    )

    assert result.status is models.SourceStatus.FAILED
    assert result.complete_count == 0
    assert result.accepted_count == 0
    assert result.accepted_frames == ()
    assert result.reason
    assert any(
        marker in result.reason.lower() for marker in ("utf", "decode")
    )


def test_each_segment_resets_sampling_and_has_independent_status(tmp_path):
    collect_outcar_source = _collect_outcar_source_api()
    work_dir, first_path = _runtime_outcar_source(
        tmp_path,
        "complete_two_frame.OUTCAR",
        directory="0_0",
        name="OUTCAR0",
    )
    _, second_path = _runtime_outcar_source(
        tmp_path,
        "complete_two_frame.OUTCAR",
        directory="0_1",
        name="OUTCAR1",
    )
    expected_first = _outcar_reference_frames(first_path)[0]

    results = tuple(
        collect_outcar_source(
            work_dir=work_dir,
            selection=_outcar_selection(
                source_path,
                pattern=r"^OUTCAR\d+$",
                pattern_index=0,
                order=order,
            ),
            freq=2,
        )
        for order, source_path in enumerate((first_path, second_path))
    )

    assert tuple(result.status for result in results) == (
        models.SourceStatus.COMPLETE,
        models.SourceStatus.COMPLETE,
    )
    assert tuple(result.complete_count for result in results) == (2, 2)
    assert tuple(result.accepted_count for result in results) == (1, 1)
    for result in results:
        _assert_outcar_frame_matches(result.accepted_frames[0], expected_first)


def test_outcar_source_records_pattern_index_and_order(tmp_path):
    collect_outcar_source = _collect_outcar_source_api()
    work_dir, source_path = _runtime_outcar_source(
        tmp_path,
        "complete_two_frame.OUTCAR",
    )
    selection = _outcar_selection(
        source_path,
        pattern=r"^OUTCAR$",
        pattern_index=4,
        order=9,
    )

    result = collect_outcar_source(
        work_dir=work_dir,
        selection=selection,
        freq=2,
    )

    assert result.source_path == "md/0_0/OUTCAR"
    assert result.pattern == selection.pattern
    assert result.pattern_index == selection.pattern_index
    assert result.order == selection.order


def _build_seed_aware_candidate_api():
    builder = getattr(
        collect_module,
        "build_seed_aware_candidate",
        None,
    )
    assert callable(
        builder
    ), "build_seed_aware_candidate is not implemented"
    return builder


def _build_full_dedup_candidate_api():
    builder = getattr(
        collect_module,
        "build_full_dedup_candidate",
        None,
    )
    assert callable(
        builder
    ), "build_full_dedup_candidate is not implemented"
    return builder


def _task6_config(
    tmp_path: Path,
    *,
    vasp_ml: bool,
    outcar_collect_freq: int = 1,
    outcar_patterns: tuple[str, ...] = (r"^OUTCAR$",),
):
    root = tmp_path / "config-root"
    input_dir = root / "input"
    script_dir = root / "scripts"
    potcar_dir = root / "potcars"
    input_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)
    potcar_dir.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "dft_script": "DFT_script.sh",
                "potcar_dir": str(potcar_dir),
                "script_dir": str(script_dir),
                "input_dir": str(input_dir),
                "work_dir": str(tmp_path / "work"),
                "n_nodes": 1,
                "stage": 0,
                "submit": False,
                "auto_resub": False,
                "vasp_ml": vasp_ml,
                "outcar_collect_freq": outcar_collect_freq,
                "do_relaxation": True,
                "init_mlff": True,
                "sc_rlx": True,
                "n_sectors": [1, 1],
                "sc": [1, 1],
                "d": 4.0,
                "k_mesh": 20,
                "encut_factor": 1.5,
                "r_cut": -1,
                "symm_reduce": False,
                "twist_val": False,
                "min_val_n": 4,
                "max_val_n": 5,
                "include_monolayer_md": False,
                "outcar_patterns": list(outcar_patterns),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return load_config(config_path)


def _task6_current_manifest(
    work_dir: Path,
    stage: str,
    directories: list[str],
    *,
    seed_fixture: str | None = None,
):
    mlff_seed = {}
    if seed_fixture is not None:
        parsed_seed = parse_mlab(_mlab_fixture_path(seed_fixture))
        seed_identity = seed_prefix_identity(parsed_seed.configurations)
        mlff_seed = {
            "source": "init_mlff/ML_ABN",
            "configurations": len(parsed_seed.configurations),
            "digest_schema": "mlab-seed-v1",
            "seed_prefix_sha256": seed_identity.sha256,
            "ml_ab_sha256": hashlib.sha256(b"synthetic-ML_AB").hexdigest(),
            "ml_ff_sha256": hashlib.sha256(b"synthetic-ML_FF").hexdigest(),
        }
    write_manifest(
        work_dir,
        Manifest(
            stage=stage,
            generated_at="2026-07-13T00:00:00+00:00",
            directories=list(directories),
            mlff_seed=mlff_seed,
        ),
    )
    result = read_manifest(work_dir, stage)
    assert result.kind == "current"
    assert result.manifest is not None
    return result


def _task6_legacy_manifest(
    work_dir: Path,
    stage: str,
    directories: list[str],
):
    path = manifest_path(work_dir, stage)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "stage": stage,
                "directories": list(directories),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = read_manifest(work_dir, stage)
    assert result.kind == "legacy"
    assert result.manifest is None
    assert isinstance(result.raw_data, dict)
    return result


def _task6_file_snapshot(work_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(work_dir).as_posix(): path.read_bytes()
        for path in work_dir.rglob("*")
        if path.is_file()
    }


def test_candidate_uses_manifest_directories_only(tmp_path):
    builder = _build_seed_aware_candidate_api()
    work_dir = tmp_path / "work"
    declared = work_dir / "rlx" / "declared"
    undeclared = work_dir / "rlx" / "undeclared"
    declared.mkdir(parents=True)
    undeclared.mkdir(parents=True)
    fixture = _outcar_fixture_path("complete_two_frame.OUTCAR")
    shutil.copy2(fixture, declared / "OUTCAR")
    shutil.copy2(fixture, undeclared / "OUTCAR")
    manifest = _task6_legacy_manifest(work_dir, "rlx", ["rlx/declared"])
    config = _task6_config(tmp_path, vasp_ml=False)

    candidate = builder(config=config, stage="rlx", manifest=manifest)

    assert candidate.expected_directories == ("rlx/declared",)
    assert candidate.expected_directory_count == 1
    assert tuple(
        source_result.source_path for source_result in candidate.source_results
    ) == ("rlx/declared/OUTCAR",)
    assert "rlx/undeclared/OUTCAR" not in {
        source_result.source_path for source_result in candidate.source_results
    }
    assert candidate.frame_count == 2


def test_collection_candidate_reuses_one_verifier_for_all_sources(tmp_path):
    builder = _build_seed_aware_candidate_api()
    work_dir = tmp_path / "work"
    directories = ("md/0_0", "md/0_1")
    for directory in directories:
        source_dir = work_dir / directory
        source_dir.mkdir(parents=True)
        shutil.copy2(
            _mlab_fixture_path("seed_rewrite_postseed_vasp_651.mlab"),
            source_dir / "ML_ABN",
        )
    seed_path = work_dir / "init_mlff" / "ML_ABN"
    seed_path.parent.mkdir(parents=True)
    shutil.copy2(_mlab_fixture_path("seed_input_vasp_651.mlab"), seed_path)
    manifest = _task6_legacy_manifest(work_dir, "md", list(directories))
    config = _task6_config(tmp_path, vasp_ml=True)

    with pytest.warns(UserWarning) as observed:
        candidate = builder(config=config, stage="md", manifest=manifest)

    assert len(observed) == 1
    assert candidate.frame_count == 2
    assert candidate.sources_complete == 2
    assert tuple(
        result.seed_verification.outcome for result in candidate.source_results
    ) == ("vasp_equivalent", "vasp_equivalent")
    assert (
        candidate.source_results[0].seed_verification.reference
        == candidate.source_results[1].seed_verification.reference
    )


def test_exact_and_vasp_equivalent_sources_skip_same_seed_count(tmp_path):
    builder = _build_seed_aware_candidate_api()
    work_dir = tmp_path / "work"
    exact_dir = work_dir / "md" / "exact"
    equivalent_dir = work_dir / "md" / "equivalent"
    exact_dir.mkdir(parents=True)
    equivalent_dir.mkdir(parents=True)
    shutil.copy2(_mlab_fixture_path("complete_multi.mlab"), exact_dir / "ML_ABN")
    shutil.copy2(
        _mlab_fixture_path("seed_rewrite_postseed_vasp_651.mlab"),
        equivalent_dir / "ML_ABN",
    )
    seed_path = work_dir / "init_mlff" / "ML_ABN"
    seed_path.parent.mkdir(parents=True)
    shutil.copy2(_mlab_fixture_path("seed_input_vasp_651.mlab"), seed_path)
    manifest = _task6_current_manifest(
        work_dir,
        "md",
        ["md/exact", "md/equivalent"],
        seed_fixture="seed_input_vasp_651.mlab",
    )
    manifest.manifest.mlff_seed["ml_ab_sha256"] = hashlib.sha256(
        seed_path.read_bytes()
    ).hexdigest()
    config = _task6_config(tmp_path, vasp_ml=True)

    candidate = builder(config=config, stage="md", manifest=manifest)

    assert candidate.frame_count == 2
    assert tuple(result.accepted_count for result in candidate.source_results) == (
        1,
        1,
    )
    assert tuple(
        result.seed_verification.outcome for result in candidate.source_results
    ) == ("exact", "vasp_equivalent")
    assert tuple(
        result.seed_verification.configurations
        for result in candidate.source_results
    ) == (1, 1)
    assert tuple(
        configuration.source_configuration_number
        for result in candidate.source_results
        for configuration in result.parsed_configurations
    ) == (2, 2)


def test_seed_mismatch_source_contributes_zero_frames(tmp_path):
    builder = _build_seed_aware_candidate_api()
    work_dir = tmp_path / "work"
    accepted_dir = work_dir / "md" / "accepted"
    mismatched_dir = work_dir / "md" / "mismatched"
    accepted_dir.mkdir(parents=True)
    mismatched_dir.mkdir(parents=True)
    shutil.copy2(
        _mlab_fixture_path("seed_rewrite_postseed_vasp_651.mlab"),
        accepted_dir / "ML_ABN",
    )
    shutil.copy2(
        _mlab_fixture_path("complete_vasp_641.mlab"),
        mismatched_dir / "ML_ABN",
    )
    seed_path = work_dir / "init_mlff" / "ML_ABN"
    seed_path.parent.mkdir(parents=True)
    shutil.copy2(_mlab_fixture_path("seed_input_vasp_651.mlab"), seed_path)
    manifest = _task6_current_manifest(
        work_dir,
        "md",
        ["md/accepted", "md/mismatched"],
        seed_fixture="seed_input_vasp_651.mlab",
    )
    manifest.manifest.mlff_seed["ml_ab_sha256"] = hashlib.sha256(
        seed_path.read_bytes()
    ).hexdigest()
    config = _task6_config(tmp_path, vasp_ml=True)

    candidate = builder(config=config, stage="md", manifest=manifest)

    accepted, mismatched = candidate.source_results
    assert candidate.frame_count == 1
    assert candidate.frame_count == sum(
        result.accepted_count for result in candidate.source_results
    )
    assert accepted.status is models.SourceStatus.COMPLETE
    assert accepted.accepted_count == 1
    assert mismatched.status is models.SourceStatus.FAILED
    assert mismatched.accepted_count == 0
    assert mismatched.parsed_configurations == ()
    assert mismatched.seed_verification.outcome == "mismatch"
    assert mismatched.seed_verification.first_mismatch.field == "elements"
    mismatch_diagnostic = mismatched.as_diagnostic()["seed_verification"]
    assert mismatch_diagnostic["status"] == "mismatch"
    assert mismatch_diagnostic["first_mismatch"] == {
        "configuration_index": 0,
        "field": "elements",
        "component": [0],
        "expected": "O",
        "actual": "C",
        "absolute": None,
        "scaled": None,
        "reason": "structural_mismatch",
    }
    assert yaml.safe_load(yaml.safe_dump(mismatch_diagnostic)) == (
        mismatch_diagnostic
    )


def test_reference_failure_is_source_local_and_reused(tmp_path):
    builder = _build_seed_aware_candidate_api()
    work_dir = tmp_path / "work"
    directories = ("md/first", "md/second")
    for directory in directories:
        source_dir = work_dir / directory
        source_dir.mkdir(parents=True)
        shutil.copy2(
            _mlab_fixture_path("seed_rewrite_postseed_vasp_651.mlab"),
            source_dir / "ML_ABN",
        )
    manifest = _task6_current_manifest(
        work_dir,
        "md",
        list(directories),
        seed_fixture="seed_input_vasp_651.mlab",
    )
    config = _task6_config(tmp_path, vasp_ml=True)

    candidate = builder(config=config, stage="md", manifest=manifest)

    first, second = candidate.source_results
    assert candidate.frame_count == 0
    assert candidate.sources_failed == 2
    assert tuple(result.source_path for result in (first, second)) == (
        "md/first/ML_ABN",
        "md/second/ML_ABN",
    )
    assert tuple(result.complete_count for result in (first, second)) == (2, 2)
    assert all(result.accepted_count == 0 for result in (first, second))
    assert all(result.parsed_configurations == () for result in (first, second))
    assert (
        first.seed_verification.reference_failure
        == second.seed_verification.reference_failure
    )
    assert first.seed_verification.reference_failure.code == "source_missing"
    assert first.seed_verification.reference_failure.source == "init_mlff/ML_ABN"
    failure_diagnostic = first.as_diagnostic()["seed_verification"]
    assert failure_diagnostic["status"] == "mismatch"
    assert failure_diagnostic["first_mismatch"]["field"] == "reference"
    assert failure_diagnostic["first_mismatch"]["reason"] == "reference_failure"
    assert failure_diagnostic["reference_failure"] == {
        "code": "source_missing",
        "source": "init_mlff/ML_ABN",
        "reason": "reference source is missing or is not a regular file",
        "expected": None,
        "actual": None,
    }
    assert yaml.safe_load(yaml.safe_dump(failure_diagnostic)) == failure_diagnostic


def test_early_md_collection_keeps_missing_sources_skipped(tmp_path):
    builder = _build_seed_aware_candidate_api()
    work_dir = tmp_path / "work"
    complete_dir = work_dir / "md" / "complete"
    empty_dir = work_dir / "md" / "empty"
    complete_dir.mkdir(parents=True)
    empty_dir.mkdir(parents=True)
    shutil.copy2(
        _mlab_fixture_path("seed_rewrite_postseed_vasp_651.mlab"),
        complete_dir / "ML_ABN",
    )
    seed_path = work_dir / "init_mlff" / "ML_ABN"
    seed_path.parent.mkdir(parents=True)
    shutil.copy2(_mlab_fixture_path("seed_input_vasp_651.mlab"), seed_path)
    manifest = _task6_current_manifest(
        work_dir,
        "md",
        ["md/complete", "md/missing", "md/empty"],
        seed_fixture="seed_input_vasp_651.mlab",
    )
    manifest.manifest.mlff_seed["ml_ab_sha256"] = hashlib.sha256(
        seed_path.read_bytes()
    ).hexdigest()
    config = _task6_config(tmp_path, vasp_ml=True)

    candidate = builder(config=config, stage="md", manifest=manifest)

    assert candidate.frame_count == 1
    assert candidate.sources_complete == 1
    assert candidate.sources_skipped == 2
    assert candidate.sources_failed == 0
    complete, missing, empty = candidate.source_results
    assert complete.seed_verification.outcome == "vasp_equivalent"
    assert tuple(result.status for result in (missing, empty)) == (
        models.SourceStatus.SKIPPED,
        models.SourceStatus.SKIPPED,
    )
    assert tuple(result.seed_verification for result in (missing, empty)) == (
        None,
        None,
    )
    assert missing.source_path == "md/missing"
    assert empty.source_path == "md/empty/ML_ABN"


def test_full_dedup_never_calls_seed_equivalence_verifier(tmp_path):
    builder = _build_full_dedup_candidate_api()
    work_dir = tmp_path / "work"
    source_dir = work_dir / "md" / "source"
    source_dir.mkdir(parents=True)
    shutil.copy2(
        _mlab_fixture_path("seed_rewrite_postseed_vasp_651.mlab"),
        source_dir / "ML_ABN",
    )
    manifest = _task6_current_manifest(
        work_dir,
        "md",
        ["md/source"],
        seed_fixture="seed_input_vasp_651.mlab",
    )

    candidate = builder(work_dir=work_dir, manifest=manifest)

    assert candidate.collection_mode is models.MLFFCollectMode.FULL_DEDUP
    assert candidate.frame_count == 2
    assert candidate.dedup_stats.seen == 2
    assert candidate.dedup_stats.unique == 2
    assert candidate.dedup_stats.duplicates_removed == 0
    (source_result,) = candidate.source_results
    assert source_result.complete_count == 2
    assert source_result.accepted_count == 2
    assert source_result.seed_identity is None
    assert source_result.seed_verification is None


def test_missing_directory_and_file_are_skipped_with_reason(tmp_path):
    builder = _build_seed_aware_candidate_api()
    work_dir = tmp_path / "work"
    (work_dir / "md" / "empty").mkdir(parents=True)
    manifest = _task6_current_manifest(
        work_dir,
        "md",
        ["md/missing", "md/empty"],
        seed_fixture="complete_vasp_651.mlab",
    )
    config = _task6_config(tmp_path, vasp_ml=True)

    candidate = builder(config=config, stage="md", manifest=manifest)

    assert candidate.expected_directory_count == 2
    assert candidate.sources_skipped == 2
    assert tuple(
        source_result.source_path for source_result in candidate.source_results
    ) == ("md/missing", "md/empty/ML_ABN")
    assert all(
        source_result.status is models.SourceStatus.SKIPPED
        for source_result in candidate.source_results
    )
    assert "missing" in candidate.source_results[0].reason.lower()
    assert "ml_abn" in candidate.source_results[1].reason.lower()
    assert candidate.accepted_frames == ()


def test_candidate_preserves_outcar_selection_order(tmp_path):
    builder = _build_seed_aware_candidate_api()
    work_dir = tmp_path / "work"
    first = work_dir / "rlx" / "directory_b"
    second = work_dir / "rlx" / "directory_a"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    fixture = _outcar_fixture_path("complete_two_frame.OUTCAR")
    shutil.copy2(fixture, first / "OUTCAR10")
    shutil.copy2(fixture, first / "OUTCAR2")
    shutil.copy2(fixture, second / "OUTCAR")
    manifest = _task6_current_manifest(
        work_dir,
        "rlx",
        ["rlx/directory_b", "rlx/directory_a"],
    )
    config = _task6_config(
        tmp_path,
        vasp_ml=False,
        outcar_patterns=(r"^OUTCAR\d+$", r"^OUTCAR$"),
    )

    candidate = builder(config=config, stage="rlx", manifest=manifest)

    assert tuple(
        (
            source_result.source_path,
            source_result.pattern,
            source_result.pattern_index,
            source_result.order,
        )
        for source_result in candidate.source_results
    ) == (
        ("rlx/directory_b/OUTCAR2", r"^OUTCAR\d+$", 0, 0),
        ("rlx/directory_b/OUTCAR10", r"^OUTCAR\d+$", 0, 1),
        ("rlx/directory_a/OUTCAR", r"^OUTCAR$", 1, 0),
    )


def test_candidate_counts_all_source_statuses(tmp_path):
    builder = _build_seed_aware_candidate_api()
    work_dir = tmp_path / "work"
    directories = [
        "rlx/complete",
        "rlx/partial",
        "rlx/failed",
        "rlx/no-match",
    ]
    fixture_names = {
        "complete": "complete_two_frame.OUTCAR",
        "partial": "tail_truncated_second_frame.OUTCAR",
        "failed": "internal_corruption_second_frame.OUTCAR",
    }
    for directory, fixture_name in fixture_names.items():
        target_dir = work_dir / "rlx" / directory
        target_dir.mkdir(parents=True)
        shutil.copy2(
            _outcar_fixture_path(fixture_name),
            target_dir / "OUTCAR",
        )
    (work_dir / "rlx" / "no-match").mkdir(parents=True)
    manifest = _task6_current_manifest(work_dir, "rlx", directories)
    config = _task6_config(tmp_path, vasp_ml=False)

    candidate = builder(config=config, stage="rlx", manifest=manifest)

    assert candidate.expected_directory_count == 4
    assert candidate.sources_attempted == 3
    assert candidate.sources_complete == 1
    assert candidate.sources_partial == 1
    assert candidate.sources_skipped == 1
    assert candidate.sources_failed == 1
    assert tuple(
        source_result.status for source_result in candidate.source_results
    ) == (
        models.SourceStatus.COMPLETE,
        models.SourceStatus.PARTIAL,
        models.SourceStatus.FAILED,
        models.SourceStatus.SKIPPED,
    )
    skipped_result = candidate.source_results[-1]
    assert skipped_result.source_path == "rlx/no-match"
    assert skipped_result.reason is not None
    assert "outcar" in skipped_result.reason.lower()
    assert any(
        marker in skipped_result.reason.lower()
        for marker in ("match", "found", "no file")
    )


def test_candidate_frames_equal_complete_plus_partial_contributions(tmp_path):
    builder = _build_seed_aware_candidate_api()
    complete_text = _mlab_fixture_path("complete_multi.mlab").read_text(
        encoding="utf-8"
    )
    tail_text = _mlab_fixture_path("tail_position_crop.mlab").read_text(
        encoding="utf-8"
    )
    tail_block = tail_text[tail_text.index("Configuration num.      2") :].replace(
        "Configuration num.      2", "Configuration num.      3", 1
    )
    partial_text = (
        _replace_declared_count(complete_text, 3).rstrip()
        + "\n"
        + tail_block
    )

    for layout in ("current", "legacy"):
        layout_root = tmp_path / layout
        work_dir = layout_root / "work"
        complete_dir = work_dir / "md" / "0_0"
        partial_dir = work_dir / "md" / "0_1"
        complete_dir.mkdir(parents=True)
        partial_dir.mkdir(parents=True)
        shutil.copy2(
            _mlab_fixture_path("complete_multi.mlab"),
            complete_dir / "ML_ABN",
        )
        (partial_dir / "ML_ABN").write_text(
            partial_text,
            encoding="utf-8",
        )

        if layout == "current":
            manifest = _task6_current_manifest(
                work_dir,
                "md",
                ["md/0_0", "md/0_1"],
                seed_fixture="complete_vasp_651.mlab",
            )
        else:
            seed_path = work_dir / "init_mlff" / "ML_ABN"
            seed_path.parent.mkdir(parents=True)
            shutil.copy2(
                _mlab_fixture_path("complete_vasp_651.mlab"),
                seed_path,
            )
            manifest = _task6_legacy_manifest(
                work_dir,
                "md",
                ["md/0_0", "md/0_1"],
            )
        config = _task6_config(layout_root, vasp_ml=True)

        if layout == "legacy":
            with pytest.warns(UserWarning) as observed:
                candidate = builder(
                    config=config,
                    stage="md",
                    manifest=manifest,
                )
            assert len(observed) == 1
            assert all(
                "legacy" in str(item.message).lower() for item in observed
            )
        else:
            with warnings.catch_warnings(record=True) as observed:
                warnings.simplefilter("always")
                candidate = builder(
                    config=config,
                    stage="md",
                    manifest=manifest,
                )
            assert not observed

        assert tuple(
            source_result.source_path for source_result in candidate.source_results
        ) == ("md/0_0/ML_ABN", "md/0_1/ML_ABN")
        assert tuple(
            source_result.status for source_result in candidate.source_results
        ) == (
            models.SourceStatus.COMPLETE,
            models.SourceStatus.PARTIAL,
        )
        assert tuple(
            source_result.accepted_count
            for source_result in candidate.source_results
        ) == (1, 1)
        assert tuple(
            tuple(
                configuration.source_configuration_number
                for configuration in source_result.parsed_configurations
            )
            for source_result in candidate.source_results
        ) == ((2,), (2,))
        assert candidate.frame_count == sum(
            source_result.accepted_count
            for source_result in candidate.source_results
        ) == 2
        for frame, source_result in zip(
            candidate.accepted_frames,
            candidate.source_results,
            strict=True,
        ):
            expected = dataset_module.atoms_from_mlab_configuration(
                source_result.parsed_configurations[0]
            )
            assert frame.get_chemical_symbols() == expected.get_chemical_symbols()
            np.testing.assert_allclose(frame.cell.array, expected.cell.array)
            np.testing.assert_allclose(frame.positions, expected.positions)
            assert frame.get_potential_energy() == pytest.approx(
                expected.get_potential_energy()
            )
            np.testing.assert_allclose(frame.get_forces(), expected.get_forces())


def test_failed_sources_contribute_zero_frames(tmp_path):
    builder = _build_seed_aware_candidate_api()
    work_dir = tmp_path / "work"
    complete_dir = work_dir / "rlx" / "complete"
    failed_dir = work_dir / "rlx" / "failed"
    complete_dir.mkdir(parents=True)
    failed_dir.mkdir(parents=True)
    shutil.copy2(
        _outcar_fixture_path("complete_two_frame.OUTCAR"),
        complete_dir / "OUTCAR",
    )
    shutil.copy2(
        _outcar_fixture_path("internal_corruption_second_frame.OUTCAR"),
        failed_dir / "OUTCAR",
    )
    manifest = _task6_current_manifest(
        work_dir,
        "rlx",
        ["rlx/complete", "rlx/failed"],
    )
    config = _task6_config(tmp_path, vasp_ml=False)

    candidate = builder(config=config, stage="rlx", manifest=manifest)

    assert candidate.frame_count == 2
    assert candidate.sources_complete == 1
    assert candidate.sources_failed == 1
    failed_result = candidate.source_results[1]
    assert failed_result.status is models.SourceStatus.FAILED
    assert failed_result.complete_count == 1
    assert failed_result.accepted_frames == ()
    assert failed_result.accepted_count == 0


def test_candidate_does_not_fuzzy_deduplicate_similar_frames(tmp_path):
    builder = _build_seed_aware_candidate_api()
    work_dir = tmp_path / "work"
    directories = ["md/0_0", "md/0_1", "md/0_2"]
    for directory in directories:
        (work_dir / directory).mkdir(parents=True)
    for directory in directories[:2]:
        shutil.copy2(
            _mlab_fixture_path("complete_multi.mlab"),
            work_dir / directory / "ML_ABN",
        )
    similar_text = _mlab_fixture_path("complete_multi.mlab").read_text(
        encoding="utf-8"
    ).replace(
        "1.100000000000000E+000",
        "1.100100000000000E+000",
        1,
    )
    (work_dir / "md" / "0_2" / "ML_ABN").write_text(
        similar_text,
        encoding="utf-8",
    )
    manifest = _task6_current_manifest(
        work_dir,
        "md",
        directories,
        seed_fixture="complete_vasp_651.mlab",
    )
    config = _task6_config(tmp_path, vasp_ml=True)

    candidate = builder(config=config, stage="md", manifest=manifest)

    assert tuple(
        source_result.source_path for source_result in candidate.source_results
    ) == tuple(f"{directory}/ML_ABN" for directory in directories)
    assert tuple(
        source_result.status for source_result in candidate.source_results
    ) == (models.SourceStatus.COMPLETE,) * 3
    assert tuple(
        source_result.accepted_count for source_result in candidate.source_results
    ) == (1, 1, 1)
    assert candidate.frame_count == 3
    np.testing.assert_allclose(
        candidate.accepted_frames[0].positions,
        candidate.accepted_frames[1].positions,
    )
    assert (
        candidate.accepted_frames[0].get_chemical_symbols()
        == candidate.accepted_frames[1].get_chemical_symbols()
    )
    assert candidate.accepted_frames[0].get_potential_energy() == pytest.approx(
        candidate.accepted_frames[1].get_potential_energy()
    )
    assert not np.array_equal(
        candidate.accepted_frames[0].positions,
        candidate.accepted_frames[2].positions,
    )


def test_candidate_creation_does_not_write_output_or_manifest(tmp_path):
    builder = _build_seed_aware_candidate_api()
    work_dir = tmp_path / "work"
    source_dir = work_dir / "rlx" / "0_0"
    source_dir.mkdir(parents=True)
    shutil.copy2(
        _outcar_fixture_path("complete_two_frame.OUTCAR"),
        source_dir / "OUTCAR",
    )
    manifest = _task6_current_manifest(work_dir, "rlx", ["rlx/0_0"])
    output = work_dir / "rlx_data.extxyz"
    output.write_bytes(b"existing-output")
    journal = work_dir / "journal.json"
    journal.write_bytes(b"existing-journal")
    candidate_dir = work_dir / "candidates"
    candidate_dir.mkdir()
    (candidate_dir / "candidate.tmp").write_bytes(b"existing-candidate")
    backup_dir = work_dir / "backups"
    backup_dir.mkdir()
    (backup_dir / "old.extxyz").write_bytes(b"existing-backup")
    before = _task6_file_snapshot(work_dir)
    config = _task6_config(tmp_path, vasp_ml=False)

    builder(config=config, stage="rlx", manifest=manifest)

    assert _task6_file_snapshot(work_dir) == before
