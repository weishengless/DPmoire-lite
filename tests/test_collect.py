from contextlib import contextmanager
from dataclasses import replace
import shutil
from pathlib import Path

import numpy as np
import pytest
import yaml
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator
from ase.io import read as ase_read
from ase.io import write as ase_write

import dpmoire_lite.collect as collect_module
import dpmoire_lite.collect_publish as collect_publish_module
import dpmoire_lite.dataset as dataset_module
from dpmoire_lite import atomic_io
from dpmoire_lite.build import run_build
from dpmoire_lite.cli import main
from dpmoire_lite import collect_models as models
from dpmoire_lite.dataset import Dataset
from dpmoire_lite.collect import run_collect
from dpmoire_lite.file_lock import CollectFileLock, CollectLockError
from dpmoire_lite.manifest import Manifest, read_manifest, write_manifest
from dpmoire_lite.mlab import MlabConfiguration, parse_mlab
from dpmoire_lite.paths import manifest_path

from test_stage_provenance import prepare_stage1_case


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


TASK6_MLAB_FIXTURES = Path(__file__).parent / "data" / "mlab"
TASK6_OUTCAR_FIXTURE = (
    Path(__file__).parent / "data" / "outcar" / "complete_two_frame.OUTCAR"
)


def _prepare_task6_seeded_stage1(
    root: Path,
    *,
    seed_fixture: str = "complete_vasp_651.mlab",
):
    config_path = prepare_stage1_case(
        root,
        stage0_overrides={"n_sectors": [1, 1]},
        stage1_overrides={"vasp_ml": True},
    )
    work = root / "work"
    init_mlff = work / "init_mlff"
    init_mlff.mkdir(parents=True)
    shutil.copy2(
        TASK6_MLAB_FIXTURES / seed_fixture,
        init_mlff / "ML_ABN",
    )
    (init_mlff / "ML_FFN").write_text(
        "synthetic-initial-force-field\n",
        encoding="utf-8",
    )

    run_build(config_path, wait=False)

    manifest = read_manifest(work, "md").manifest
    assert manifest is not None
    assert manifest.directories == ["md/0_0"]
    assert manifest.mlff_seed["configurations"] == 1
    return config_path, work, work / "md" / "0_0", manifest


def _prepare_ticket5_vasp_rewrite_case(root: Path):
    config_path, work, md_directory, manifest = _prepare_task6_seeded_stage1(
        root,
        seed_fixture="seed_input_vasp_651.mlab",
    )
    shutil.copy2(
        TASK6_MLAB_FIXTURES / "seed_rewrite_postseed_vasp_651.mlab",
        md_directory / "ML_ABN",
    )
    return config_path, work, md_directory, manifest


def _walk_manifest_values(value):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_manifest_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_manifest_values(item)


def _copy_task6_final_mlab(directory: Path) -> Path:
    target = directory / "ML_ABN"
    shutil.copy2(TASK6_MLAB_FIXTURES / "complete_multi.mlab", target)
    return target


def _write_task6_three_configuration_mlab(target: Path) -> None:
    text = (TASK6_MLAB_FIXTURES / "complete_multi.mlab").read_text(
        encoding="utf-8"
    )
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
    lines[value_index] = f"         3{newline}"
    text = "".join(lines)

    second_marker = "     Configuration num.      2"
    third_block = text[text.index(second_marker) :]
    third_block = third_block.replace(
        second_marker,
        "     Configuration num.      3",
        1,
    )
    third_block = third_block.replace("synthetic-b", "synthetic-c", 1)
    third_block = third_block.replace(
        "  -1.200000000000000E+000",
        "  -1.100000000000000E+000",
        1,
    )
    target.write_text(text + third_block, encoding="utf-8")


def test_stage1_seed_manifest_is_accepted_by_md_collect(tmp_path):
    config_path, work, md_directory, producer_manifest = (
        _prepare_task6_seeded_stage1(tmp_path)
    )
    _copy_task6_final_mlab(md_directory)
    seed_evidence = dict(producer_manifest.mlff_seed)

    result = run_collect(config_path, stage="md")

    source = result.source_results[0]
    output_frames = ase_read(work / "MD_data.extxyz", format="extxyz", index=":")
    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    assert result.status is models.CollectStatus.COMPLETE
    assert result.publication_committed is True
    assert result.accepted_frame_count == len(output_frames) == 1
    assert result.source_count == 1
    assert source.source_path == "md/0_0/ML_ABN"
    assert source.status is models.SourceStatus.COMPLETE
    assert source.complete_count == 2
    assert source.accepted_count == 1
    assert source.seed_identity is not None
    assert source.seed_identity.sha256 == seed_evidence["seed_prefix_sha256"]
    assert published_manifest.mlff_seed == seed_evidence
    assert published_manifest.collect["status"] == "complete"
    assert published_manifest.collect["collection_mode"] == "seed-aware"
    assert published_manifest.collect["frames"] == 1
    assert published_manifest.collect["dedup"]["seed_verification"] == {
        "schema": "vasp-seed-prefix-equivalence-v1",
        "exact": 1,
        "vasp_equivalent": 0,
        "mismatch": 0,
    }
    assert published_manifest.collect["sources"][0]["seed_verification"][
        "status"
    ] == "exact"


def test_collect_manifest_records_seed_verification_counts_and_deltas(tmp_path):
    config_path, work, _md_directory, _manifest = (
        _prepare_ticket5_vasp_rewrite_case(tmp_path)
    )

    result = run_collect(config_path, stage="md")

    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    collect = published_manifest.collect
    assert result.status is models.CollectStatus.COMPLETE
    assert collect["dedup"]["seed_verification"] == {
        "schema": "vasp-seed-prefix-equivalence-v1",
        "exact": 0,
        "vasp_equivalent": 1,
        "mismatch": 0,
    }
    verification = collect["sources"][0]["seed_verification"]
    assert verification["status"] == "vasp_equivalent"
    assert verification["configurations"] == 1
    assert verification["max_deltas"]["positions"]["absolute"] == pytest.approx(
        1.0125233984581428e-13
    )
    assert verification["max_deltas"]["forces"]["absolute"] == pytest.approx(
        1.3877787807814457e-17
    )


def test_vasp_equivalent_source_publishes_complete_when_coverage_is_complete(
    tmp_path,
):
    config_path, work, _md_directory, _manifest = (
        _prepare_ticket5_vasp_rewrite_case(tmp_path)
    )

    result = run_collect(config_path, stage="md")

    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    collect = published_manifest.collect
    assert result.status is models.CollectStatus.COMPLETE
    assert result.sources_complete == 1
    assert result.sources_partial == 0
    assert result.sources_skipped == 0
    assert result.sources_failed == 0
    assert result.coverage_declined is False
    assert collect["status"] == "complete"
    assert collect["sources"][0]["seed_verification"]["status"] == (
        "vasp_equivalent"
    )


def test_vasp_equivalent_source_publishes_degraded_only_for_missing_coverage(
    tmp_path,
):
    config_path, work, _md_directory, manifest = (
        _prepare_ticket5_vasp_rewrite_case(tmp_path)
    )
    manifest.directories.append("md/missing")
    write_manifest(work, manifest)

    result = run_collect(config_path, stage="md")

    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    collect = published_manifest.collect
    assert result.status is models.CollectStatus.DEGRADED
    assert result.accepted_frame_count == 1
    assert result.sources_complete == 1
    assert result.sources_partial == 0
    assert result.sources_skipped == 1
    assert result.sources_failed == 0
    assert result.coverage_declined is False
    assert collect["status"] == "degraded"
    assert collect["dedup"]["seed_verification"] == {
        "schema": "vasp-seed-prefix-equivalence-v1",
        "exact": 0,
        "vasp_equivalent": 1,
        "mismatch": 0,
    }
    assert collect["sources"][0]["seed_verification"]["status"] == (
        "vasp_equivalent"
    )
    assert collect["sources"][1]["status"] == "skipped"
    assert "seed_verification" not in collect["sources"][1]


def test_seed_verification_mismatch_publishes_source_failure_diagnostic(tmp_path):
    config_path, work, _md_directory, manifest = (
        _prepare_ticket5_vasp_rewrite_case(tmp_path)
    )
    mismatch_directory = work / "md" / "mismatch"
    mismatch_directory.mkdir()
    shutil.copy2(
        TASK6_MLAB_FIXTURES / "complete_vasp_641.mlab",
        mismatch_directory / "ML_ABN",
    )
    manifest.directories.append("md/mismatch")
    write_manifest(work, manifest)

    result = run_collect(config_path, stage="md")

    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    collect = published_manifest.collect
    assert result.status is models.CollectStatus.DEGRADED
    assert result.accepted_frame_count == 1
    assert result.sources_complete == 1
    assert result.sources_failed == 1
    assert collect["dedup"]["seed_verification"] == {
        "schema": "vasp-seed-prefix-equivalence-v1",
        "exact": 0,
        "vasp_equivalent": 1,
        "mismatch": 1,
    }
    failure = collect["sources"][1]
    assert failure["path"] == "md/mismatch/ML_ABN"
    assert failure["status"] == "failed"
    assert failure["accepted_count"] == 0
    assert failure["seed_verification"]["status"] == "mismatch"
    assert failure["seed_verification"]["first_mismatch"] == {
        "configuration_index": 0,
        "field": "elements",
        "component": [0],
        "expected": "O",
        "actual": "C",
        "absolute": None,
        "scaled": None,
        "reason": "structural_mismatch",
    }


def test_no_data_and_previous_output_preservation_are_unchanged(tmp_path):
    config_path, work, md_directory, _manifest = (
        _prepare_ticket5_vasp_rewrite_case(tmp_path)
    )
    shutil.copy2(
        TASK6_MLAB_FIXTURES / "complete_vasp_641.mlab",
        md_directory / "ML_ABN",
    )
    output = work / "MD_data.extxyz"
    previous_bytes = _write_previous_extxyz(output, frame_count=2)
    previous_sha256 = atomic_io.sha256_file(output)

    result = run_collect(config_path, stage="md")

    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    collect = published_manifest.collect
    assert result.status is models.CollectStatus.NO_DATA
    assert result.accepted_frame_count == 0
    assert result.sources_failed == 1
    assert result.publication_committed is True
    assert output.read_bytes() == previous_bytes
    assert atomic_io.sha256_file(output) == previous_sha256
    assert collect["status"] == "no_data"
    assert collect["written"] is False
    assert collect["preserved_previous_output"] is True
    assert collect["output_sha256"] == previous_sha256
    assert collect["backup"] is None
    assert collect["sources"][0]["status"] == "failed"
    assert collect["sources"][0]["seed_verification"]["status"] == "mismatch"
    assert not (work / "backups").exists()


def test_vasp_rewrite_regression_publishes_only_post_seed_frames(tmp_path):
    config_path, work, md_directory, _manifest = (
        _prepare_ticket5_vasp_rewrite_case(tmp_path)
    )
    parsed_final = parse_mlab(md_directory / "ML_ABN")

    result = run_collect(config_path, stage="md")

    output_frames = ase_read(work / "MD_data.extxyz", format="extxyz", index=":")
    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    assert result.status is models.CollectStatus.COMPLETE
    assert result.accepted_frame_count == len(output_frames) == 1
    assert result.source_results[0].complete_count == 2
    assert result.source_results[0].accepted_count == 1
    assert published_manifest.collect["frames"] == 1
    np.testing.assert_allclose(
        output_frames[0].get_positions(),
        parsed_final.configurations[1].positions,
        rtol=0.0,
        atol=1e-14,
    )
    assert output_frames[0].get_potential_energy() == pytest.approx(
        parsed_final.configurations[1].energy
    )
    assert output_frames[0].get_potential_energy() != pytest.approx(
        parsed_final.configurations[0].energy
    )


def test_result_manifest_never_dumps_full_seed_configurations(tmp_path):
    config_path, work, _md_directory, _manifest = (
        _prepare_ticket5_vasp_rewrite_case(tmp_path)
    )

    result = run_collect(config_path, stage="md")

    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    assert result.status is models.CollectStatus.COMPLETE
    verification = published_manifest.collect["sources"][0]["seed_verification"]
    assert set(verification) == {
        "status",
        "schema",
        "configurations",
        "expected_exact_sha256",
        "actual_exact_sha256",
        "reference",
        "max_deltas",
    }
    assert set(verification["reference"]) == {
        "source",
        "raw_sha256",
        "trust",
    }
    assert set(verification["max_deltas"]) == {
        "lattice",
        "positions",
        "energy",
        "forces",
        "stress_kbar",
    }
    assert all(
        set(delta) == {"absolute", "scaled"}
        and all(isinstance(value, float) for value in delta.values())
        for delta in verification["max_deltas"].values()
    )
    assert not any(
        isinstance(value, (list, tuple))
        for value in _walk_manifest_values(verification)
    )


def test_multiple_restart_mlab_collects_all_post_initial_seed_frames(tmp_path):
    config_path, work, md_directory, producer_manifest = (
        _prepare_task6_seeded_stage1(tmp_path)
    )
    current_restart = md_directory / "ML_AB"
    shutil.copy2(TASK6_MLAB_FIXTURES / "complete_multi.mlab", current_restart)
    final_source = md_directory / "ML_ABN"
    _write_task6_three_configuration_mlab(final_source)
    parsed_final = parse_mlab(final_source)
    seed_evidence = dict(producer_manifest.mlff_seed)
    current_restart_bytes = current_restart.read_bytes()

    result = run_collect(config_path, stage="md")

    source = result.source_results[0]
    output_frames = ase_read(work / "MD_data.extxyz", format="extxyz", index=":")
    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    assert parsed_final.status == "complete"
    assert parsed_final.complete_count == 3
    assert result.status is models.CollectStatus.COMPLETE
    assert result.publication_committed is True
    assert result.accepted_frame_count == len(output_frames) == 2
    assert source.complete_count == 3
    assert source.accepted_count == 2
    assert source.seed_identity is not None
    assert source.seed_identity.sha256 == seed_evidence["seed_prefix_sha256"]
    assert published_manifest.mlff_seed == seed_evidence
    assert published_manifest.collect["frames"] == 2
    assert current_restart.read_bytes() == current_restart_bytes


def test_default_and_explicit_seed_aware_outputs_match(tmp_path, capsys):
    default_config, default_work, default_md, _ = _prepare_task6_seeded_stage1(
        tmp_path / "default"
    )
    explicit_config, explicit_work, explicit_md, _ = _prepare_task6_seeded_stage1(
        tmp_path / "explicit"
    )
    _copy_task6_final_mlab(default_md)
    _copy_task6_final_mlab(explicit_md)

    default_exit = main(
        ["collect", str(default_config), "--stage", "md"]
    )
    default_cli = capsys.readouterr()
    explicit_exit = main(
        [
            "collect",
            str(explicit_config),
            "--stage",
            "md",
            "--mlff-collect-mode",
            "seed-aware",
        ]
    )
    explicit_cli = capsys.readouterr()

    default_manifest = read_manifest(default_work, "md").manifest
    explicit_manifest = read_manifest(explicit_work, "md").manifest
    assert default_manifest is not None
    assert explicit_manifest is not None
    assert default_exit == explicit_exit == 0
    assert default_cli.out == explicit_cli.out == ""
    assert default_cli.err == explicit_cli.err
    assert (default_work / "MD_data.extxyz").read_bytes() == (
        explicit_work / "MD_data.extxyz"
    ).read_bytes()
    stable_fields = (
        "status",
        "frames",
        "sources",
        "source_order",
        "collection_mode",
        "expected_directories",
        "output_sha256",
    )
    assert {
        field: default_manifest.collect[field] for field in stable_fields
    } == {
        field: explicit_manifest.collect[field] for field in stable_fields
    }
    assert default_manifest.collect["collection_mode"] == "seed-aware"


def test_build_then_collect_uses_only_manifest_declared_directories(tmp_path):
    config_path, work, md_directory, producer_manifest = (
        _prepare_task6_seeded_stage1(tmp_path)
    )
    _copy_task6_final_mlab(md_directory)
    undeclared = work / "md" / "undeclared"
    undeclared.mkdir()
    _copy_task6_final_mlab(undeclared)

    result = run_collect(config_path, stage="md")

    output_frames = ase_read(work / "MD_data.extxyz", format="extxyz", index=":")
    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    assert result.status is models.CollectStatus.COMPLETE
    assert result.accepted_frame_count == len(output_frames) == 1
    assert result.candidate is not None
    assert result.candidate.expected_directories == ("md/0_0",)
    assert tuple(source.source_path for source in result.source_results) == (
        "md/0_0/ML_ABN",
    )
    assert published_manifest.directories == producer_manifest.directories
    assert published_manifest.collect["expected_directories"] == ["md/0_0"]
    assert published_manifest.collect["source_order"] == ["md/0_0/ML_ABN"]
    assert published_manifest.collect["inventory"] == {
        "manifest_kind": "current",
        "directory_discovery": "declared",
        "directories": ["md/0_0"],
        "coverage_known": True,
        "warnings": [],
    }


def _prepare_task6_full_dedup_case(
    root: Path,
    sources: tuple[tuple[str, str], ...],
):
    config_path = write_collect_config(root, vasp_ml=True)
    work = root / "work"
    directories = []
    for directory_name, fixture_name in sources:
        directory = work / "md" / directory_name
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            TASK6_MLAB_FIXTURES / fixture_name,
            directory / "ML_ABN",
        )
        directories.append(f"md/{directory_name}")
    write_manifest(
        work,
        Manifest(
            stage="md",
            generated_at="task6-full-dedup",
            directories=directories,
        ),
    )
    return config_path, work


def test_full_dedup_output_and_dedup_counts_are_unchanged(tmp_path):
    config_path, work = _prepare_task6_full_dedup_case(
        tmp_path,
        (
            ("seed", "complete_vasp_651.mlab"),
            ("restart", "complete_multi.mlab"),
        ),
    )

    result = run_collect(
        config_path,
        stage="md",
        collection_mode=models.MLFFCollectMode.FULL_DEDUP,
    )

    output_frames = ase_read(work / "MD_data.extxyz", format="extxyz", index=":")
    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    collect = published_manifest.collect
    assert result.status is models.CollectStatus.COMPLETE
    assert result.accepted_frame_count == len(output_frames) == 2
    assert [frame.get_potential_energy() for frame in output_frames] == pytest.approx(
        [-1.25, -1.2]
    )
    assert collect["dedup"] == {
        "applied": True,
        "schema": "mlab-config-v1",
        "seen": 3,
        "unique": 2,
        "duplicates_removed": 1,
        "candidate_frame_count": 2,
        "per_source": [
            {
                "source_path": "md/seed/ML_ABN",
                "seen": 1,
                "retained": 1,
                "duplicates_removed": 0,
            },
            {
                "source_path": "md/restart/ML_ABN",
                "seen": 2,
                "retained": 1,
                "duplicates_removed": 1,
            },
        ],
    }
    assert all("seed_verification" not in source for source in collect["sources"])


def test_full_dedup_fresh_restart_keeps_pre_restart_configurations(tmp_path):
    config_path, work = _prepare_task6_full_dedup_case(
        tmp_path,
        (("restart", "complete_multi.mlab"),),
    )
    current_restart = work / "md" / "restart" / "ML_AB"
    shutil.copy2(
        TASK6_MLAB_FIXTURES / "complete_vasp_651.mlab",
        current_restart,
    )
    current_restart_bytes = current_restart.read_bytes()

    result = run_collect(
        config_path,
        stage="md",
        collection_mode=models.MLFFCollectMode.FULL_DEDUP,
    )

    output_frames = ase_read(work / "MD_data.extxyz", format="extxyz", index=":")
    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    assert result.status is models.CollectStatus.COMPLETE
    assert result.publication_committed is True
    assert result.accepted_frame_count == len(output_frames) == 2
    assert result.source_count == 1
    source = result.source_results[0]
    assert source.source_path == "md/restart/ML_ABN"
    assert source.status is models.SourceStatus.COMPLETE
    assert source.complete_count == source.accepted_count == 2
    np.testing.assert_allclose(
        output_frames[0].get_positions(),
        [[0.0, 0.0, 1.0], [2.0, 2.0, 2.0]],
    )
    np.testing.assert_allclose(
        output_frames[1].get_positions(),
        [[0.0, 0.0, 1.1], [2.1, 2.0, 2.0]],
    )
    assert current_restart.read_bytes() == current_restart_bytes
    collect = published_manifest.collect
    assert collect["status"] == "complete"
    assert collect["frames"] == 2
    assert collect["collection_mode"] == "full-dedup"
    assert (
        collect["dedup"]["seen"],
        collect["dedup"]["unique"],
        collect["dedup"]["duplicates_removed"],
    ) == (2, 2, 0)


def test_full_dedup_repeated_seed_is_published_once(tmp_path):
    config_path, work = _prepare_task6_full_dedup_case(
        tmp_path,
        (
            ("seed", "complete_vasp_651.mlab"),
            ("restart", "complete_multi.mlab"),
        ),
    )

    result = run_collect(
        config_path,
        stage="md",
        collection_mode=models.MLFFCollectMode.FULL_DEDUP,
    )

    output_frames = ase_read(work / "MD_data.extxyz", format="extxyz", index=":")
    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    assert result.status is models.CollectStatus.COMPLETE
    assert result.publication_committed is True
    assert result.accepted_frame_count == len(output_frames) == 2
    assert tuple(source.source_path for source in result.source_results) == (
        "md/seed/ML_ABN",
        "md/restart/ML_ABN",
    )
    assert [source.complete_count for source in result.source_results] == [1, 2]
    assert [source.accepted_count for source in result.source_results] == [1, 2]
    assert [frame.get_potential_energy() for frame in output_frames] == pytest.approx(
        [-1.25, -1.2]
    )
    collect = published_manifest.collect
    assert collect["status"] == "complete"
    assert collect["source_order"] == [
        "md/seed/ML_ABN",
        "md/restart/ML_ABN",
    ]
    assert collect["dedup"] == {
        "applied": True,
        "schema": "mlab-config-v1",
        "seen": 3,
        "unique": 2,
        "duplicates_removed": 1,
        "candidate_frame_count": 2,
        "per_source": [
            {
                "source_path": "md/seed/ML_ABN",
                "seen": 1,
                "retained": 1,
                "duplicates_removed": 0,
            },
            {
                "source_path": "md/restart/ML_ABN",
                "seen": 2,
                "retained": 1,
                "duplicates_removed": 1,
            },
        ],
    }


def test_vasp_641_and_651_mlab_fixtures_collect_end_to_end(tmp_path):
    config_path, work = _prepare_task6_full_dedup_case(
        tmp_path,
        (
            ("vasp-641", "complete_vasp_641.mlab"),
            ("vasp-651", "complete_vasp_651.mlab"),
        ),
    )

    result = run_collect(
        config_path,
        stage="md",
        collection_mode=models.MLFFCollectMode.FULL_DEDUP,
    )

    output_frames = ase_read(work / "MD_data.extxyz", format="extxyz", index=":")
    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    assert result.status is models.CollectStatus.COMPLETE
    assert result.publication_committed is True
    assert result.accepted_frame_count == len(output_frames) == 2
    assert tuple(source.source_path for source in result.source_results) == (
        "md/vasp-641/ML_ABN",
        "md/vasp-651/ML_ABN",
    )
    assert all(
        source.status is models.SourceStatus.COMPLETE
        and source.complete_count == source.accepted_count == 1
        for source in result.source_results
    )
    assert [frame.get_chemical_symbols() for frame in output_frames] == [
        ["C"],
        ["O", "Pt"],
    ]
    assert [frame.get_potential_energy() for frame in output_frames] == pytest.approx(
        [-1.0, -1.25]
    )
    np.testing.assert_allclose(output_frames[0].get_positions(), [[0.0, 0.0, 1.0]])
    np.testing.assert_allclose(
        output_frames[1].get_positions(),
        [[0.0, 0.0, 1.0], [2.0, 2.0, 2.0]],
    )
    collect = published_manifest.collect
    assert collect["source_order"] == [
        "md/vasp-641/ML_ABN",
        "md/vasp-651/ML_ABN",
    ]
    assert collect["inventory"] == {
        "manifest_kind": "current",
        "directory_discovery": "declared",
        "directories": ["md/vasp-641", "md/vasp-651"],
        "coverage_known": True,
        "warnings": [],
    }
    assert (
        collect["dedup"]["seen"],
        collect["dedup"]["unique"],
        collect["dedup"]["duplicates_removed"],
    ) == (2, 2, 0)


def test_missing_manifest_full_dedup_writes_degraded_compatibility_result(
    tmp_path,
):
    config_path = write_collect_config(tmp_path, vasp_ml=True)
    work = tmp_path / "work"
    source_directory = work / "md" / "run-a"
    source_directory.mkdir(parents=True)
    shutil.copy2(
        TASK6_MLAB_FIXTURES / "complete_multi.mlab",
        source_directory / "ML_ABN",
    )

    result = run_collect(
        config_path,
        stage="md",
        collection_mode=models.MLFFCollectMode.FULL_DEDUP,
    )

    output = work / "MD_data.extxyz"
    output_frames = ase_read(output, format="extxyz", index=":")
    compatibility = yaml.safe_load(
        (work / "MD_data.collect.yaml").read_text(encoding="utf-8")
    )
    collect = compatibility["collect"]
    assert result.status is models.CollectStatus.DEGRADED
    assert result.publication_committed is True
    assert result.accepted_frame_count == len(output_frames) == 2
    assert result.sources_complete == 1
    assert not manifest_path(work, "md").exists()
    assert compatibility["schema_version"] == 1
    assert compatibility["kind"] == "dpmoire-lite-collect-result"
    assert compatibility["stage"] == "md"
    assert compatibility["input_layout"] == "missing-stage-manifest"
    assert compatibility["directory_discovery"] == "legacy-scan"
    assert compatibility["declared_directories"] == []
    assert compatibility["discovered_directories"] == ["md/run-a"]
    assert compatibility["collection_mode"] == "full-dedup"
    assert collect["status"] == "degraded"
    assert collect["frames"] == 2
    assert collect["written"] is True
    assert collect["output"] == "MD_data.extxyz"
    assert collect["output_sha256"] == atomic_io.sha256_file(output)
    assert collect["source_order"] == ["md/run-a/ML_ABN"]
    assert {
        key: collect["inventory"][key]
        for key in (
            "manifest_kind",
            "directory_discovery",
            "directories",
            "coverage_known",
        )
    } == {
        "manifest_kind": "missing",
        "directory_discovery": "legacy-scan",
        "directories": ["md/run-a"],
        "coverage_known": False,
    }
    inventory_warnings = collect["inventory"]["warnings"]
    assert len(inventory_warnings) == 1
    assert "bounded direct-child legacy scan" in inventory_warnings[0]
    assert "unknown source coverage" in inventory_warnings[0]
    assert compatibility["dedup"] == collect["dedup"]
    assert (
        collect["dedup"]["seen"],
        collect["dedup"]["unique"],
        collect["dedup"]["duplicates_removed"],
    ) == (2, 2, 0)


def _write_task6_marked_outcar(target: Path, first_x: float) -> None:
    text = TASK6_OUTCAR_FIXTURE.read_text(encoding="utf-8")
    replacements = (
        (
            "   0.00000000   0.00000000   0.00000000      "
            "0.10000000  -0.20000000   0.30000000",
            f"   {first_x:.8f}   0.00000000   0.00000000      "
            "0.10000000  -0.20000000   0.30000000",
        ),
        (
            "   0.05000000   0.00000000   0.00000000     "
            "-0.05000000   0.10000000  -0.15000000",
            f"   {first_x + 0.05:.8f}   0.00000000   0.00000000     "
            "-0.05000000   0.10000000  -0.15000000",
        ),
    )
    for original, replacement in replacements:
        assert text.count(original) == 1
        text = text.replace(original, replacement, 1)
    target.write_text(text, encoding="utf-8")


def test_rlx_multiple_outcar_segments_record_deterministic_order(tmp_path):
    config_path = write_collect_config(
        tmp_path,
        vasp_ml=False,
        outcar_collect_freq=1,
        outcar_patterns=[r"^OUTCAR\d+$", r"^OUTCAR$"],
    )
    work = tmp_path / "work"
    first_directory = work / "rlx" / "z-declared-first"
    second_directory = work / "rlx" / "a-declared-second"
    first_directory.mkdir(parents=True)
    second_directory.mkdir(parents=True)
    _write_task6_marked_outcar(first_directory / "OUTCAR2", 0.20)
    _write_task6_marked_outcar(first_directory / "OUTCAR10", 1.00)
    _write_task6_marked_outcar(first_directory / "OUTCAR", 2.00)
    _write_task6_marked_outcar(second_directory / "OUTCAR", 3.00)
    declared_directories = [
        "rlx/z-declared-first",
        "rlx/a-declared-second",
    ]
    write_manifest(
        work,
        Manifest(
            stage="rlx",
            generated_at="task6-rlx-order",
            directories=declared_directories,
        ),
    )
    input_manifest = read_manifest(work, "rlx")
    assert input_manifest.kind == "current"
    assert input_manifest.manifest is not None
    assert input_manifest.manifest.schema_version == 2

    result = run_collect(config_path, stage="rlx")

    expected_sources = (
        ("rlx/z-declared-first/OUTCAR2", r"^OUTCAR\d+$", 0, 0),
        ("rlx/z-declared-first/OUTCAR10", r"^OUTCAR\d+$", 0, 1),
        ("rlx/z-declared-first/OUTCAR", r"^OUTCAR$", 1, 2),
        ("rlx/a-declared-second/OUTCAR", r"^OUTCAR$", 1, 0),
    )
    assert result.status is models.CollectStatus.COMPLETE
    assert result.publication_committed is True
    assert result.accepted_frame_count == 8
    assert result.sources_complete == 4
    assert all(
        source.status is models.SourceStatus.COMPLETE
        and source.complete_count == source.accepted_count == 2
        for source in result.source_results
    )
    assert tuple(
        (source.source_path, source.pattern, source.pattern_index, source.order)
        for source in result.source_results
    ) == expected_sources

    output_frames = ase_read(work / "rlx_data.extxyz", format="extxyz", index=":")
    assert len(output_frames) == 8
    assert [frame.positions[0, 0] for frame in output_frames] == pytest.approx(
        [0.20, 0.25, 1.00, 1.05, 2.00, 2.05, 3.00, 3.05]
    )
    published_manifest = read_manifest(work, "rlx").manifest
    assert published_manifest is not None
    assert published_manifest.schema_version == 2
    assert published_manifest.directories == declared_directories
    collect = published_manifest.collect
    assert collect["expected_directories"] == declared_directories
    assert collect["source_order"] == [source[0] for source in expected_sources]
    assert tuple(
        (
            source["path"],
            source["pattern"],
            source["pattern_index"],
            source["order"],
        )
        for source in collect["sources"]
    ) == expected_sources


def test_collect_fault_after_data_replace_recovers_manifest_next_run(
    tmp_path,
    monkeypatch,
):
    config_path, work = _prepare_task6_full_dedup_case(
        tmp_path,
        (("recover", "complete_multi.mlab"),),
    )
    output = work / "MD_data.extxyz"
    stage_manifest = manifest_path(work, "md")
    journal_path = work / ".MD_data.extxyz.collect-journal.yaml"
    baseline_manifest_bytes = stage_manifest.read_bytes()
    baseline_manifest_sha256 = atomic_io.sha256_file(stage_manifest)
    output_destination = output.resolve(strict=False)
    manifest_destination = stage_manifest.resolve(strict=False)
    real_replace = collect_publish_module.os.replace
    events = []

    def interrupting_replace(source, destination, *args, **kwargs):
        resolved_destination = Path(destination).resolve(strict=False)
        if resolved_destination == output_destination:
            result = real_replace(source, destination, *args, **kwargs)
            events.append("data-replaced")
            return result
        if resolved_destination == manifest_destination:
            assert events == ["data-replaced"]
            events.append("manifest-fault")
            raise OSError("synthetic Task 6 fault after data replace")
        return real_replace(source, destination, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(
            collect_publish_module.os,
            "replace",
            interrupting_replace,
        )
        interrupted = run_collect(
            config_path,
            stage="md",
            collection_mode=models.MLFFCollectMode.FULL_DEDUP,
        )

    assert interrupted.status is models.CollectStatus.FATAL
    assert interrupted.publication_committed is False
    assert interrupted.candidate is not None
    assert interrupted.accepted_frame_count == 2
    assert interrupted.source_count == interrupted.sources_complete == 1
    assert interrupted.source_results[0].source_path == "md/recover/ML_ABN"
    assert "synthetic Task 6 fault after data replace" in (
        interrupted.fatal_diagnostic
    )
    assert events == ["data-replaced", "manifest-fault"]

    journal = yaml.safe_load(journal_path.read_text(encoding="utf-8"))
    data_candidate = work / Path(journal["data_candidate_path"])
    manifest_candidate = work / Path(journal["manifest_candidate_path"])
    assert journal["state"] == "pending"
    assert journal["stage"] == "md"
    assert journal["final_output"] == "MD_data.extxyz"
    assert journal["result_manifest_target_kind"] == "current-stage"
    assert journal["result_manifest_path"] == "md/manifest.yaml"
    assert journal["previous_output_sha256"] is None
    assert journal["previous_manifest_sha256"] == baseline_manifest_sha256
    assert journal["backup_path"] is None
    assert journal["backup_sha256"] is None
    assert output.is_file()
    assert atomic_io.sha256_file(output) == journal["data_candidate_sha256"]
    assert not data_candidate.exists()
    assert manifest_candidate.is_file()
    assert (
        atomic_io.sha256_file(manifest_candidate)
        == journal["manifest_candidate_sha256"]
    )
    assert stage_manifest.read_bytes() == baseline_manifest_bytes
    interrupted_output_bytes = output.read_bytes()

    source_calls = []

    def unexpected_full_dedup_build(*args, **kwargs):
        source_calls.append((args, kwargs))
        raise AssertionError(
            "new source collection started after a recoverable transaction"
        )

    with monkeypatch.context() as patch:
        patch.setattr(
            collect_module,
            "build_full_dedup_candidate",
            unexpected_full_dedup_build,
        )
        recovered = run_collect(
            config_path,
            stage="md",
            collection_mode=models.MLFFCollectMode.FULL_DEDUP,
        )

    recovered_evidence = recovered.recovered_evidence
    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    collect = published_manifest.collect
    assert recovered.status is models.CollectStatus.COMPLETE
    assert recovered.publication_committed is True
    assert recovered.candidate is None
    assert isinstance(recovered_evidence, models.RecoveredCollectionEvidence)
    assert recovered_evidence.transaction_id == journal["transaction_id"]
    assert recovered.accepted_frame_count == collect["frames"] == 2
    assert recovered.source_count == recovered.sources_complete == 1
    assert recovered.sources_partial == 0
    assert recovered.sources_skipped == 0
    assert recovered.sources_failed == 0
    assert source_calls == []
    assert collect["transaction_id"] == journal["transaction_id"]
    assert collect["status"] == "complete"
    assert collect["collection_mode"] == "full-dedup"
    assert collect["output_sha256"] == journal["data_candidate_sha256"]
    assert atomic_io.sha256_file(stage_manifest) == journal[
        "manifest_candidate_sha256"
    ]
    assert output.read_bytes() == interrupted_output_bytes
    assert not journal_path.exists()
    assert not data_candidate.exists()
    assert not manifest_candidate.exists()


def test_committed_journal_residual_is_cleaned_next_run(tmp_path, monkeypatch):
    config_path = write_collect_config(
        tmp_path,
        vasp_ml=False,
        outcar_collect_freq=2,
    )
    work = tmp_path / "work"
    source_directory = work / "md" / "run-a"
    source_directory.mkdir(parents=True)
    source = source_directory / "OUTCAR"
    shutil.copy2(TASK6_OUTCAR_FIXTURE, source)
    write_manifest(
        work,
        Manifest(
            stage="md",
            generated_at="task6-committed-residual",
            directories=["md/run-a"],
        ),
    )
    output = work / "MD_data.extxyz"
    stage_manifest = manifest_path(work, "md")
    journal_path = work / ".MD_data.extxyz.collect-journal.yaml"
    resolved_journal = journal_path.resolve(strict=False)
    real_unlink = Path.unlink

    def interrupting_unlink(path, *args, **kwargs):
        candidate = Path(path)
        if (
            candidate.resolve(strict=False) == resolved_journal
            and candidate.exists()
        ):
            record = yaml.safe_load(candidate.read_text(encoding="utf-8"))
            if record["state"] == "committed":
                raise OSError(
                    "synthetic Task 6 interruption before committed journal unlink"
                )
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupting_unlink)
        interrupted = run_collect(config_path, stage="md")

    assert interrupted.status is models.CollectStatus.FATAL
    assert interrupted.publication_committed is False
    assert interrupted.candidate is not None
    assert interrupted.accepted_frame_count == 1
    assert interrupted.source_count == interrupted.sources_complete == 1
    assert "synthetic Task 6 interruption before committed journal unlink" in (
        interrupted.fatal_diagnostic
    )

    committed_journal = yaml.safe_load(journal_path.read_text(encoding="utf-8"))
    first_manifest = read_manifest(work, "md").manifest
    assert first_manifest is not None
    first_collect = first_manifest.collect
    first_output_bytes = output.read_bytes()
    first_output_sha256 = atomic_io.sha256_file(output)
    first_manifest_sha256 = atomic_io.sha256_file(stage_manifest)
    first_transaction_id = committed_journal["transaction_id"]
    assert committed_journal["state"] == "committed"
    assert committed_journal["data_candidate_sha256"] == first_output_sha256
    assert committed_journal["manifest_candidate_sha256"] == first_manifest_sha256
    assert committed_journal["previous_output_sha256"] is None
    assert committed_journal["backup_path"] is None
    assert first_collect["transaction_id"] == first_transaction_id
    assert first_collect["status"] == "complete"
    assert first_collect["frames"] == 1
    assert first_collect["output_sha256"] == first_output_sha256
    assert not (work / Path(committed_journal["data_candidate_path"])).exists()
    assert not (work / Path(committed_journal["manifest_candidate_path"])).exists()

    updated_config_path = write_collect_config(
        tmp_path,
        vasp_ml=False,
        outcar_collect_freq=1,
    )
    assert updated_config_path == config_path
    journal_state_at_collection = []
    real_builder = collect_module.build_seed_aware_candidate

    def observing_builder(*args, **kwargs):
        journal_state_at_collection.append(journal_path.exists())
        return real_builder(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(
            collect_module,
            "build_seed_aware_candidate",
            observing_builder,
        )
        result = run_collect(config_path, stage="md")

    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    collect = published_manifest.collect
    output_frames = ase_read(output, format="extxyz", index=":")
    assert journal_state_at_collection == [False]
    assert result.status is models.CollectStatus.COMPLETE
    assert result.publication_committed is True
    assert result.candidate is not None
    assert result.recovered_evidence is None
    assert result.accepted_frame_count == len(output_frames) == 2
    assert result.source_count == result.sources_complete == 1
    assert collect["transaction_id"] != first_transaction_id
    assert collect["status"] == "complete"
    assert collect["frames"] == 2
    assert collect["source_order"] == ["md/run-a/OUTCAR"]
    assert collect["output_sha256"] == atomic_io.sha256_file(output)
    assert collect["output_sha256"] != first_output_sha256
    assert collect["previous_sha256"] == first_output_sha256
    assert collect["previous_manifest_sha256"] == first_manifest_sha256
    assert collect["previous_frames"] == 1
    assert collect["backup_sha256"] == first_output_sha256
    backup = work / Path(collect["backup"])
    assert backup.is_file()
    assert backup.read_bytes() == first_output_bytes
    assert not journal_path.exists()


def test_load_ml_abn_sample_counts_frames():
    dataset = Dataset()
    dataset.load_ml_ab(TASK6_MLAB_FIXTURES / "complete_multi.mlab")

    assert dataset.n_configs == len(dataset.data) == 2


def test_load_outcar_sample_counts_frames():
    dataset = Dataset()
    dataset.load_outcar(TASK6_OUTCAR_FIXTURE, freq=1)

    assert dataset.n_configs == len(dataset.data) == 2


def test_load_outcar_rejects_non_positive_frequency():
    with pytest.raises(ValueError, match="positive"):
        Dataset().load_outcar(Path("whatever"), freq=0)


def test_collect_ml_md_writes_extxyz_and_manifest_counts(tmp_path):
    config_path, work, md_directory, producer_manifest = (
        _prepare_task6_seeded_stage1(tmp_path)
    )
    _copy_task6_final_mlab(md_directory)

    result = run_collect(config_path, stage="md")

    output = work / "MD_data.extxyz"
    output_frames = ase_read(output, format="extxyz", index=":")
    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    assert output.is_file()
    assert result.status is models.CollectStatus.COMPLETE
    assert result.publication_committed is True
    assert result.accepted_frame_count == len(output_frames) == 1
    assert result.source_count == result.sources_complete == 1
    assert result.sources_partial == 0
    assert result.sources_skipped == 0
    assert result.sources_failed == 0
    assert published_manifest.mlff_seed == producer_manifest.mlff_seed
    collect = published_manifest.collect
    assert collect["frames"] == 1
    assert collect["output"] == "MD_data.extxyz"
    assert collect["sources_attempted"] == collect["sources_complete"] == 1
    assert collect["sources_partial"] == 0
    assert collect["sources_skipped"] == 0
    assert collect["sources_failed"] == 0


def test_collect_ml_md_records_missing_ml_abn_without_crashing(tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=True)
    work = tmp_path / "work"
    (work / "md" / "0_0").mkdir(parents=True)
    write_manifest(
        work,
        Manifest(
            stage="md",
            generated_at="test",
            directories=["md/0_0"],
            mlff_seed={
                "configurations": 1,
                "digest_schema": "mlab-seed-v1",
                "seed_prefix_sha256": "0" * 64,
            },
        ),
    )

    result = run_collect(config_path, stage="md")

    assert result.status is models.CollectStatus.NO_DATA
    assert result.publication_committed is True
    manifest = read_manifest(work, "md")
    records = manifest.collect["sources"]
    assert records == [source.as_diagnostic() for source in result.source_results]
    assert records
    assert any("md/0_0/ML_ABN" in record["path"] for record in records)
    assert any("ML_ABN" in record["reason"] for record in records)


def test_collect_ml_md_uses_immutable_manifest_seed_after_ml_ab_growth(tmp_path):
    config_path, work, md_directory, producer_manifest = (
        _prepare_task6_seeded_stage1(tmp_path)
    )
    current_ml_ab = md_directory / "ML_AB"
    shutil.copy2(TASK6_MLAB_FIXTURES / "complete_multi.mlab", current_ml_ab)
    _copy_task6_final_mlab(md_directory)
    current_ml_ab_bytes = current_ml_ab.read_bytes()

    result = run_collect(config_path, stage="md")

    source = result.source_results[0]
    output_frames = ase_read(work / "MD_data.extxyz", format="extxyz", index=":")
    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    assert result.status is models.CollectStatus.COMPLETE
    assert result.publication_committed is True
    assert result.accepted_frame_count == len(output_frames) == 1
    assert source.status is models.SourceStatus.COMPLETE
    assert source.complete_count == 2
    assert source.accepted_count == 1
    assert source.seed_identity is not None
    assert (
        source.seed_identity.sha256
        == producer_manifest.mlff_seed["seed_prefix_sha256"]
    )
    assert published_manifest.collect["frames"] == 1
    assert current_ml_ab.read_bytes() == current_ml_ab_bytes


def test_collect_ml_md_ignores_unparseable_current_ml_ab(tmp_path):
    config_path, work, md_directory, _ = _prepare_task6_seeded_stage1(tmp_path)
    _copy_task6_final_mlab(md_directory)
    current_ml_ab = md_directory / "ML_AB"
    current_ml_ab.write_text(
        "not\nan\nML_AB\nfile\nbad-count\n",
        encoding="utf-8",
    )
    current_ml_ab_bytes = current_ml_ab.read_bytes()

    result = run_collect(config_path, stage="md")

    source = result.source_results[0]
    output_frames = ase_read(work / "MD_data.extxyz", format="extxyz", index=":")
    published_manifest = read_manifest(work, "md").manifest
    assert published_manifest is not None
    assert result.status is models.CollectStatus.COMPLETE
    assert result.publication_committed is True
    assert result.accepted_frame_count == len(output_frames) == 1
    assert result.sources_failed == 0
    assert source.source_path == "md/0_0/ML_ABN"
    assert source.status is models.SourceStatus.COMPLETE
    assert published_manifest.collect["sources_failed"] == 0
    assert published_manifest.collect["sources"] == [source.as_diagnostic()]
    assert current_ml_ab.read_bytes() == current_ml_ab_bytes


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


def _nonzero_publication_api():
    publisher = getattr(collect_module, "publish_nonzero_candidate", None)
    assert callable(publisher), "publish_nonzero_candidate is not implemented"
    return publisher


def _publication_frame(index: int) -> Atoms:
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
    return atoms


def _publication_outcar_source(
    *,
    directory: str,
    status: models.SourceStatus,
    frame_index: int,
):
    frames = ()
    complete_count = 0
    reason = None
    if status in {models.SourceStatus.COMPLETE, models.SourceStatus.PARTIAL}:
        frames = (_publication_frame(frame_index),)
        complete_count = 1
    if status is not models.SourceStatus.COMPLETE:
        reason = f"structured {status.value} publication source"
    return models.SourceResult(
        source_path=f"{directory}/OUTCAR",
        source_kind=models.SourceKind.OUTCAR,
        status=status,
        complete_count=complete_count,
        accepted_frames=frames,
        reason=reason,
    )


def _publication_candidate(
    *statuses: models.SourceStatus,
    directories: tuple[str, ...] | None = None,
):
    selected_directories = directories or tuple(
        f"md/run-{index}" for index in range(len(statuses))
    )
    if len(selected_directories) != len(statuses):
        raise ValueError("directories must align with source statuses")
    source_results = tuple(
        _publication_outcar_source(
            directory=directory,
            status=status,
            frame_index=index,
        )
        for index, (directory, status) in enumerate(
            zip(selected_directories, statuses, strict=True)
        )
    )
    accepted_frames = tuple(
        frame
        for source_result in source_results
        for frame in source_result.accepted_frames
    )
    return models.CollectionCandidate(
        source_results=source_results,
        accepted_frames=accepted_frames,
        expected_directories=selected_directories,
    )


def _publication_full_dedup_candidate(
    *,
    manifest_kind: str,
    coverage_known: bool,
    duplicate: bool = False,
):
    directory = "md/run-a"
    source_path = f"{directory}/ML_ABN"
    seen = 2 if duplicate else 1
    configurations = tuple(_status_mlab_configuration() for _ in range(seen))
    source_result = models.SourceResult(
        source_path=source_path,
        source_kind=models.SourceKind.MLAB,
        status=models.SourceStatus.COMPLETE,
        complete_count=seen,
        parsed_configurations=configurations,
    )
    inventory = models.SourceInventory(
        manifest_kind=manifest_kind,
        directory_discovery=(
            "legacy-scan" if manifest_kind == "missing" else "declared"
        ),
        directories=(directory,),
        coverage_known=coverage_known,
        warnings=(
            ("legacy scan cannot prove expected-source coverage",)
            if not coverage_known
            else ()
        ),
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
        accepted_frames=(_publication_frame(0),),
        expected_directories=inventory.directories,
        collection_mode=models.MLFFCollectMode.FULL_DEDUP,
        source_inventory=inventory,
        dedup_stats=dedup_stats,
    )


def _previous_collect_record(
    *,
    directories: tuple[str, ...],
    statuses: tuple[models.SourceStatus, ...],
    frames: int,
):
    if len(directories) != len(statuses):
        raise ValueError("previous directories must align with statuses")
    sources = [
        {
            "path": f"{directory}/OUTCAR",
            "kind": "outcar",
            "status": status.value,
            "complete_count": 1 if status is models.SourceStatus.COMPLETE else 0,
            "accepted_count": 1 if status is models.SourceStatus.COMPLETE else 0,
        }
        for directory, status in zip(directories, statuses, strict=True)
    ]
    counts = {
        status: sum(observed is status for observed in statuses)
        for status in models.SourceStatus
    }
    return {
        "transaction_id": "previous-transaction",
        "status": "complete",
        "frames": frames,
        "written": True,
        "sources_attempted": (
            counts[models.SourceStatus.COMPLETE]
            + counts[models.SourceStatus.PARTIAL]
            + counts[models.SourceStatus.FAILED]
        ),
        "sources_complete": counts[models.SourceStatus.COMPLETE],
        "sources_partial": counts[models.SourceStatus.PARTIAL],
        "sources_skipped": counts[models.SourceStatus.SKIPPED],
        "sources_failed": counts[models.SourceStatus.FAILED],
        "expected_directories": list(directories),
        "expected_directory_count": len(directories),
        "source_order": [source["path"] for source in sources],
        "sources": sources,
    }


def _write_current_publication_manifest(
    work: Path,
    *,
    directories: tuple[str, ...],
    previous_collect: dict | None = None,
):
    write_manifest(
        work,
        Manifest(
            stage="md",
            generated_at="nonzero-publication-test",
            directories=list(directories),
            collect=previous_collect or {},
        ),
    )
    return read_manifest(work, "md")


def _write_legacy_publication_manifest(
    work: Path,
    *,
    directories: tuple[str, ...],
):
    path = manifest_path(work, "md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "stage": "md",
                "directories": list(directories),
                "legacy_key": "preserve-me",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path, path.read_bytes(), read_manifest(work, "md")


def test_legacy_declared_full_dedup_collects_end_to_end(tmp_path):
    config_path, work = _prepare_task6_full_dedup_case(
        tmp_path,
        (
            ("run-10", "complete_vasp_651.mlab"),
            ("run-2", "complete_multi.mlab"),
        ),
    )
    directories = ("md/run-10", "md/run-2")
    legacy_path, legacy_bytes, _manifest = _write_legacy_publication_manifest(
        work,
        directories=directories,
    )

    result = run_collect(
        config_path,
        stage="md",
        collection_mode=models.MLFFCollectMode.FULL_DEDUP,
    )

    output = work / "MD_data.extxyz"
    output_frames = ase_read(output, format="extxyz", index=":")
    compatibility_path = work / "MD_data.collect.yaml"
    compatibility = yaml.safe_load(compatibility_path.read_text(encoding="utf-8"))
    collect = compatibility["collect"]
    source_paths = ("md/run-10/ML_ABN", "md/run-2/ML_ABN")

    assert result.status is models.CollectStatus.COMPLETE
    assert result.publication_committed is True
    assert result.accepted_frame_count == len(output_frames) == 2
    assert result.source_count == 2
    assert tuple(source.source_path for source in result.source_results) == source_paths
    assert [source.complete_count for source in result.source_results] == [1, 2]
    assert [source.accepted_count for source in result.source_results] == [1, 2]
    assert [frame.get_potential_energy() for frame in output_frames] == pytest.approx(
        [-1.25, -1.2]
    )

    assert legacy_path.read_bytes() == legacy_bytes
    assert compatibility_path.is_file()
    assert compatibility["schema_version"] == 1
    assert compatibility["kind"] == "dpmoire-lite-collect-result"
    assert compatibility["stage"] == "md"
    assert compatibility["input_layout"] == "legacy-stage-manifest"
    assert compatibility["directory_discovery"] == "declared"
    assert compatibility["declared_directories"] == list(directories)
    assert compatibility["discovered_directories"] == []
    assert compatibility["collection_mode"] == "full-dedup"
    assert collect["status"] == "complete"
    assert collect["frames"] == 2
    assert collect["source_order"] == list(source_paths)
    assert collect["inventory"] == {
        "manifest_kind": "legacy",
        "directory_discovery": "declared",
        "directories": list(directories),
        "coverage_known": True,
        "warnings": [],
    }
    assert collect["dedup"] == {
        "applied": True,
        "schema": "mlab-config-v1",
        "seen": 3,
        "unique": 2,
        "duplicates_removed": 1,
        "candidate_frame_count": 2,
        "per_source": [
            {
                "source_path": "md/run-10/ML_ABN",
                "seen": 1,
                "retained": 1,
                "duplicates_removed": 0,
            },
            {
                "source_path": "md/run-2/ML_ABN",
                "seen": 2,
                "retained": 1,
                "duplicates_removed": 1,
            },
        ],
    }


def _publish_nonzero(
    publisher,
    *,
    work: Path,
    manifest,
    candidate,
    transaction_id: str,
    collection_mode: models.MLFFCollectMode | None = None,
):
    return publisher(
        work_dir=work,
        stage="md",
        manifest=manifest,
        candidate=candidate,
        collection_mode=collection_mode,
        transaction_id=transaction_id,
    )


def test_complete_candidate_publishes_data_and_manifest_transaction(tmp_path):
    publish_nonzero_candidate = _nonzero_publication_api()
    work = tmp_path / "work"
    directories = ("md/run-a", "md/run-b")
    manifest = _write_current_publication_manifest(
        work,
        directories=directories,
    )
    candidate = _publication_candidate(
        models.SourceStatus.COMPLETE,
        models.SourceStatus.COMPLETE,
        directories=directories,
    )

    result = _publish_nonzero(
        publish_nonzero_candidate,
        work=work,
        manifest=manifest,
        candidate=candidate,
        transaction_id="plan10-task3-complete",
    )

    output = work / "MD_data.extxyz"
    collect = _current_collect_record(work)
    assert result.status is models.CollectStatus.COMPLETE
    assert result.publication_committed is True
    assert output.is_file()
    assert collect["transaction_id"] == "plan10-task3-complete"
    assert collect["status"] == "complete"
    assert collect["frames"] == 2
    assert collect["written"] is True
    assert collect["sources_complete"] == 2


def test_partial_candidate_publishes_degraded_with_exact_source_counts(tmp_path):
    publish_nonzero_candidate = _nonzero_publication_api()
    work = tmp_path / "work"
    directories = ("md/run-a", "md/run-b")
    manifest = _write_current_publication_manifest(
        work,
        directories=directories,
    )
    candidate = _publication_candidate(
        models.SourceStatus.COMPLETE,
        models.SourceStatus.PARTIAL,
        directories=directories,
    )

    result = _publish_nonzero(
        publish_nonzero_candidate,
        work=work,
        manifest=manifest,
        candidate=candidate,
        transaction_id="plan10-task3-partial",
    )

    collect = _current_collect_record(work)
    assert result.status is models.CollectStatus.DEGRADED
    assert collect["status"] == "degraded"
    assert collect["sources_attempted"] == 2
    assert collect["sources_complete"] == 1
    assert collect["sources_partial"] == 1
    assert collect["sources_skipped"] == 0
    assert collect["sources_failed"] == 0
    assert collect["sources"] == [
        source_result.as_diagnostic()
        for source_result in candidate.source_results
    ]


def test_failed_source_with_other_frames_publishes_degraded(tmp_path):
    publish_nonzero_candidate = _nonzero_publication_api()
    work = tmp_path / "work"
    directories = ("md/run-a", "md/run-b")
    manifest = _write_current_publication_manifest(
        work,
        directories=directories,
    )
    candidate = _publication_candidate(
        models.SourceStatus.COMPLETE,
        models.SourceStatus.FAILED,
        directories=directories,
    )

    result = _publish_nonzero(
        publish_nonzero_candidate,
        work=work,
        manifest=manifest,
        candidate=candidate,
        transaction_id="plan10-task3-failed-source",
    )

    collect = _current_collect_record(work)
    assert result.status is models.CollectStatus.DEGRADED
    assert collect["frames"] == 1
    assert collect["sources_complete"] == 1
    assert collect["sources_failed"] == 1


def test_new_frames_less_than_previous_warns_with_backup(tmp_path):
    publish_nonzero_candidate = _nonzero_publication_api()
    work = tmp_path / "work"
    directories = ("md/run-a",)
    previous_collect = _previous_collect_record(
        directories=directories,
        statuses=(models.SourceStatus.COMPLETE,),
        frames=3,
    )
    manifest = _write_current_publication_manifest(
        work,
        directories=directories,
        previous_collect=previous_collect,
    )
    output = work / "MD_data.extxyz"
    previous_bytes = _write_previous_extxyz(output, frame_count=3)
    candidate = _publication_candidate(
        models.SourceStatus.COMPLETE,
        directories=directories,
    )

    result = _publish_nonzero(
        publish_nonzero_candidate,
        work=work,
        manifest=manifest,
        candidate=candidate,
        transaction_id="plan10-task3-smaller",
    )

    collect = _current_collect_record(work)
    warning = "\n".join(result.warnings)
    assert result.status is models.CollectStatus.COMPLETE
    assert collect["previous_frames"] == 3
    assert collect["backup"].startswith("backups/collect/")
    assert collect["backup_sha256"] == atomic_io.sha256_file(
        work / collect["backup"]
    )
    assert (work / collect["backup"]).read_bytes() == previous_bytes
    assert "frames new=1 old=3" in warning
    assert "backup=backups/collect/" in warning


def test_source_or_directory_coverage_decline_warns_with_backup(tmp_path):
    publish_nonzero_candidate = _nonzero_publication_api()
    work = tmp_path / "work"
    previous_directories = ("md/run-a", "md/run-b")
    current_directories = ("md/run-a",)
    previous_collect = _previous_collect_record(
        directories=previous_directories,
        statuses=(models.SourceStatus.COMPLETE, models.SourceStatus.COMPLETE),
        frames=1,
    )
    manifest = _write_current_publication_manifest(
        work,
        directories=current_directories,
        previous_collect=previous_collect,
    )
    _write_previous_extxyz(work / "MD_data.extxyz", frame_count=1)
    candidate = _publication_candidate(
        models.SourceStatus.COMPLETE,
        directories=current_directories,
    )

    result = _publish_nonzero(
        publish_nonzero_candidate,
        work=work,
        manifest=manifest,
        candidate=candidate,
        transaction_id="plan10-task3-coverage-decline",
    )

    collect = _current_collect_record(work)
    warning = "\n".join(result.warnings)
    assert result.status is models.CollectStatus.DEGRADED
    assert result.coverage_declined is True
    assert collect["coverage_declined"] is True
    assert collect["previous_sources_complete"] == 2
    assert collect["sources_complete"] == 1
    assert collect["previous_expected_directories"] == list(
        previous_directories
    )
    assert collect["expected_directories"] == list(current_directories)
    assert "complete new=1 old=2" in warning
    assert "directories new=1 old=2" in warning
    assert "backup=backups/collect/" in warning


def test_new_partial_failed_or_skipped_warns_with_backup(tmp_path):
    publish_nonzero_candidate = _nonzero_publication_api()
    work = tmp_path / "work"
    directories = tuple(f"md/run-{index}" for index in range(4))
    previous_collect = _previous_collect_record(
        directories=directories,
        statuses=(models.SourceStatus.COMPLETE,) * 4,
        frames=2,
    )
    manifest = _write_current_publication_manifest(
        work,
        directories=directories,
        previous_collect=previous_collect,
    )
    _write_previous_extxyz(work / "MD_data.extxyz", frame_count=2)
    candidate = _publication_candidate(
        models.SourceStatus.COMPLETE,
        models.SourceStatus.PARTIAL,
        models.SourceStatus.FAILED,
        models.SourceStatus.SKIPPED,
        directories=directories,
    )

    result = _publish_nonzero(
        publish_nonzero_candidate,
        work=work,
        manifest=manifest,
        candidate=candidate,
        transaction_id="plan10-task3-new-noncomplete",
    )

    warning = "\n".join(result.warnings)
    assert result.status is models.CollectStatus.DEGRADED
    assert "partial new=1 old=0" in warning
    assert "failed new=1 old=0" in warning
    assert "skipped new=1 old=0" in warning
    assert "backup=backups/collect/" in warning


def test_published_manifest_frames_equal_reread_extxyz_frames(tmp_path):
    publish_nonzero_candidate = _nonzero_publication_api()
    work = tmp_path / "work"
    directories = ("md/run-a", "md/run-b")
    manifest = _write_current_publication_manifest(
        work,
        directories=directories,
    )
    candidate = _publication_candidate(
        models.SourceStatus.COMPLETE,
        models.SourceStatus.COMPLETE,
        directories=directories,
    )

    _publish_nonzero(
        publish_nonzero_candidate,
        work=work,
        manifest=manifest,
        candidate=candidate,
        transaction_id="plan10-task3-frame-proof",
    )

    frames = ase_read(work / "MD_data.extxyz", format="extxyz", index=":")
    collect = _current_collect_record(work)
    assert len(frames) == collect["frames"] == candidate.frame_count


def test_published_manifest_and_output_share_transaction_and_hash(tmp_path):
    publish_nonzero_candidate = _nonzero_publication_api()
    work = tmp_path / "work"
    directories = ("md/run-a",)
    manifest = _write_current_publication_manifest(
        work,
        directories=directories,
    )
    candidate = _publication_candidate(
        models.SourceStatus.COMPLETE,
        directories=directories,
    )
    transaction_id = "plan10-task3-identity"

    _publish_nonzero(
        publish_nonzero_candidate,
        work=work,
        manifest=manifest,
        candidate=candidate,
        transaction_id=transaction_id,
    )

    output = work / "MD_data.extxyz"
    collect = _current_collect_record(work)
    assert collect["transaction_id"] == transaction_id
    assert collect["output_sha256"] == atomic_io.sha256_file(output)
    assert not (work / ".MD_data.extxyz.collect-journal.yaml").exists()


def test_full_dedup_current_v2_publishes_counts_to_stage_manifest(tmp_path):
    publish_nonzero_candidate = _nonzero_publication_api()
    work = tmp_path / "work"
    directories = ("md/run-a",)
    manifest = _write_current_publication_manifest(
        work,
        directories=directories,
    )
    candidate = _publication_full_dedup_candidate(
        manifest_kind="current",
        coverage_known=True,
        duplicate=True,
    )

    result = _publish_nonzero(
        publish_nonzero_candidate,
        work=work,
        manifest=manifest,
        candidate=candidate,
        collection_mode=models.MLFFCollectMode.FULL_DEDUP,
        transaction_id="plan10-task3-current-full-dedup",
    )

    collect = _current_collect_record(work)
    assert result.status is models.CollectStatus.COMPLETE
    assert collect["collection_mode"] == "full-dedup"
    assert collect["inventory"] == {
        "manifest_kind": "current",
        "directory_discovery": "declared",
        "directories": ["md/run-a"],
        "coverage_known": True,
        "warnings": [],
    }
    assert collect["dedup"] == {
        "applied": True,
        "schema": "mlab-config-v1",
        "seen": 2,
        "unique": 1,
        "duplicates_removed": 1,
        "candidate_frame_count": 1,
        "per_source": [
            {
                "source_path": "md/run-a/ML_ABN",
                "seen": 2,
                "retained": 1,
                "duplicates_removed": 1,
            }
        ],
    }


def test_full_dedup_legacy_publishes_compatibility_result_manifest(tmp_path):
    publish_nonzero_candidate = _nonzero_publication_api()
    work = tmp_path / "work"
    directories = ("md/run-a",)
    legacy_path, legacy_bytes, manifest = _write_legacy_publication_manifest(
        work,
        directories=directories,
    )
    candidate = _publication_full_dedup_candidate(
        manifest_kind="legacy",
        coverage_known=True,
        duplicate=True,
    )

    result = _publish_nonzero(
        publish_nonzero_candidate,
        work=work,
        manifest=manifest,
        candidate=candidate,
        collection_mode=models.MLFFCollectMode.FULL_DEDUP,
        transaction_id="plan10-task3-legacy-full-dedup",
    )

    compatibility = yaml.safe_load(
        (work / "MD_data.collect.yaml").read_text(encoding="utf-8")
    )
    assert result.status is models.CollectStatus.COMPLETE
    assert legacy_path.read_bytes() == legacy_bytes
    assert compatibility["input_layout"] == "legacy-stage-manifest"
    assert compatibility["collection_mode"] == "full-dedup"
    assert compatibility["collect"]["dedup"]["seen"] == 2
    assert compatibility["collect"]["dedup"]["duplicates_removed"] == 1


def test_missing_scan_publication_is_degraded_and_records_inventory(tmp_path):
    publish_nonzero_candidate = _nonzero_publication_api()
    work = tmp_path / "work"
    manifest = read_manifest(work, "md")
    candidate = _publication_full_dedup_candidate(
        manifest_kind="missing",
        coverage_known=False,
    )

    result = _publish_nonzero(
        publish_nonzero_candidate,
        work=work,
        manifest=manifest,
        candidate=candidate,
        collection_mode=models.MLFFCollectMode.FULL_DEDUP,
        transaction_id="plan10-task3-missing-full-dedup",
    )

    compatibility = yaml.safe_load(
        (work / "MD_data.collect.yaml").read_text(encoding="utf-8")
    )
    assert result.status is models.CollectStatus.DEGRADED
    assert not manifest_path(work, "md").exists()
    assert compatibility["directory_discovery"] == "legacy-scan"
    assert compatibility["discovered_directories"] == ["md/run-a"]
    assert compatibility["collect"]["inventory"] == {
        "manifest_kind": "missing",
        "directory_discovery": "legacy-scan",
        "directories": ["md/run-a"],
        "coverage_known": False,
        "warnings": ["legacy scan cannot prove expected-source coverage"],
    }


def test_compatibility_publication_never_rewrites_legacy_stage_manifest(tmp_path):
    publish_nonzero_candidate = _nonzero_publication_api()
    work = tmp_path / "work"
    directories = ("md/run-a",)
    legacy_path, legacy_bytes, manifest = _write_legacy_publication_manifest(
        work,
        directories=directories,
    )
    candidate = _publication_candidate(
        models.SourceStatus.COMPLETE,
        directories=directories,
    )

    result = _publish_nonzero(
        publish_nonzero_candidate,
        work=work,
        manifest=manifest,
        candidate=candidate,
        collection_mode=models.MLFFCollectMode.SEED_AWARE,
        transaction_id="plan10-task3-preserve-legacy",
    )

    assert result.status is models.CollectStatus.COMPLETE
    assert legacy_path.read_bytes() == legacy_bytes
    assert (work / "MD_data.collect.yaml").is_file()
    assert (work / "MD_data.extxyz").is_file()


def _fatal_recovery_api():
    orchestrator = getattr(collect_module, "orchestrate_collect", None)
    assert callable(orchestrator), "orchestrate_collect is not implemented"
    return orchestrator


def _run_orchestration(orchestrator, config_path, mode, transaction_id):
    return orchestrator(
        config_path=config_path,
        stage="md",
        collection_mode=mode,
        transaction_id=transaction_id,
    )


def _write_invalid_md_manifest(work: Path, payload: str) -> Path:
    path = manifest_path(work, "md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path


def _copy_mlab_fixture(work: Path, directory: str) -> Path:
    source = Path(__file__).parent / "data" / "mlab" / "complete_multi.mlab"
    target = work / directory / "ML_ABN"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def _recovery_publication_request(work: Path, *, transaction_id: str):
    dataset = Dataset()
    dataset.add_atoms(_publication_frame(7))
    target = collect_publish_module.ResultManifestTarget(
        kind=collect_publish_module.ResultManifestTargetKind.CURRENT_STAGE,
        path=manifest_path(work, "md"),
    )
    return collect_publish_module.CandidateRequest(
        work_dir=work,
        stage="md",
        final_output=work / "MD_data.extxyz",
        target=target,
        transaction_id=transaction_id,
        dataset=dataset,
        expected_frame_count=1,
        status="degraded",
        source_diagnostics=(
            {
                "path": "md/run-a/OUTCAR",
                "kind": "outcar",
                "status": "partial",
                "complete_count": 1,
                "accepted_count": 1,
                "reason": "synthetic recoverable partial source",
            },
        ),
        current_manifest=read_manifest(work, "md"),
    )


def _interrupt_orchestration_recovery(
    root: Path,
    monkeypatch,
    *,
    boundary: str,
    previous_output: bool,
):
    config_path = write_collect_config(root, vasp_ml=False)
    work = root / "work"
    _write_current_publication_manifest(work, directories=("md/run-a",))
    request = _recovery_publication_request(
        work,
        transaction_id=f"plan10-task4-recovery-{boundary}",
    )
    if previous_output:
        _write_previous_extxyz(request.final_output, frame_count=1)

    with monkeypatch.context() as patch:
        if boundary == "before_data_replace":
            real_replace = collect_publish_module.os.replace
            blocked_destination = request.final_output.resolve(strict=False)

            def interrupting_replace(source, destination, *args, **kwargs):
                if Path(destination).resolve(strict=False) == blocked_destination:
                    raise OSError("synthetic Task 4 interruption before data replace")
                return real_replace(source, destination, *args, **kwargs)

            patch.setattr(collect_publish_module.os, "replace", interrupting_replace)
        elif boundary == "before_committed":
            real_publish_journal = collect_publish_module._publish_journal

            def interrupting_publish_journal(path, record):
                if record["state"] == "committed":
                    raise OSError("synthetic Task 4 interruption before commit")
                return real_publish_journal(path, record)

            patch.setattr(
                collect_publish_module,
                "_publish_journal",
                interrupting_publish_journal,
            )
        else:
            raise AssertionError(f"unknown recovery boundary: {boundary}")

        with pytest.raises(
            collect_publish_module.PublicationError,
            match="synthetic Task 4 interruption",
        ):
            with collect_publish_module.PublicationSession(
                work_dir=request.work_dir,
                stage=request.stage,
                final_output=request.final_output,
                target=request.target,
            ) as session:
                session_request = replace(
                    request,
                    previous_output_sha256=session.previous_output_sha256,
                    previous_manifest_sha256=session.previous_manifest_sha256,
                )
                session.publish(session_request)

    journal_path = work / ".MD_data.extxyz.collect-journal.yaml"
    journal = yaml.safe_load(journal_path.read_text(encoding="utf-8"))

    def artifact_path(field: str):
        value = journal.get(field)
        return None if value is None else work / Path(value)

    return {
        "config_path": config_path,
        "work": work,
        "request": request,
        "journal_path": journal_path,
        "journal": journal,
        "data_candidate": artifact_path("data_candidate_path"),
        "manifest_candidate": artifact_path("manifest_candidate_path"),
        "backup_path": artifact_path("backup_path"),
    }


def _publish_historical_compatibility(work: Path, *, transaction_id: str):
    publisher = _nonzero_publication_api()
    legacy_path, legacy_bytes, manifest = _write_legacy_publication_manifest(
        work,
        directories=("md/run-a",),
    )
    candidate = _publication_candidate(
        models.SourceStatus.COMPLETE,
        directories=("md/run-a",),
    )
    result = _publish_nonzero(
        publisher,
        work=work,
        manifest=manifest,
        candidate=candidate,
        collection_mode=models.MLFFCollectMode.SEED_AWARE,
        transaction_id=transaction_id,
    )
    compatibility_path = work / "MD_data.collect.yaml"
    assert result.status is models.CollectStatus.COMPLETE
    return (
        legacy_path,
        legacy_bytes,
        compatibility_path,
        compatibility_path.read_bytes(),
    )


def test_seed_aware_missing_stage_manifest_returns_fatal_without_creating_manifest(
    tmp_path,
):
    orchestrate_collect = _fatal_recovery_api()
    config_path = write_collect_config(tmp_path)
    work = tmp_path / "work"

    result = _run_orchestration(
        orchestrate_collect,
        config_path,
        models.MLFFCollectMode.SEED_AWARE,
        "plan10-task4-seed-aware-missing",
    )

    assert result.status is models.CollectStatus.FATAL
    assert result.publication_committed is False
    assert "manifest" in result.fatal_diagnostic.lower()
    assert "missing" in result.fatal_diagnostic.lower()
    assert not manifest_path(work, "md").exists()
    assert not (work / "MD_data.collect.yaml").exists()
    assert not (work / "MD_data.extxyz").exists()


def test_missing_manifest_full_dedup_uses_legacy_scan_instead_of_fatal(tmp_path):
    orchestrate_collect = _fatal_recovery_api()
    config_path = write_collect_config(tmp_path)
    work = tmp_path / "work"
    _copy_mlab_fixture(work, "md/run-a")

    result = _run_orchestration(
        orchestrate_collect,
        config_path,
        models.MLFFCollectMode.FULL_DEDUP,
        "plan10-task4-missing-full-dedup",
    )

    compatibility = yaml.safe_load(
        (work / "MD_data.collect.yaml").read_text(encoding="utf-8")
    )
    frames = ase_read(work / "MD_data.extxyz", format="extxyz", index=":")
    assert result.status is models.CollectStatus.DEGRADED
    assert result.publication_committed is True
    assert result.accepted_frame_count == len(frames) > 0
    assert not manifest_path(work, "md").exists()
    assert compatibility["input_layout"] == "missing-stage-manifest"
    assert compatibility["directory_discovery"] == "legacy-scan"
    assert compatibility["collect"]["status"] == "degraded"


def test_invalid_manifest_returns_fatal_without_overwrite(tmp_path):
    orchestrate_collect = _fatal_recovery_api()
    config_path = write_collect_config(tmp_path)
    work = tmp_path / "work"
    invalid_path = _write_invalid_md_manifest(
        work,
        "schema_version: 999\nstage: md\ngenerated_at: invalid\n",
    )
    invalid_bytes = invalid_path.read_bytes()

    result = _run_orchestration(
        orchestrate_collect,
        config_path,
        models.MLFFCollectMode.SEED_AWARE,
        "plan10-task4-invalid-manifest",
    )

    assert result.status is models.CollectStatus.FATAL
    assert result.publication_committed is False
    assert "manifest" in result.fatal_diagnostic.lower()
    assert invalid_path.read_bytes() == invalid_bytes
    assert not (work / "MD_data.collect.yaml").exists()
    assert not (work / "MD_data.extxyz").exists()


def test_invalid_manifest_full_dedup_never_falls_back_to_scan(tmp_path):
    orchestrate_collect = _fatal_recovery_api()
    config_path = write_collect_config(tmp_path)
    work = tmp_path / "work"
    source = _copy_mlab_fixture(work, "md/run-a")
    source_bytes = source.read_bytes()
    invalid_path = _write_invalid_md_manifest(
        work,
        "schema_version: [2\nstage: md\n",
    )
    invalid_bytes = invalid_path.read_bytes()

    result = _run_orchestration(
        orchestrate_collect,
        config_path,
        models.MLFFCollectMode.FULL_DEDUP,
        "plan10-task4-invalid-full-dedup",
    )

    assert result.status is models.CollectStatus.FATAL
    assert result.publication_committed is False
    assert source.read_bytes() == source_bytes
    assert invalid_path.read_bytes() == invalid_bytes
    assert not (work / "MD_data.collect.yaml").exists()
    assert not (work / "MD_data.extxyz").exists()


def test_config_error_returns_fatal_before_lock_or_manifest(tmp_path, monkeypatch):
    orchestrate_collect = _fatal_recovery_api()
    config_path = write_collect_config(tmp_path, n_nodes=0)
    touched = []

    def unexpected_manifest_read(*args, **kwargs):
        touched.append("manifest")
        raise AssertionError("manifest read occurred after invalid config")

    def unexpected_session(*args, **kwargs):
        touched.append("lock")
        raise AssertionError("publication session opened after invalid config")

    monkeypatch.setattr(collect_module, "read_manifest", unexpected_manifest_read)
    monkeypatch.setattr(collect_module, "PublicationSession", unexpected_session)

    result = _run_orchestration(
        orchestrate_collect,
        config_path,
        models.MLFFCollectMode.SEED_AWARE,
        "plan10-task4-invalid-config",
    )

    assert result.status is models.CollectStatus.FATAL
    assert result.publication_committed is False
    assert "n_nodes" in result.fatal_diagnostic
    assert touched == []


def test_invalid_manifest_seed_schema_is_global_fatal(tmp_path):
    orchestrate_collect = _fatal_recovery_api()
    config_path = write_collect_config(tmp_path)
    work = tmp_path / "work"
    write_manifest(
        work,
        Manifest(
            stage="md",
            generated_at="invalid-seed-schema",
            directories=[],
            mlff_seed={
                "configurations": 1,
                "digest_schema": "unsupported-seed-schema",
                "seed_prefix_sha256": "0" * 64,
            },
        ),
    )
    stage_manifest = manifest_path(work, "md")
    manifest_bytes = stage_manifest.read_bytes()

    result = _run_orchestration(
        orchestrate_collect,
        config_path,
        models.MLFFCollectMode.SEED_AWARE,
        "plan10-task4-invalid-seed-schema",
    )

    assert result.status is models.CollectStatus.FATAL
    assert result.candidate is None
    assert result.publication_committed is False
    assert "digest_schema" in result.fatal_diagnostic
    assert stage_manifest.read_bytes() == manifest_bytes
    assert not (work / "MD_data.extxyz").exists()


def test_unrecoverable_pending_journal_returns_fatal_and_preserves_evidence(
    tmp_path,
    monkeypatch,
):
    orchestrate_collect = _fatal_recovery_api()
    case = _interrupt_orchestration_recovery(
        tmp_path,
        monkeypatch,
        boundary="before_data_replace",
        previous_output=True,
    )
    data_candidate = case["data_candidate"]
    backup_path = case["backup_path"]
    assert data_candidate is not None and data_candidate.is_file()
    assert backup_path is not None and backup_path.is_file()
    data_candidate.write_bytes(b"corrupt recovery candidate evidence\n")
    evidence_paths = tuple(
        path
        for path in (
            case["request"].final_output,
            case["request"].target.path,
            case["journal_path"],
            data_candidate,
            case["manifest_candidate"],
            backup_path,
        )
        if path is not None and path.exists()
    )
    snapshot = {path: path.read_bytes() for path in evidence_paths}

    result = _run_orchestration(
        orchestrate_collect,
        case["config_path"],
        models.MLFFCollectMode.SEED_AWARE,
        "plan10-task4-unrecoverable-call",
    )

    assert result.status is models.CollectStatus.FATAL
    assert result.publication_committed is False
    assert "data candidate" in result.fatal_diagnostic.lower()
    assert {path: path.read_bytes() for path in evidence_paths} == snapshot


def test_recovered_transaction_returns_its_committed_status(tmp_path, monkeypatch):
    orchestrate_collect = _fatal_recovery_api()
    case = _interrupt_orchestration_recovery(
        tmp_path,
        monkeypatch,
        boundary="before_committed",
        previous_output=False,
    )

    result = _run_orchestration(
        orchestrate_collect,
        case["config_path"],
        models.MLFFCollectMode.SEED_AWARE,
        "plan10-task4-recovery-call",
    )

    recovered = result.recovered_evidence
    collect = _current_collect_record(case["work"])
    assert result.status is models.CollectStatus.DEGRADED
    assert result.candidate is None
    assert result.publication_committed is True
    assert isinstance(recovered, models.RecoveredCollectionEvidence)
    assert recovered.transaction_id == case["journal"]["transaction_id"]
    assert result.accepted_frame_count == collect["frames"] == 1
    assert collect["status"] == "degraded"
    assert not case["journal_path"].exists()


def test_recovery_completes_before_new_source_collection_starts(
    tmp_path,
    monkeypatch,
):
    orchestrate_collect = _fatal_recovery_api()
    case = _interrupt_orchestration_recovery(
        tmp_path,
        monkeypatch,
        boundary="before_committed",
        previous_output=False,
    )
    source_calls = []

    def unexpected_candidate_build(*args, **kwargs):
        source_calls.append((args, kwargs))
        raise AssertionError("source collection started after recovered transaction")

    monkeypatch.setattr(
        collect_module,
        "build_seed_aware_candidate",
        unexpected_candidate_build,
    )

    result = _run_orchestration(
        orchestrate_collect,
        case["config_path"],
        models.MLFFCollectMode.SEED_AWARE,
        "plan10-task4-recovery-order-call",
    )

    assert result.status is models.CollectStatus.DEGRADED
    assert result.publication_committed is True
    assert source_calls == []
    assert not case["journal_path"].exists()


def test_lock_contention_returns_fatal(tmp_path):
    orchestrate_collect = _fatal_recovery_api()
    config_path = write_collect_config(tmp_path, vasp_ml=False)
    work = tmp_path / "work"
    _write_current_publication_manifest(work, directories=())
    stage_manifest = manifest_path(work, "md")
    manifest_bytes = stage_manifest.read_bytes()
    output = work / "MD_data.extxyz"

    with CollectFileLock(
        "md",
        output,
        transaction_id="plan10-task4-held-lock",
    ):
        result = _run_orchestration(
            orchestrate_collect,
            config_path,
            models.MLFFCollectMode.SEED_AWARE,
            "plan10-task4-contender",
        )

    assert result.status is models.CollectStatus.FATAL
    assert result.publication_committed is False
    assert "lock" in result.fatal_diagnostic.lower()
    assert stage_manifest.read_bytes() == manifest_bytes
    assert not output.exists()


def test_current_v2_manifest_takes_precedence_over_old_compatibility_result(
    tmp_path,
):
    orchestrate_collect = _fatal_recovery_api()
    config_path = write_collect_config(tmp_path, vasp_ml=False)
    work = tmp_path / "work"
    _, _, compatibility_path, compatibility_bytes = _publish_historical_compatibility(
        work,
        transaction_id="plan10-task4-historical-compatibility",
    )
    output = work / "MD_data.extxyz"
    preserved_output = _write_previous_extxyz(output, frame_count=2)
    _write_current_publication_manifest(work, directories=())

    result = _run_orchestration(
        orchestrate_collect,
        config_path,
        models.MLFFCollectMode.SEED_AWARE,
        "plan10-task4-current-authority",
    )

    collect = _current_collect_record(work)
    assert result.status is models.CollectStatus.NO_DATA
    assert result.publication_committed is True
    assert collect["transaction_id"] == "plan10-task4-current-authority"
    assert collect["status"] == "no_data"
    assert compatibility_path.read_bytes() == compatibility_bytes
    assert output.read_bytes() == preserved_output


def test_compatibility_previous_result_requires_matching_output_hash(tmp_path):
    orchestrate_collect = _fatal_recovery_api()
    config_path = write_collect_config(tmp_path)
    work = tmp_path / "work"
    (
        legacy_path,
        legacy_bytes,
        compatibility_path,
        compatibility_bytes,
    ) = _publish_historical_compatibility(
        work,
        transaction_id="plan10-task4-compatibility-before-mismatch",
    )
    output = work / "MD_data.extxyz"
    mismatching_output = _write_previous_extxyz(output, frame_count=2)

    result = _run_orchestration(
        orchestrate_collect,
        config_path,
        models.MLFFCollectMode.SEED_AWARE,
        "plan10-task4-compatibility-mismatch",
    )

    assert result.status is models.CollectStatus.FATAL
    assert result.publication_committed is False
    assert "hash" in result.fatal_diagnostic.lower()
    assert legacy_path.read_bytes() == legacy_bytes
    assert compatibility_path.read_bytes() == compatibility_bytes
    assert output.read_bytes() == mismatching_output


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

    def fake_collect_outcar_source(*, work_dir, selection, freq):
        del work_dir, selection
        seen_freqs.append(freq)
        return models.SourceResult(
            source_path="validation/1.00deg/OUTCAR",
            source_kind=models.SourceKind.OUTCAR,
            status=models.SourceStatus.COMPLETE,
            complete_count=1,
            accepted_frames=(_publication_frame(1),),
            pattern="^OUTCAR$",
            pattern_index=0,
            order=0,
        )

    monkeypatch.setattr(
        collect_module,
        "collect_outcar_source",
        fake_collect_outcar_source,
    )
    monkeypatch.setattr(
        collect_module,
        "find_outcar_series",
        lambda directory, patterns: [Path(directory) / "OUTCAR"],
        raising=False,
    )

    result = run_collect(config_path, stage="validation")

    assert seen_freqs == [1]
    assert result.status is models.CollectStatus.COMPLETE
    assert result.publication_committed is True
    assert result.accepted_frame_count == 1
    manifest = read_manifest(work, "validation")
    assert manifest.collect["frames"] == 1


def test_collect_missing_stage_manifest_fails_without_output_or_manifest(tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=False)
    work = tmp_path / "work"

    result = run_collect(config_path, stage="rlx")

    assert result.status is models.CollectStatus.FATAL
    assert result.publication_committed is False
    message = result.fatal_diagnostic.lower()
    assert "manifest" in message
    assert "rlx" in message
    assert not (work / "rlx_data.extxyz").exists()
    assert not (work / "rlx" / "manifest.yaml").exists()


def test_collect_does_not_derive_directories_from_config(tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=False, n_sectors=[2, 1])
    work = tmp_path / "work"
    (work / "rlx" / "0_0").mkdir(parents=True)

    result = run_collect(config_path, stage="rlx")

    assert result.status is models.CollectStatus.FATAL
    assert result.publication_committed is False
    message = result.fatal_diagnostic.lower()
    assert "manifest" in message
    assert "rlx" in message
    assert not (work / "rlx_data.extxyz").exists()
    assert not (work / "rlx" / "manifest.yaml").exists()


def test_collect_does_not_scan_validation_directories_without_manifest(tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=False)
    work = tmp_path / "work"
    (work / "validation" / "1.00deg").mkdir(parents=True)

    result = run_collect(config_path, stage="validation")

    assert result.status is models.CollectStatus.FATAL
    assert result.publication_committed is False
    message = result.fatal_diagnostic.lower()
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
