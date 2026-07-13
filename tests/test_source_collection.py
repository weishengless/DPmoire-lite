import hashlib
import shutil
import warnings
from pathlib import Path

import numpy as np
import pytest
import yaml
from ase import Atoms
from ase.units import GPa

from dpmoire_lite import collect as collect_module
from dpmoire_lite import collect_models as models
from dpmoire_lite import dataset as dataset_module
from dpmoire_lite.manifest import Manifest, read_manifest
from dpmoire_lite.mlab import (
    MlabConfiguration,
    MlabIdentity,
    parse_mlab,
    seed_prefix_identity,
)
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
    assert result.complete_count == 0
    assert result.accepted_count == 0
    assert result.accepted_frames == ()
    assert result.parsed_configurations == ()
    assert result.seed_identity is None
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
        assert result.complete_count == 0
        assert result.accepted_count == 0
        assert result.accepted_frames == ()
        assert result.parsed_configurations == ()
        assert result.seed_identity is None
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
