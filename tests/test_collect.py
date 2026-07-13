from contextlib import contextmanager
import shutil
from pathlib import Path

import numpy as np
import pytest
import yaml
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator
from ase.io import write as ase_write

import dpmoire_lite.collect as collect_module
import dpmoire_lite.collect_publish as collect_publish_module
import dpmoire_lite.dataset as dataset_module
from dpmoire_lite import atomic_io
from dpmoire_lite import collect_models as models
from dpmoire_lite.dataset import Dataset
from dpmoire_lite.collect import run_collect
from dpmoire_lite.file_lock import CollectFileLock, CollectLockError
from dpmoire_lite.manifest import Manifest, read_manifest, write_manifest
from dpmoire_lite.mlab import MlabConfiguration
from dpmoire_lite.paths import manifest_path


def write_collect_config(root, **overrides):
    input_dir = root / "input"
    script_dir = root / "scripts"
    potcar_dir = root / "potcars"
    input_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)
    potcar_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "dft_script": "DFT_script.sh",
        "potcar_dir": str(potcar_dir),
        "script_dir": str(script_dir),
        "input_dir": str(input_dir),
        "work_dir": str(root / "work"),
        "n_nodes": 1,
        "stage": 0,
        "submit": False,
        "auto_resub": False,
        "vasp_ml": True,
        "outcar_collect_freq": 8,
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
    }
    data.update(overrides)
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def install_dataset_stream_spies(monkeypatch, streams, opened):
    @contextmanager
    def fake_open_outcar_frames(path):
        source_path = Path(path)
        opened.append(source_path)
        yield iter(streams[source_path])

    def reject_batch_reader(*_args, **_kwargs):
        raise AssertionError("batch OUTCAR reader used instead of streaming context")

    monkeypatch.setattr(
        dataset_module,
        "open_outcar_frames",
        fake_open_outcar_frames,
        raising=False,
    )
    monkeypatch.setattr(
        dataset_module,
        "read_outcar_frames",
        reject_batch_reader,
        raising=False,
    )


def _aggregate_status_api():
    collect_status = getattr(models, "CollectStatus", None)
    assert collect_status is not None, "CollectStatus is not implemented"
    collect_result = getattr(models, "CollectResult", None)
    assert collect_result is not None, "CollectResult is not implemented"
    selector = getattr(collect_module, "select_collect_status", None)
    assert callable(selector), "select_collect_status is not implemented"
    return collect_status, collect_result, selector


def _status_frame(x_position: float = 0.0) -> Atoms:
    return Atoms(
        "H",
        positions=[[x_position, 0.0, 0.0]],
        cell=[4.0, 4.0, 4.0],
        pbc=True,
    )


def _status_source_result(status, index: int, reason: str | None = None):
    accepted_frames = ()
    complete_count = 0
    if status in {models.SourceStatus.COMPLETE, models.SourceStatus.PARTIAL}:
        accepted_frames = (_status_frame(float(index)),)
        complete_count = 1
    if status is not models.SourceStatus.COMPLETE and reason is None:
        reason = f"structured {status.value} source"
    return models.SourceResult(
        source_path=f"md/{index}/OUTCAR",
        source_kind=models.SourceKind.OUTCAR,
        status=status,
        complete_count=complete_count,
        accepted_frames=accepted_frames,
        reason=reason,
    )


def _status_candidate(*statuses, reasons=()):
    if reasons and len(reasons) != len(statuses):
        raise ValueError("reasons must align with statuses")
    source_results = tuple(
        _status_source_result(
            status,
            index,
            reasons[index] if reasons else None,
        )
        for index, status in enumerate(statuses)
    )
    accepted_frames = tuple(
        frame
        for source_result in source_results
        for frame in source_result.accepted_frames
    )
    return models.CollectionCandidate(
        source_results=source_results,
        accepted_frames=accepted_frames,
        expected_directories=tuple(
            f"md/{index}" for index in range(len(source_results))
        ),
    )


def _status_mlab_configuration() -> MlabConfiguration:
    return MlabConfiguration(
        source_path=Path("md/0/ML_ABN"),
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


def _full_dedup_status_candidate(*, coverage_known: bool, duplicate: bool = False):
    seen = 2 if duplicate else 1
    source_path = "md/0/ML_ABN"
    configurations = tuple(_status_mlab_configuration() for _ in range(seen))
    source_result = models.SourceResult(
        source_path=source_path,
        source_kind=models.SourceKind.MLAB,
        status=models.SourceStatus.COMPLETE,
        complete_count=seen,
        parsed_configurations=configurations,
    )
    if coverage_known:
        inventory = models.SourceInventory(
            manifest_kind="current",
            directory_discovery="declared",
            directories=("md/0",),
            coverage_known=True,
            warnings=("fatal degraded skipped log text is not status evidence",),
        )
    else:
        inventory = models.SourceInventory(
            manifest_kind="missing",
            directory_discovery="legacy-scan",
            directories=("md/0",),
            coverage_known=False,
            warnings=("legacy scan cannot prove expected-source coverage",),
        )
    source_stats = models.SourceDedupStats(
        source_path=source_path,
        seen=seen,
        retained=1,
        duplicates_removed=seen - 1,
    )
    dedup_stats = models.DedupStats(
        seen=seen,
        unique=1,
        duplicates_removed=seen - 1,
        candidate_frame_count=1,
        per_source=(source_stats,),
    )
    return models.CollectionCandidate(
        source_results=(source_result,),
        accepted_frames=(_status_frame(),),
        expected_directories=inventory.directories,
        collection_mode=models.MLFFCollectMode.FULL_DEDUP,
        source_inventory=inventory,
        dedup_stats=dedup_stats,
    )


def test_complete_requires_frames_and_all_expected_sources_complete():
    CollectStatus, CollectResult, select_collect_status = _aggregate_status_api()

    candidate = _status_candidate(
        models.SourceStatus.COMPLETE,
        models.SourceStatus.COMPLETE,
    )

    assert tuple(status.value for status in CollectStatus) == (
        "complete",
        "degraded",
        "no_data",
        "fatal",
    )
    assert select_collect_status(candidate) is CollectStatus.COMPLETE
    with pytest.raises(ValueError, match="publication"):
        CollectResult(status=CollectStatus.COMPLETE, candidate=candidate)
    result = CollectResult(
        status=CollectStatus.COMPLETE,
        candidate=candidate,
        publication_committed=True,
    )
    assert result.accepted_frame_count == 2
    assert result.sources_complete == 2


def test_partial_with_frames_selects_degraded():
    CollectStatus, CollectResult, select_collect_status = _aggregate_status_api()

    candidate = _status_candidate(models.SourceStatus.PARTIAL)

    assert select_collect_status(candidate) is CollectStatus.DEGRADED
    result = CollectResult(
        status=CollectStatus.DEGRADED,
        candidate=candidate,
        publication_committed=True,
    )
    assert result.sources_partial == 1


def test_skipped_or_failed_with_other_frames_selects_degraded():
    CollectStatus, _CollectResult, select_collect_status = _aggregate_status_api()

    candidate = _status_candidate(
        models.SourceStatus.COMPLETE,
        models.SourceStatus.SKIPPED,
        models.SourceStatus.FAILED,
    )

    assert select_collect_status(candidate) is CollectStatus.DEGRADED


def test_coverage_decline_with_frames_selects_degraded():
    CollectStatus, _CollectResult, select_collect_status = _aggregate_status_api()

    candidate = _status_candidate(models.SourceStatus.COMPLETE)

    assert select_collect_status(
        candidate,
        coverage_declined=True,
    ) is CollectStatus.DEGRADED


def test_unknown_coverage_legacy_scan_with_frames_selects_degraded():
    CollectStatus, _CollectResult, select_collect_status = _aggregate_status_api()

    candidate = _full_dedup_status_candidate(coverage_known=False)

    assert select_collect_status(candidate) is CollectStatus.DEGRADED


def test_exact_duplicates_alone_do_not_select_degraded():
    CollectStatus, _CollectResult, select_collect_status = _aggregate_status_api()

    candidate = _full_dedup_status_candidate(
        coverage_known=True,
        duplicate=True,
    )

    assert candidate.dedup_stats.duplicates_removed == 1
    assert select_collect_status(candidate) is CollectStatus.COMPLETE


def test_zero_frames_selects_no_data_before_publication():
    CollectStatus, CollectResult, select_collect_status = _aggregate_status_api()

    candidate = _status_candidate(
        models.SourceStatus.SKIPPED,
        models.SourceStatus.FAILED,
    )

    assert select_collect_status(candidate) is CollectStatus.NO_DATA
    with pytest.raises(ValueError, match="publication"):
        CollectResult(status=CollectStatus.NO_DATA, candidate=candidate)
    result = CollectResult(
        status=CollectStatus.NO_DATA,
        candidate=candidate,
        publication_committed=True,
    )
    assert result.accepted_frame_count == 0


def test_global_invariant_failure_selects_fatal():
    CollectStatus, CollectResult, select_collect_status = _aggregate_status_api()

    diagnostic = "candidate/source count invariant failed"

    assert select_collect_status(
        None,
        fatal_diagnostic=diagnostic,
    ) is CollectStatus.FATAL
    result = CollectResult(
        status=CollectStatus.FATAL,
        fatal_diagnostic=diagnostic,
    )
    assert result.fatal_diagnostic == diagnostic
    assert result.accepted_frame_count == 0
    with pytest.raises(ValueError, match="fatal.*publication"):
        CollectResult(
            status=CollectStatus.FATAL,
            publication_committed=True,
            fatal_diagnostic=diagnostic,
        )


def test_status_selection_does_not_parse_log_text():
    CollectStatus, _CollectResult, select_collect_status = _aggregate_status_api()

    complete_candidate = _status_candidate(
        models.SourceStatus.COMPLETE,
        reasons=("fatal failed partial degraded no_data",),
    )
    partial_candidate = _status_candidate(
        models.SourceStatus.PARTIAL,
        reasons=("complete transaction committed",),
    )

    assert select_collect_status(complete_candidate) is CollectStatus.COMPLETE
    assert select_collect_status(partial_candidate) is CollectStatus.DEGRADED


def test_load_ml_abn_sample_counts_frames(tmp_path, sample_dir):
    source = sample_dir / "ML_ABN"
    target = tmp_path / "ML_ABN"
    shutil.copy2(source, target)
    dataset = Dataset()
    dataset.load_ml_ab(target)
    assert dataset.n_configs > 0
    assert len(dataset.data) == dataset.n_configs


def test_load_outcar_sample_counts_frames(tmp_path, sample_dir):
    source = sample_dir / "lambda_p0p00" / "OUTCAR"
    target = tmp_path / "OUTCAR"
    shutil.copy2(source, target)
    dataset = Dataset()
    dataset.load_outcar(target, freq=1)
    assert dataset.n_configs > 0


def test_load_outcar_rejects_non_positive_frequency():
    with pytest.raises(ValueError, match="positive"):
        Dataset().load_outcar(Path("whatever"), freq=0)


def test_collect_ml_md_writes_extxyz_and_manifest_counts(tmp_path, sample_dir):
    config_path = write_collect_config(tmp_path, stage=0, vasp_ml=True)
    work = tmp_path / "work"
    md_dir = work / "md" / "0_0"
    md_dir.mkdir(parents=True)
    shutil.copy2(sample_dir / "ML_ABN", md_dir / "ML_ABN")
    write_manifest(
        work,
        Manifest(stage="md", generated_at="test", directories=["md/0_0"]),
    )

    run_collect(config_path, stage="md")

    assert (work / "MD_data.extxyz").is_file()
    manifest = read_manifest(work, "md")
    assert manifest.schema_version == 2
    assert manifest.collect["frames"] > 0
    assert manifest.collect["output"] == "MD_data.extxyz"


def test_collect_ml_md_records_missing_ml_abn_without_crashing(tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=True)
    work = tmp_path / "work"
    (work / "md" / "0_0").mkdir(parents=True)
    write_manifest(
        work,
        Manifest(stage="md", generated_at="test", directories=["md/0_0"]),
    )

    run_collect(config_path, stage="md")

    manifest = read_manifest(work, "md")
    records = manifest.skipped + manifest.failed
    assert records
    assert any("md/0_0/ML_ABN" in record["path"] for record in records)
    assert any("ML_ABN" in record["reason"] for record in records)


def test_collect_ml_md_skips_existing_ml_ab_prefix(tmp_path, sample_dir):
    config_path = write_collect_config(tmp_path, vasp_ml=True)
    work = tmp_path / "work"
    md_dir = work / "md" / "0_0"
    md_dir.mkdir(parents=True)
    shutil.copy2(sample_dir / "ML_ABN", md_dir / "ML_ABN")
    shutil.copy2(sample_dir / "ML_ABN", md_dir / "ML_AB")
    write_manifest(
        work,
        Manifest(stage="md", generated_at="test", directories=["md/0_0"]),
    )

    run_collect(config_path, stage="md")

    manifest = read_manifest(work, "md")
    assert manifest.collect["frames"] == 0
    assert manifest.collect["sources"] == 1


def test_collect_ml_md_skips_source_when_ml_ab_count_fails(tmp_path, sample_dir):
    config_path = write_collect_config(tmp_path, vasp_ml=True)
    work = tmp_path / "work"
    md_dir = work / "md" / "0_0"
    md_dir.mkdir(parents=True)
    shutil.copy2(sample_dir / "ML_ABN", md_dir / "ML_ABN")
    (md_dir / "ML_AB").write_text("not\nan\nML_AB\nfile\nbad-count\n", encoding="utf-8")
    write_manifest(
        work,
        Manifest(stage="md", generated_at="test", directories=["md/0_0"]),
    )

    run_collect(config_path, stage="md")

    manifest = read_manifest(work, "md")
    assert not (work / "MD_data.extxyz").exists()
    assert manifest.collect["frames"] == 0
    assert manifest.collect["sources"] == 0
    assert manifest.collect["written"] is False
    assert any(
        record["path"] == "md/0_0/ML_AB"
        and "Could not read ML_AB count" in record["reason"]
        for record in manifest.failed
    )


def _no_data_publication_api():
    publisher = getattr(collect_module, "publish_no_data_candidate", None)
    assert callable(publisher), "publish_no_data_candidate is not implemented"
    return publisher


def _seed_aware_no_data_candidate(
    directories: tuple[str, ...] = ("md/run-a",),
):
    source_path = f"{directories[0]}/ML_ABN" if directories else "md/ML_ABN"
    source_result = models.SourceResult(
        source_path=source_path,
        source_kind=models.SourceKind.MLAB,
        status=models.SourceStatus.SKIPPED,
        complete_count=0,
        reason="source contained no publishable configurations",
        line_number=17,
    )
    return models.CollectionCandidate(
        source_results=(source_result,),
        accepted_frames=(),
        expected_directories=directories,
    )


def _missing_full_dedup_no_data_candidate():
    source_path = "md/run-a/ML_ABN"
    source_result = models.SourceResult(
        source_path=source_path,
        source_kind=models.SourceKind.MLAB,
        status=models.SourceStatus.FAILED,
        complete_count=0,
        reason="source could not supply a complete configuration",
        line_number=23,
    )
    inventory = models.SourceInventory(
        manifest_kind="missing",
        directory_discovery="legacy-scan",
        directories=("md/run-a",),
        coverage_known=False,
        warnings=("legacy scan cannot prove expected-source coverage",),
    )
    source_stats = models.SourceDedupStats(
        source_path=source_path,
        seen=0,
        retained=0,
        duplicates_removed=0,
    )
    dedup_stats = models.DedupStats(
        seen=0,
        unique=0,
        duplicates_removed=0,
        candidate_frame_count=0,
        per_source=(source_stats,),
    )
    return models.CollectionCandidate(
        source_results=(source_result,),
        accepted_frames=(),
        expected_directories=inventory.directories,
        collection_mode=models.MLFFCollectMode.FULL_DEDUP,
        source_inventory=inventory,
        dedup_stats=dedup_stats,
    )


def _write_current_no_data_manifest(
    work: Path,
    directories: tuple[str, ...] = ("md/run-a",),
):
    write_manifest(
        work,
        Manifest(
            stage="md",
            generated_at="no-data-test",
            directories=list(directories),
            collect={"sentinel": "previous manifest bytes"},
        ),
    )
    return read_manifest(work, "md")


def _write_previous_extxyz(path: Path, *, frame_count: int = 1) -> bytes:
    frames = []
    for index in range(frame_count):
        atoms = Atoms(
            "H",
            positions=[[0.1 * index, 0.0, 0.0]],
            cell=np.diag([4.0, 4.0, 4.0]),
            pbc=True,
        )
        atoms.calc = SinglePointCalculator(
            atoms,
            energy=-1.0 - index,
            forces=np.zeros((1, 3), dtype=float),
            stress=np.zeros(6, dtype=float),
        )
        frames.append(atoms)
    ase_write(path, frames, format="extxyz")
    return path.read_bytes()


def _current_collect_record(work: Path):
    data = yaml.safe_load(manifest_path(work, "md").read_text(encoding="utf-8"))
    return data["collect"]


def test_no_data_preserves_existing_output_bytes(tmp_path):
    publish_no_data_candidate = _no_data_publication_api()
    work = tmp_path / "work"
    manifest = _write_current_no_data_manifest(work)
    candidate = _seed_aware_no_data_candidate()
    output = work / "MD_data.extxyz"
    previous_bytes = _write_previous_extxyz(output)

    result = publish_no_data_candidate(
        work_dir=work,
        stage="md",
        manifest=manifest,
        candidate=candidate,
        collection_mode=models.MLFFCollectMode.SEED_AWARE,
        transaction_id="plan10-task2-preserve-output",
    )

    assert result.status is models.CollectStatus.NO_DATA
    assert result.publication_committed is True
    assert output.read_bytes() == previous_bytes


def test_no_data_without_previous_output_creates_no_extxyz(tmp_path):
    publish_no_data_candidate = _no_data_publication_api()
    work = tmp_path / "work"
    manifest = _write_current_no_data_manifest(work)
    candidate = _seed_aware_no_data_candidate()
    output = work / "MD_data.extxyz"

    result = publish_no_data_candidate(
        work_dir=work,
        stage="md",
        manifest=manifest,
        candidate=candidate,
        collection_mode=models.MLFFCollectMode.SEED_AWARE,
        transaction_id="plan10-task2-first-no-data",
    )

    assert result.status is models.CollectStatus.NO_DATA
    assert result.publication_committed is True
    assert not output.exists()
    assert _current_collect_record(work)["preserved_previous_output"] is False


def test_no_data_records_preserved_previous_and_previous_frame_count(tmp_path):
    publish_no_data_candidate = _no_data_publication_api()
    work = tmp_path / "work"
    manifest = _write_current_no_data_manifest(work)
    candidate = _seed_aware_no_data_candidate()
    output = work / "MD_data.extxyz"
    _write_previous_extxyz(output, frame_count=2)
    previous_sha256 = atomic_io.sha256_file(output)

    publish_no_data_candidate(
        work_dir=work,
        stage="md",
        manifest=manifest,
        candidate=candidate,
        collection_mode=models.MLFFCollectMode.SEED_AWARE,
        transaction_id="plan10-task2-previous-count",
    )

    collect = _current_collect_record(work)
    assert collect["preserved_previous_output"] is True
    assert collect["previous_output_frames"] == 2
    assert collect["output_sha256"] == previous_sha256
    assert collect["sources"] == [candidate.source_results[0].as_diagnostic()]


def test_no_data_manifest_only_publish_uses_stage_output_lock(
    tmp_path,
    monkeypatch,
):
    publish_no_data_candidate = _no_data_publication_api()
    work = tmp_path / "work"
    manifest = _write_current_no_data_manifest(work)
    candidate = _seed_aware_no_data_candidate()
    observed = []
    real_publish = collect_publish_module.PublicationSession.publish_manifest_only

    def assert_lock_then_publish(session, request):
        with pytest.raises(CollectLockError):
            with CollectFileLock(
                request.stage,
                request.final_output,
                transaction_id="plan10-task2-contender",
            ):
                pass
        observed.append((request.stage, request.final_output))
        return real_publish(session, request)

    monkeypatch.setattr(
        collect_publish_module.PublicationSession,
        "publish_manifest_only",
        assert_lock_then_publish,
    )

    result = publish_no_data_candidate(
        work_dir=work,
        stage="md",
        manifest=manifest,
        candidate=candidate,
        collection_mode=models.MLFFCollectMode.SEED_AWARE,
        transaction_id="plan10-task2-lock",
    )

    assert result.status is models.CollectStatus.NO_DATA
    assert observed == [("md", work / "MD_data.extxyz")]


def test_no_data_manifest_failure_returns_fatal_not_no_data(
    tmp_path,
    monkeypatch,
):
    publish_no_data_candidate = _no_data_publication_api()
    work = tmp_path / "work"
    manifest = _write_current_no_data_manifest(work)
    candidate = _seed_aware_no_data_candidate()
    target = manifest_path(work, "md")
    real_replace = collect_publish_module.os.replace

    def fail_manifest_replace(source, destination, *args, **kwargs):
        if Path(destination) == target:
            raise OSError("synthetic no-data manifest replacement failure")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(collect_publish_module.os, "replace", fail_manifest_replace)

    result = publish_no_data_candidate(
        work_dir=work,
        stage="md",
        manifest=manifest,
        candidate=candidate,
        collection_mode=models.MLFFCollectMode.SEED_AWARE,
        transaction_id="plan10-task2-fatal",
    )

    assert result.status is models.CollectStatus.FATAL
    assert result.status is not models.CollectStatus.NO_DATA
    assert result.publication_committed is False
    assert result.candidate is not None
    assert result.candidate.source_results[0].as_diagnostic() == (
        candidate.source_results[0].as_diagnostic()
    )
    assert "synthetic no-data manifest replacement failure" in result.fatal_diagnostic


def test_no_data_failure_preserves_previous_manifest(tmp_path, monkeypatch):
    publish_no_data_candidate = _no_data_publication_api()
    work = tmp_path / "work"
    manifest = _write_current_no_data_manifest(work)
    candidate = _seed_aware_no_data_candidate()
    target = manifest_path(work, "md")
    previous_manifest_bytes = target.read_bytes()
    output = work / "MD_data.extxyz"
    previous_output_bytes = _write_previous_extxyz(output)
    real_replace = collect_publish_module.os.replace

    def fail_manifest_replace(source, destination, *args, **kwargs):
        if Path(destination) == target:
            raise OSError("synthetic preserved-manifest failure")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(collect_publish_module.os, "replace", fail_manifest_replace)

    result = publish_no_data_candidate(
        work_dir=work,
        stage="md",
        manifest=manifest,
        candidate=candidate,
        collection_mode=models.MLFFCollectMode.SEED_AWARE,
        transaction_id="plan10-task2-preserve-manifest",
    )

    assert result.status is models.CollectStatus.FATAL
    assert target.read_bytes() == previous_manifest_bytes
    assert output.read_bytes() == previous_output_bytes
    assert not list(work.rglob("*.candidate"))


def test_no_data_creates_no_data_candidate_or_two_file_journal(
    tmp_path,
    monkeypatch,
):
    publish_no_data_candidate = _no_data_publication_api()
    work = tmp_path / "work"
    manifest = _write_current_no_data_manifest(work)
    candidate = _seed_aware_no_data_candidate()
    output = work / "MD_data.extxyz"

    def reject_data_candidate(*_args, **_kwargs):
        raise AssertionError("no-data publication prepared an extxyz candidate")

    def reject_two_file_journal(*_args, **_kwargs):
        raise AssertionError("no-data publication prepared a two-file journal")

    monkeypatch.setattr(
        collect_publish_module,
        "_prepare_data_candidate",
        reject_data_candidate,
    )
    monkeypatch.setattr(
        collect_publish_module,
        "_publish_journal",
        reject_two_file_journal,
    )

    result = publish_no_data_candidate(
        work_dir=work,
        stage="md",
        manifest=manifest,
        candidate=candidate,
        collection_mode=models.MLFFCollectMode.SEED_AWARE,
        transaction_id="plan10-task2-manifest-only",
    )

    assert result.status is models.CollectStatus.NO_DATA
    assert not output.exists()
    assert not (work / ".MD_data.extxyz.collect-journal.yaml").exists()
    assert not list(work.rglob("*.candidate"))


def test_legacy_no_data_writes_only_compatibility_result_manifest(tmp_path):
    publish_no_data_candidate = _no_data_publication_api()
    work = tmp_path / "work"
    legacy_path = manifest_path(work, "md")
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        "stage: md\ndirectories:\n  - md/run-a\nlegacy_key: preserve-me\n",
        encoding="utf-8",
    )
    legacy_bytes = legacy_path.read_bytes()
    manifest = read_manifest(work, "md")
    candidate = _seed_aware_no_data_candidate()

    result = publish_no_data_candidate(
        work_dir=work,
        stage="md",
        manifest=manifest,
        candidate=candidate,
        collection_mode=models.MLFFCollectMode.SEED_AWARE,
        transaction_id="plan10-task2-legacy",
    )

    compatibility_path = work / "MD_data.collect.yaml"
    compatibility = yaml.safe_load(compatibility_path.read_text(encoding="utf-8"))
    assert result.status is models.CollectStatus.NO_DATA
    assert legacy_path.read_bytes() == legacy_bytes
    assert compatibility_path.is_file()
    assert compatibility["input_layout"] == "legacy-stage-manifest"
    assert compatibility["directory_discovery"] == "declared"
    assert compatibility["declared_directories"] == ["md/run-a"]
    assert compatibility["discovered_directories"] == []
    assert compatibility["collection_mode"] == "seed-aware"
    assert compatibility["dedup"] == {
        "applied": False,
        "schema": None,
        "seen": 0,
        "unique": 0,
        "duplicates_removed": 0,
        "candidate_frame_count": 0,
        "per_source": [
            {
                "source_path": "md/run-a/ML_ABN",
                "seen": 0,
                "retained": 0,
                "duplicates_removed": 0,
            }
        ],
    }
    assert compatibility["collect"]["sources"] == [
        candidate.source_results[0].as_diagnostic()
    ]


def test_missing_scan_no_data_does_not_create_stage_manifest(tmp_path):
    publish_no_data_candidate = _no_data_publication_api()
    work = tmp_path / "work"
    manifest = read_manifest(work, "md")
    candidate = _missing_full_dedup_no_data_candidate()

    result = publish_no_data_candidate(
        work_dir=work,
        stage="md",
        manifest=manifest,
        candidate=candidate,
        collection_mode=models.MLFFCollectMode.FULL_DEDUP,
        transaction_id="plan10-task2-missing",
    )

    stage_manifest = manifest_path(work, "md")
    compatibility_path = work / "MD_data.collect.yaml"
    compatibility = yaml.safe_load(compatibility_path.read_text(encoding="utf-8"))
    assert result.status is models.CollectStatus.NO_DATA
    assert not stage_manifest.exists()
    assert compatibility_path.is_file()
    assert compatibility["input_layout"] == "missing-stage-manifest"
    assert compatibility["directory_discovery"] == "legacy-scan"
    assert compatibility["declared_directories"] == []
    assert compatibility["discovered_directories"] == ["md/run-a"]
    assert compatibility["collection_mode"] == "full-dedup"
    assert compatibility["dedup"] == {
        "applied": True,
        "schema": "mlab-config-v1",
        "seen": 0,
        "unique": 0,
        "duplicates_removed": 0,
        "candidate_frame_count": 0,
        "per_source": [
            {
                "source_path": "md/run-a/ML_ABN",
                "seen": 0,
                "retained": 0,
                "duplicates_removed": 0,
            }
        ],
    }


def test_collect_validation_uses_all_ionic_steps(monkeypatch, tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=False, outcar_collect_freq=11)
    work = tmp_path / "work"
    validation_dir = work / "validation" / "1.00deg"
    validation_dir.mkdir(parents=True)
    write_manifest(
        work,
        Manifest(stage="validation", generated_at="test", directories=["validation/1.00deg"]),
    )
    seen_freqs = []

    class FakeDataset:
        def __init__(self):
            self.n_configs = 0

        def load_outcar(self, path, freq):
            seen_freqs.append(freq)
            self.n_configs += 1

        def save_extxyz(self, path):
            Path(path).write_text("fake extxyz\n", encoding="utf-8")

    monkeypatch.setattr(collect_module, "Dataset", FakeDataset, raising=False)
    monkeypatch.setattr(
        collect_module,
        "find_outcar_series",
        lambda directory, patterns: [Path(directory) / "OUTCAR"],
        raising=False,
    )

    run_collect(config_path, stage="validation")

    assert seen_freqs == [1]
    manifest = read_manifest(work, "validation")
    assert manifest.collect["frames"] == 1


def test_collect_missing_stage_manifest_fails_without_output_or_manifest(tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=False)
    work = tmp_path / "work"

    with pytest.raises(Exception) as exc_info:
        run_collect(config_path, stage="rlx")

    message = str(exc_info.value).lower()
    assert "manifest" in message
    assert "rlx" in message
    assert not (work / "rlx_data.extxyz").exists()
    assert not (work / "rlx" / "manifest.yaml").exists()


def test_collect_does_not_derive_directories_from_config(tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=False, n_sectors=[2, 1])
    work = tmp_path / "work"
    (work / "rlx" / "0_0").mkdir(parents=True)

    with pytest.raises(Exception) as exc_info:
        run_collect(config_path, stage="rlx")

    message = str(exc_info.value).lower()
    assert "manifest" in message
    assert "rlx" in message
    assert not (work / "rlx_data.extxyz").exists()
    assert not (work / "rlx" / "manifest.yaml").exists()


def test_collect_does_not_scan_validation_directories_without_manifest(tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=False)
    work = tmp_path / "work"
    (work / "validation" / "1.00deg").mkdir(parents=True)

    with pytest.raises(Exception) as exc_info:
        run_collect(config_path, stage="validation")

    message = str(exc_info.value).lower()
    assert "manifest" in message
    assert "validation" in message
    assert not (work / "valid.extxyz").exists()
    assert not (work / "validation" / "manifest.yaml").exists()


def test_stream_sampling_freq_one_accepts_every_frame(tmp_path, monkeypatch):
    path = tmp_path / "segment.OUTCAR"
    frames = ["frame-0", "frame-1", "frame-2", "frame-3"]
    opened = []
    install_dataset_stream_spies(monkeypatch, {path: frames}, opened)
    accepted = []
    dataset = Dataset()
    dataset.add_atoms = accepted.append

    dataset.load_outcar(path, freq=1)

    assert accepted == frames
    assert opened == [path]


def test_stream_sampling_accepts_zero_n_2n_per_file(tmp_path, monkeypatch):
    path = tmp_path / "segment.OUTCAR"
    frames = [f"frame-{index}" for index in range(6)]
    opened = []
    install_dataset_stream_spies(monkeypatch, {path: frames}, opened)
    accepted = []
    dataset = Dataset()
    dataset.add_atoms = accepted.append

    dataset.load_outcar(path, freq=2)

    assert accepted == ["frame-0", "frame-2", "frame-4"]
    assert opened == [path]


def test_sampling_index_resets_for_each_outcar_segment(tmp_path, monkeypatch):
    first_path = tmp_path / "first.OUTCAR"
    second_path = tmp_path / "second.OUTCAR"
    streams = {
        first_path: ["first-0", "first-1", "first-2"],
        second_path: ["second-0", "second-1", "second-2"],
    }
    opened = []
    install_dataset_stream_spies(monkeypatch, streams, opened)
    accepted = []
    dataset = Dataset()
    dataset.add_atoms = accepted.append

    dataset.load_outcar(first_path, freq=2)
    dataset.load_outcar(second_path, freq=2)

    assert accepted == ["first-0", "first-2", "second-0", "second-2"]
    assert opened == [first_path, second_path]


def test_nonpositive_frequency_fails_before_opening_file(monkeypatch):
    def fail_open(*_args, **_kwargs):
        raise AssertionError("streaming context opened before frequency validation")

    def fail_batch(*_args, **_kwargs):
        raise AssertionError("batch reader opened before frequency validation")

    monkeypatch.setattr(
        dataset_module,
        "open_outcar_frames",
        fail_open,
        raising=False,
    )
    monkeypatch.setattr(
        dataset_module,
        "read_outcar_frames",
        fail_batch,
        raising=False,
    )

    for frequency in (0, -1):
        with pytest.raises(ValueError, match="freq must be a positive integer"):
            Dataset().load_outcar(Path("not-opened.OUTCAR"), freq=frequency)


def test_unselected_frames_are_not_retained_as_atoms(tmp_path, monkeypatch):
    path = tmp_path / "streaming.OUTCAR"
    frames = [f"frame-{index}" for index in range(6)]
    opened = []
    accepted = []

    @contextmanager
    def fake_open_outcar_frames(requested_path):
        source_path = Path(requested_path)
        opened.append(source_path)

        def stream():
            for index, frame in enumerate(frames):
                assert accepted == [
                    frames[previous]
                    for previous in range(index)
                    if previous % 2 == 0
                ]
                yield frame

        yield stream()

    def reject_batch_reader(*_args, **_kwargs):
        raise AssertionError("batch OUTCAR reader used instead of streaming context")

    monkeypatch.setattr(
        dataset_module,
        "open_outcar_frames",
        fake_open_outcar_frames,
        raising=False,
    )
    monkeypatch.setattr(
        dataset_module,
        "read_outcar_frames",
        reject_batch_reader,
        raising=False,
    )
    dataset = Dataset()
    dataset.add_atoms = accepted.append

    dataset.load_outcar(path, freq=2)

    assert accepted == ["frame-0", "frame-2", "frame-4"]
    assert opened == [path]
