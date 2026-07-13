from pathlib import Path

import pytest
import yaml
from ase import Atoms

from dpmoire_lite import collect_models as models
from dpmoire_lite.mlab import MlabConfiguration, MlabIdentity
from dpmoire_lite.paths import relative_to_workdir


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
