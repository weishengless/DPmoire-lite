from __future__ import annotations

import errno
import importlib
import os
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator
from ase.io import write as ase_write

from dpmoire_lite import atomic_io
from dpmoire_lite.dataset import Dataset
from dpmoire_lite.file_lock import CollectFileLock, CollectLockError
from dpmoire_lite.manifest import Manifest, write_manifest
from dpmoire_lite.paths import manifest_path


def _api():
    try:
        module = importlib.import_module("dpmoire_lite.collect_publish")
    except ModuleNotFoundError as exc:
        if exc.name == "dpmoire_lite.collect_publish":
            pytest.fail("candidate preparation is not implemented")
        raise

    required = (
        "CandidateArtifacts",
        "CandidateRequest",
        "CandidateValidationError",
        "CompatibilityManifestEvidence",
        "ResultManifestTarget",
        "ResultManifestTargetKind",
        "prepare_candidates",
    )
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        pytest.fail(f"candidate preparation API is incomplete: {missing}")
    return module


def _backup_api():
    try:
        module = importlib.import_module("dpmoire_lite.collect_publish")
    except ModuleNotFoundError as exc:
        if exc.name == "dpmoire_lite.collect_publish":
            pytest.fail("backup preparation is not implemented")
        raise

    required = ("BackupError", "BackupArtifacts", "prepare_backup")
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        pytest.fail(f"backup preparation API is incomplete: {missing}")
    return module


def _publication_api():
    module = _api()
    required = ("PublicationError", "PublicationResult", "PublicationSession")
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        pytest.fail(f"publication transaction API is incomplete: {missing}")
    return module


def _atoms(offset: float) -> Atoms:
    atoms = Atoms(
        "Si2",
        positions=[[offset, 0.0, 0.0], [1.4 + offset, 0.0, 0.0]],
        cell=np.diag([8.0, 8.0, 8.0]),
        pbc=True,
    )
    atoms.calc = SinglePointCalculator(
        atoms,
        energy=-2.0 + offset,
        forces=np.array(
            [[0.1 + offset, 0.0, 0.0], [-0.1 - offset, 0.0, 0.0]],
            dtype=float,
        ),
        stress=np.array([1.0, 1.1, 1.2, 0.1, 0.2, 0.3], dtype=float),
    )
    return atoms


def _dataset() -> Dataset:
    dataset = Dataset()
    dataset.add_atoms(_atoms(0.0))
    dataset.add_atoms(_atoms(0.2))
    return dataset


def _compatibility_evidence(api):
    return api.CompatibilityManifestEvidence(
        input_layout="missing-stage-manifest",
        directory_discovery="immediate-md-children",
        declared_directories=(),
        discovered_directories=("md/run-a", "md/run-b"),
        collection_mode="full-dedup",
        dedup={
            "schema": "mlab-canonical-v1",
            "seen": 3,
            "retained": 2,
            "duplicates_removed": 1,
        },
    )


def _request(
    tmp_path: Path,
    *,
    target_kind: str = "current",
    dataset: Dataset | None = None,
    data_writer=None,
):
    api = _api()
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    stage = "md"
    final_output = work_dir / "MD_data.extxyz"
    current_manifest = None
    compatibility_evidence = None

    if target_kind == "current":
        current_manifest = Manifest(
            stage=stage,
            generated_at="2026-07-13T00:00:00+00:00",
            directories=["md/run-a", "md/run-b"],
        )
        write_manifest(work_dir, current_manifest)
        target = api.ResultManifestTarget(
            kind=api.ResultManifestTargetKind.CURRENT_STAGE,
            path=manifest_path(work_dir, stage),
        )
    elif target_kind == "compatibility":
        compatibility_evidence = _compatibility_evidence(api)
        target = api.ResultManifestTarget(
            kind=api.ResultManifestTargetKind.MD_COMPATIBILITY,
            path=work_dir / "MD_data.collect.yaml",
        )
    else:
        raise AssertionError(f"unknown test target kind: {target_kind}")

    candidate_dataset = dataset if dataset is not None else _dataset()
    return api.CandidateRequest(
        work_dir=work_dir,
        stage=stage,
        final_output=final_output,
        target=target,
        transaction_id="collect-task-2",
        dataset=candidate_dataset,
        expected_frame_count=candidate_dataset.n_configs,
        status="complete",
        source_diagnostics=(
            {"path": "md/run-a/ML_ABN", "status": "complete", "frames": 1},
            {"path": "md/run-b/ML_ABN", "status": "complete", "frames": 1},
        ),
        previous_output_sha256="previous-data-sha256",
        previous_manifest_sha256="previous-manifest-sha256",
        backup_path="backups/collect/MD_data.collect-task-2.extxyz",
        backup_sha256="backup-sha256",
        current_manifest=current_manifest,
        compatibility_evidence=compatibility_evidence,
        data_writer=data_writer,
    )


def _backup_case(tmp_path: Path, *, existing: bool = True):
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    final_output = work_dir / "MD_data.extxyz"
    dataset = _dataset()
    if existing:
        dataset.save_extxyz(final_output)
        previous_bytes = final_output.read_bytes()
        previous_sha256 = atomic_io.sha256_file(final_output)
    else:
        previous_bytes = None
        previous_sha256 = None
    return work_dir, final_output, dataset, previous_bytes, previous_sha256


def _expected_backup_path(work_dir: Path, final_output: Path, transaction_id: str) -> Path:
    return (
        work_dir
        / "backups"
        / "collect"
        / f"{final_output.stem}.{transaction_id}{final_output.suffix}"
    )


def _journal_path(final_output: Path) -> Path:
    return final_output.parent / f".{final_output.name}.collect-journal.yaml"


def _request_for_session(request, session):
    return replace(
        request,
        previous_output_sha256=session.previous_output_sha256,
        previous_manifest_sha256=session.previous_manifest_sha256,
        backup_path=None,
        backup_sha256=None,
    )


def _assert_same_output_lock_is_held(request) -> None:
    with pytest.raises(CollectLockError):
        with CollectFileLock(
            request.stage,
            request.final_output,
            transaction_id="contender",
        ):
            pass


def _publish_request(request):
    api = _publication_api()
    with api.PublicationSession(
        work_dir=request.work_dir,
        stage=request.stage,
        final_output=request.final_output,
        target=request.target,
    ) as session:
        result = session.publish(_request_for_session(request, session))
        journal_path = session.journal_path
    return api, result, journal_path


def _write_previous_output(request) -> bytes:
    previous = Dataset()
    previous.add_atoms(_atoms(-0.2))
    previous.save_extxyz(request.final_output)
    return request.final_output.read_bytes()


def test_data_candidate_written_in_final_directory_and_fsynced(tmp_path, monkeypatch):
    api = _api()
    request = _request(tmp_path)
    fsynced: list[Path] = []
    real_fsync_path = atomic_io.fsync_path

    def recording_fsync(path: Path) -> None:
        fsynced.append(Path(path))
        real_fsync_path(path)

    monkeypatch.setattr(atomic_io, "fsync_path", recording_fsync)

    artifacts = api.prepare_candidates(request)

    assert artifacts.data_candidate_path.parent == request.final_output.parent
    assert artifacts.data_candidate_path.name.startswith(f".{request.final_output.name}.")
    assert artifacts.data_candidate_path.suffix == ".candidate"
    assert artifacts.data_candidate_path in fsynced


def test_data_candidate_reread_matches_expected_frame_count(tmp_path):
    api = _api()
    request = _request(tmp_path)

    def omit_last_frame(dataset: Dataset, path: Path) -> None:
        ase_write(path, dataset.data[:-1], format="extxyz")

    request = replace(request, data_writer=omit_last_frame)

    with pytest.raises(api.CandidateValidationError, match="frame count"):
        api.prepare_candidates(request)

    assert not request.final_output.exists()


def test_data_candidate_requires_energy_forces_and_stress(tmp_path):
    api = _api()
    incomplete = Dataset()
    incomplete.data.append(
        Atoms(
            "Si2",
            positions=[[0.0, 0.0, 0.0], [1.4, 0.0, 0.0]],
            cell=np.diag([8.0, 8.0, 8.0]),
            pbc=True,
        )
    )
    incomplete.n_configs = 1
    request = _request(tmp_path, dataset=incomplete)

    with pytest.raises(
        api.CandidateValidationError,
        match="energy.*forces.*stress|energy, forces, and stress",
    ):
        api.prepare_candidates(request)

    assert not request.final_output.exists()


def test_data_candidate_rejects_shape_or_truncated_tail(tmp_path):
    api = _api()

    def truncate_tail(dataset: Dataset, path: Path) -> None:
        dataset.save_extxyz(path)
        payload = path.read_bytes()
        path.write_bytes(payload[:-23])

    request = _request(tmp_path, data_writer=truncate_tail)

    with pytest.raises(
        api.CandidateValidationError,
        match="candidate|frame count|shape|truncated|reread",
    ):
        api.prepare_candidates(request)

    assert not request.final_output.exists()


def test_manifest_candidate_contains_transaction_and_data_hash(tmp_path):
    api = _api()
    request = _request(tmp_path)

    artifacts = api.prepare_candidates(request)
    data = yaml.safe_load(artifacts.manifest_candidate_path.read_text(encoding="utf-8"))

    assert data["collect"] == {
        "transaction_id": request.transaction_id,
        "status": "complete",
        "frames": 2,
        "written": True,
        "output": "MD_data.extxyz",
        "output_sha256": artifacts.data_sha256,
        "previous_sha256": "previous-data-sha256",
        "previous_manifest_sha256": "previous-manifest-sha256",
        "backup": "backups/collect/MD_data.collect-task-2.extxyz",
        "backup_sha256": "backup-sha256",
        "sources": list(request.source_diagnostics),
    }


def test_current_target_writes_stage_manifest_candidate(tmp_path):
    api = _api()
    request = _request(tmp_path)
    official_manifest = request.target.path.read_bytes()

    artifacts = api.prepare_candidates(request)
    data = yaml.safe_load(artifacts.manifest_candidate_path.read_text(encoding="utf-8"))

    assert artifacts.manifest_candidate_path.parent == request.target.path.parent
    assert artifacts.manifest_candidate_path != request.target.path
    assert data["schema_version"] == 2
    assert data["stage"] == request.stage
    assert request.target.path.read_bytes() == official_manifest


def test_compatibility_target_writes_md_data_collect_candidate(tmp_path):
    api = _api()
    request = _request(tmp_path, target_kind="compatibility")

    artifacts = api.prepare_candidates(request)
    data = yaml.safe_load(artifacts.manifest_candidate_path.read_text(encoding="utf-8"))

    assert artifacts.manifest_candidate_path.parent == request.work_dir
    assert artifacts.manifest_candidate_path != request.target.path
    assert data["schema_version"] == 1
    assert data["kind"] == "dpmoire-lite-collect-result"
    assert data["stage"] == "md"
    assert data["input_layout"] == "missing-stage-manifest"
    assert data["directory_discovery"] == "immediate-md-children"
    assert data["declared_directories"] == []
    assert data["discovered_directories"] == ["md/run-a", "md/run-b"]
    assert data["collection_mode"] == "full-dedup"
    assert data["dedup"]["duplicates_removed"] == 1
    assert data["collect"]["output"] == "MD_data.extxyz"
    assert data["collect"]["output_sha256"] == artifacts.data_sha256


def test_compatibility_target_rejects_wrong_stage_output_or_path(tmp_path, monkeypatch):
    api = _api()
    request = _request(tmp_path, target_kind="compatibility")

    def forbid_candidate_creation(_destination: Path) -> Path:
        raise AssertionError("invalid target must fail before creating candidates")

    monkeypatch.setattr(atomic_io, "create_candidate", forbid_candidate_creation)
    invalid_requests = (
        replace(request, stage="rlx"),
        replace(request, final_output=request.work_dir / "rlx_data.extxyz"),
        replace(
            request,
            target=api.ResultManifestTarget(
                kind=api.ResultManifestTargetKind.MD_COMPATIBILITY,
                path=request.work_dir / "other.collect.yaml",
            ),
        ),
    )

    for invalid in invalid_requests:
        with pytest.raises(api.CandidateValidationError, match="compatibility target"):
            api.prepare_candidates(invalid)


def test_compatibility_candidate_preserves_legacy_stage_manifest_bytes(tmp_path):
    api = _api()
    request = _request(tmp_path, target_kind="compatibility")
    legacy_manifest = manifest_path(request.work_dir, "md")
    legacy_manifest.parent.mkdir(parents=True, exist_ok=True)
    legacy_bytes = b"stage: md\ndirectories:\n  - run-a\nlegacy_key: keep-me\n"
    legacy_manifest.write_bytes(legacy_bytes)

    api.prepare_candidates(request)

    assert legacy_manifest.read_bytes() == legacy_bytes


def test_both_candidates_exist_and_validate_before_pending_journal(tmp_path):
    api = _api()
    request = _request(tmp_path)

    artifacts = api.prepare_candidates(request)

    assert artifacts.data_candidate_path.is_file()
    assert artifacts.manifest_candidate_path.is_file()
    assert atomic_io.sha256_file(artifacts.data_candidate_path) == artifacts.data_sha256
    assert atomic_io.sha256_file(artifacts.manifest_candidate_path) == artifacts.manifest_sha256
    assert not list(request.work_dir.rglob("*journal*"))


def test_candidate_failure_leaves_final_and_previous_manifest_unchanged(tmp_path):
    api = _api()

    def corrupt_data(_dataset: Dataset, path: Path) -> None:
        path.write_bytes(b"not an extxyz candidate")

    request = _request(tmp_path, data_writer=corrupt_data)
    request.final_output.write_bytes(b"previous formal dataset bytes")
    previous_output = request.final_output.read_bytes()
    previous_manifest = request.target.path.read_bytes()

    with pytest.raises(api.CandidateValidationError):
        api.prepare_candidates(request)

    assert request.final_output.read_bytes() == previous_output
    assert request.target.path.read_bytes() == previous_manifest
    assert not list(request.final_output.parent.glob(f".{request.final_output.name}.*.candidate"))
    assert not list(request.target.path.parent.glob(f".{request.target.path.name}.*.candidate"))


def test_backup_prefers_hardlink_and_preserves_final_path(tmp_path, monkeypatch):
    api = _backup_api()
    work_dir, final_output, dataset, previous_bytes, previous_sha256 = _backup_case(tmp_path)
    transaction_id = "collect-task-3-hardlink"
    link_observations = []
    real_link = os.link

    def forbid_copyfileobj(*args, **kwargs):
        raise AssertionError("hardlink backup must not use copyfileobj")

    monkeypatch.setattr(shutil, "copyfileobj", forbid_copyfileobj)

    def recording_link(source, destination, *args, **kwargs):
        link_observations.append(
            (Path(source), Path(destination), final_output.exists())
        )
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", recording_link)

    with CollectFileLock("md", final_output, transaction_id=transaction_id):
        artifacts = api.prepare_backup(
            work_dir=work_dir,
            stage="md",
            final_output=final_output,
            transaction_id=transaction_id,
        )

    assert isinstance(artifacts, api.BackupArtifacts)
    assert final_output.exists()
    assert final_output.read_bytes() == previous_bytes
    assert artifacts.previous_output_sha256 == previous_sha256
    assert artifacts.previous_output_frames == dataset.n_configs
    assert artifacts.backup_path == _expected_backup_path(
        work_dir, final_output, transaction_id
    )
    assert artifacts.backup_path is not None
    assert artifacts.backup_path.read_bytes() == previous_bytes
    assert artifacts.backup_sha256 == previous_sha256
    assert link_observations
    assert all(observation[2] for observation in link_observations)
    assert os.path.samestat(
        os.stat(final_output), os.stat(artifacts.backup_path)
    )


def test_backup_falls_back_to_copy_when_hardlink_unsupported(tmp_path, monkeypatch):
    api = _backup_api()
    work_dir, final_output, dataset, previous_bytes, previous_sha256 = _backup_case(tmp_path)
    transaction_id = "collect-task-3-copy"
    link_attempted = []
    copy_observations = []

    real_copyfileobj = shutil.copyfileobj

    def recording_copyfileobj(source, destination, *args, **kwargs):
        copy_observations.append(
            (Path(source.name), Path(destination.name), final_output.exists())
        )
        return real_copyfileobj(source, destination, *args, **kwargs)

    def unsupported_link(source, destination, *args, **kwargs):
        link_attempted.append((Path(source), Path(destination), final_output.exists()))
        raise OSError(errno.EXDEV, "cross-device hard link")

    monkeypatch.setattr(os, "link", unsupported_link)
    monkeypatch.setattr(shutil, "copyfileobj", recording_copyfileobj)

    with CollectFileLock("md", final_output, transaction_id=transaction_id):
        artifacts = api.prepare_backup(
            work_dir=work_dir,
            stage="md",
            final_output=final_output,
            transaction_id=transaction_id,
        )

    assert final_output.exists()
    assert final_output.read_bytes() == previous_bytes
    assert artifacts.previous_output_sha256 == previous_sha256
    assert artifacts.previous_output_frames == dataset.n_configs
    assert artifacts.backup_path is not None
    assert artifacts.backup_path.read_bytes() == previous_bytes
    assert artifacts.backup_sha256 == previous_sha256
    assert link_attempted
    assert all(observation[2] for observation in link_attempted)
    assert copy_observations
    assert all(observation[2] for observation in copy_observations)
    expected_backup = _expected_backup_path(work_dir, final_output, transaction_id)
    assert all(
        source.resolve(strict=False) == final_output.resolve(strict=False)
        for source, _destination, _final_exists in copy_observations
    )
    assert all(
        destination.parent == expected_backup.parent
        and destination != expected_backup
        for _source, destination, _final_exists in copy_observations
    )
    assert not os.path.samestat(
        os.stat(final_output), os.stat(artifacts.backup_path)
    )


def test_backup_is_fsynced_and_hash_verified_before_publish(tmp_path, monkeypatch):
    api = _backup_api()
    work_dir, final_output, _dataset_value, _previous_bytes, _previous_sha256 = _backup_case(
        tmp_path
    )
    transaction_id = "collect-task-3-order"
    events = []

    real_ase_read = api.ase_read

    def record(name, **details):
        events.append(
            {
                "name": name,
                "final_exists": final_output.exists(),
                **details,
            }
        )

    def recording_ase_read(path, *args, **kwargs):
        path = Path(path)
        record("frame_read", path=path)
        return real_ase_read(path, *args, **kwargs)

    real_create_candidate = atomic_io.create_candidate

    def recording_create_candidate(destination):
        candidate = real_create_candidate(Path(destination))
        record("candidate", path=candidate)
        return candidate

    real_fsync_path = atomic_io.fsync_path

    def recording_fsync_path(path):
        record("fsync", path=Path(path))
        return real_fsync_path(Path(path))

    real_sha256_file = atomic_io.sha256_file

    def recording_sha256_file(path):
        path = Path(path)
        record("hash", path=path)
        return real_sha256_file(path)

    real_replace = os.replace

    def recording_replace(source, destination, *args, **kwargs):
        source = Path(source)
        destination = Path(destination)
        record(
            "replace",
            source=source,
            destination=destination,
            source_exists=source.exists(),
            destination_absent=not destination.exists(),
        )
        return real_replace(source, destination, *args, **kwargs)

    real_fsync_directory = getattr(
        atomic_io, "fsync_directory", atomic_io._fsync_directory
    )

    def recording_fsync_directory(directory):
        record("directory_fsync", path=Path(directory))
        return real_fsync_directory(Path(directory))

    monkeypatch.setattr(atomic_io, "create_candidate", recording_create_candidate)
    monkeypatch.setattr(atomic_io, "fsync_path", recording_fsync_path)
    monkeypatch.setattr(atomic_io, "sha256_file", recording_sha256_file)
    monkeypatch.setattr(api, "ase_read", recording_ase_read)
    monkeypatch.setattr(os, "replace", recording_replace)
    monkeypatch.setattr(
        atomic_io,
        "fsync_directory",
        recording_fsync_directory,
        raising=False,
    )

    with CollectFileLock("md", final_output, transaction_id=transaction_id):
        artifacts = api.prepare_backup(
            work_dir=work_dir,
            stage="md",
            final_output=final_output,
            transaction_id=transaction_id,
        )

    assert artifacts.backup_path is not None
    assert events
    assert all(event["final_exists"] for event in events)
    previous_hash_index = next(
        index
        for index, event in enumerate(events)
        if event["name"] == "hash" and event["path"] == final_output
    )
    frame_read_index = next(
        index
        for index, event in enumerate(events)
        if event["name"] == "frame_read"
    )
    frame_read_event = events[frame_read_index]
    assert frame_read_event["path"].resolve(strict=False) == final_output.resolve(
        strict=False
    )
    candidate_index = next(
        index for index, event in enumerate(events) if event["name"] == "candidate"
    )
    fsync_index = next(
        index for index, event in enumerate(events) if event["name"] == "fsync"
    )
    backup_hash_index = next(
        index
        for index, event in enumerate(events)
        if event["name"] == "hash" and event["path"] != final_output
    )
    replace_index = next(
        index for index, event in enumerate(events) if event["name"] == "replace"
    )
    directory_fsync_index = next(
        index
        for index, event in enumerate(events)
        if event["name"] == "directory_fsync"
    )
    assert (
        max(previous_hash_index, frame_read_index)
        < candidate_index
        < fsync_index
        < backup_hash_index
        < replace_index
        < directory_fsync_index
    )


def test_backup_hash_mismatch_aborts_before_journal(tmp_path, monkeypatch):
    api = _backup_api()
    work_dir, final_output, _dataset_value, previous_bytes, previous_sha256 = _backup_case(
        tmp_path
    )
    transaction_id = "collect-task-3-mismatch"
    older_backup = work_dir / "backups" / "collect" / "MD_data.older.extxyz"
    older_backup.parent.mkdir(parents=True, exist_ok=True)
    older_bytes = b"older backup bytes"
    older_backup.write_bytes(older_bytes)

    real_sha256_file = atomic_io.sha256_file

    def mismatching_sha256_file(path):
        path = Path(path)
        digest = real_sha256_file(path)
        if path != final_output:
            return "0" * 64
        return digest

    replace_calls = []

    def forbidden_replace(source, destination, *args, **kwargs):
        replace_calls.append((Path(source), Path(destination)))
        raise AssertionError("hash mismatch must abort before os.replace")

    monkeypatch.setattr(atomic_io, "sha256_file", mismatching_sha256_file)
    monkeypatch.setattr(os, "replace", forbidden_replace)

    with CollectFileLock("md", final_output, transaction_id=transaction_id):
        with pytest.raises(api.BackupError, match="hash"):
            api.prepare_backup(
                work_dir=work_dir,
                stage="md",
                final_output=final_output,
                transaction_id=transaction_id,
            )

    assert not replace_calls
    assert final_output.exists()
    assert final_output.read_bytes() == previous_bytes
    assert atomic_io.sha256_file(final_output) == previous_sha256
    assert older_backup.read_bytes() == older_bytes
    assert not list(work_dir.rglob("*.candidate"))
    assert not list(work_dir.rglob("*journal*"))


def test_backup_name_published_atomically(tmp_path, monkeypatch):
    api = _backup_api()
    work_dir, final_output, _dataset_value, previous_bytes, _previous_sha256 = _backup_case(
        tmp_path
    )
    transaction_id = "collect-task-3-atomic-name"
    expected_backup = _expected_backup_path(work_dir, final_output, transaction_id)
    observations = []
    real_replace = os.replace

    def recording_replace(source, destination, *args, **kwargs):
        source = Path(source)
        destination = Path(destination)
        observations.append(
            {
                "source": source,
                "destination": destination,
                "source_exists": source.exists(),
                "destination_absent": not destination.exists(),
                "final_exists": final_output.exists(),
            }
        )
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", recording_replace)

    with CollectFileLock("md", final_output, transaction_id=transaction_id):
        artifacts = api.prepare_backup(
            work_dir=work_dir,
            stage="md",
            final_output=final_output,
            transaction_id=transaction_id,
        )

    matching = [
        observation
        for observation in observations
        if observation["destination"] == expected_backup
    ]
    assert matching
    observation = matching[0]
    assert observation["source_exists"]
    assert observation["destination_absent"]
    assert observation["final_exists"]
    assert artifacts.backup_path == expected_backup
    assert expected_backup.exists()
    assert not observation["source"].exists()
    assert final_output.exists()
    assert final_output.read_bytes() == previous_bytes


def test_older_backups_are_not_deleted(tmp_path):
    api = _backup_api()
    work_dir, final_output, _dataset_value, previous_bytes, _previous_sha256 = _backup_case(
        tmp_path
    )
    older_directory = work_dir / "backups" / "collect"
    older_directory.mkdir(parents=True, exist_ok=True)
    older_one = older_directory / "MD_data.older-one.extxyz"
    older_two = older_directory / "MD_data.older-two.extxyz"
    older_one.write_bytes(b"older one")
    older_two.write_bytes(b"older two")
    transaction_id = "collect-task-3-keep-old"

    with CollectFileLock("md", final_output, transaction_id=transaction_id):
        artifacts = api.prepare_backup(
            work_dir=work_dir,
            stage="md",
            final_output=final_output,
            transaction_id=transaction_id,
        )

    assert final_output.exists()
    assert final_output.read_bytes() == previous_bytes
    assert older_one.read_bytes() == b"older one"
    assert older_two.read_bytes() == b"older two"
    assert artifacts.backup_path is not None
    assert artifacts.backup_path.exists()


def test_first_publish_has_null_previous_and_backup_fields(tmp_path):
    api = _backup_api()
    work_dir, final_output, _dataset_value, _previous_bytes, _previous_sha256 = _backup_case(
        tmp_path, existing=False
    )
    transaction_id = "collect-task-3-first-publish"

    with CollectFileLock("md", final_output, transaction_id=transaction_id):
        artifacts = api.prepare_backup(
            work_dir=work_dir,
            stage="md",
            final_output=final_output,
            transaction_id=transaction_id,
        )

    assert isinstance(artifacts, api.BackupArtifacts)
    assert artifacts.previous_output_sha256 is None
    assert artifacts.previous_output_frames is None
    assert artifacts.backup_path is None
    assert artifacts.backup_sha256 is None
    assert not final_output.exists()
    assert not (work_dir / "backups" / "collect").exists()
    assert not list(work_dir.rglob("*.candidate"))
    assert not list(work_dir.rglob("*journal*"))


def test_publish_sequence_matches_authoritative_order(tmp_path, monkeypatch):
    api = _publication_api()
    request = _request(tmp_path)
    _write_previous_output(request)
    journal_path = _journal_path(request.final_output)
    events = []
    state = {"data_replaced": False, "manifest_replaced": False}

    real_sha256_file = atomic_io.sha256_file

    def recording_sha256_file(path):
        path = Path(path)
        if (
            path.parent == request.final_output.parent
            and path.name.startswith(f".{request.final_output.name}.")
            and "collect-journal" not in path.name
        ):
            events.append("data_candidate_hash")
        elif (
            path.parent == request.target.path.parent
            and path.name.startswith(f".{request.target.path.name}.")
        ):
            events.append("manifest_candidate_hash")
        elif path == request.final_output and state["data_replaced"]:
            events.append("data_final_hash")
        elif path == request.target.path and state["manifest_replaced"]:
            events.append("manifest_final_hash")
        return real_sha256_file(path)

    real_replace = os.replace

    def recording_replace(source, destination, *args, **kwargs):
        source = Path(source)
        destination = Path(destination)
        if destination == journal_path:
            journal = yaml.safe_load(source.read_text(encoding="utf-8"))
            events.append(f"journal_{journal['state']}")
        elif destination == request.final_output:
            events.append("data_replace")
        elif destination == request.target.path:
            events.append("manifest_replace")
        elif destination.parent == request.work_dir / "backups" / "collect":
            events.append("backup_publish")

        result = real_replace(source, destination, *args, **kwargs)
        if destination == request.final_output:
            state["data_replaced"] = True
        elif destination == request.target.path:
            state["manifest_replaced"] = True
        return result

    real_unlink = Path.unlink

    def recording_unlink(path, *args, **kwargs):
        if Path(path) == journal_path:
            events.append("journal_unlink")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(atomic_io, "sha256_file", recording_sha256_file)
    monkeypatch.setattr(api.os, "replace", recording_replace)
    monkeypatch.setattr(Path, "unlink", recording_unlink)

    _publish_request(request)

    expected = [
        "data_candidate_hash",
        "backup_publish",
        "manifest_candidate_hash",
        "journal_pending",
        "data_replace",
        "data_final_hash",
        "manifest_replace",
        "manifest_final_hash",
        "journal_committed",
        "journal_unlink",
    ]
    positions = [events.index(name) for name in expected]
    assert positions == sorted(positions), events


def test_pending_journal_precedes_final_output_replace(tmp_path, monkeypatch):
    api = _publication_api()
    request = _request(tmp_path)
    journal_path = _journal_path(request.final_output)
    observations = []
    real_replace = os.replace

    def recording_replace(source, destination, *args, **kwargs):
        destination = Path(destination)
        if destination == request.final_output:
            assert journal_path.is_file()
            journal = yaml.safe_load(journal_path.read_text(encoding="utf-8"))
            observations.append(journal["state"])
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(api.os, "replace", recording_replace)

    _publish_request(request)

    assert observations == ["pending"]


def test_final_data_hash_verified_before_manifest_replace(tmp_path, monkeypatch):
    api = _publication_api()
    request = _request(tmp_path)
    events = []
    state = {"data_replaced": False}
    real_sha256_file = atomic_io.sha256_file

    def recording_sha256_file(path):
        path = Path(path)
        if path == request.final_output and state["data_replaced"]:
            events.append("data_final_hash")
        return real_sha256_file(path)

    real_replace = os.replace

    def recording_replace(source, destination, *args, **kwargs):
        destination = Path(destination)
        result = real_replace(source, destination, *args, **kwargs)
        if destination == request.final_output:
            state["data_replaced"] = True
            events.append("data_replace")
        elif destination == request.target.path:
            events.append("manifest_replace")
        return result

    monkeypatch.setattr(atomic_io, "sha256_file", recording_sha256_file)
    monkeypatch.setattr(api.os, "replace", recording_replace)

    _publish_request(request)

    assert events.index("data_replace") < events.index("data_final_hash")
    assert events.index("data_final_hash") < events.index("manifest_replace")


def test_manifest_hash_and_internal_transaction_verified(tmp_path, monkeypatch):
    api = _publication_api()
    request = _request(tmp_path)
    journal_path = _journal_path(request.final_output)
    expected_manifest_hash = {"value": None}
    manifest_replaced = {"value": False}
    real_replace = os.replace
    real_sha256_file = atomic_io.sha256_file

    def tampering_replace(source, destination, *args, **kwargs):
        source = Path(source)
        destination = Path(destination)
        if destination == request.target.path:
            expected_manifest_hash["value"] = real_sha256_file(source)
        result = real_replace(source, destination, *args, **kwargs)
        if destination == request.target.path:
            data = yaml.safe_load(destination.read_text(encoding="utf-8"))
            data["collect"]["transaction_id"] = "tampered-transaction"
            destination.write_text(
                yaml.safe_dump(data, sort_keys=False),
                encoding="utf-8",
                newline="",
            )
            manifest_replaced["value"] = True
        return result

    def preserve_expected_manifest_hash(path):
        path = Path(path)
        if path == request.target.path and manifest_replaced["value"]:
            return expected_manifest_hash["value"]
        return real_sha256_file(path)

    monkeypatch.setattr(api.os, "replace", tampering_replace)
    monkeypatch.setattr(atomic_io, "sha256_file", preserve_expected_manifest_hash)

    with pytest.raises(api.PublicationError, match="transaction"):
        _publish_request(request)

    journal = yaml.safe_load(journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "pending"


def test_journal_marked_committed_before_unlink(tmp_path, monkeypatch):
    _publication_api()
    request = _request(tmp_path)
    journal_path = _journal_path(request.final_output)
    observed_states = []
    real_unlink = Path.unlink

    def recording_unlink(path, *args, **kwargs):
        path = Path(path)
        if path == journal_path:
            journal = yaml.safe_load(path.read_text(encoding="utf-8"))
            observed_states.append(journal["state"])
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", recording_unlink)

    _publish_request(request)

    assert observed_states == ["committed"]
    assert not journal_path.exists()


def test_lock_held_from_recovery_through_journal_cleanup(tmp_path, monkeypatch):
    api = _publication_api()
    request = _request(tmp_path)
    journal_path = _journal_path(request.final_output)
    observations = []
    real_recover = api._recover_existing_journal

    def recording_recover(session):
        _assert_same_output_lock_is_held(request)
        observations.append("recovery")
        return real_recover(session)

    real_unlink = Path.unlink

    def recording_unlink(path, *args, **kwargs):
        path = Path(path)
        if path == journal_path:
            _assert_same_output_lock_is_held(request)
            observations.append("cleanup")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(api, "_recover_existing_journal", recording_recover)
    monkeypatch.setattr(Path, "unlink", recording_unlink)

    with api.PublicationSession(
        work_dir=request.work_dir,
        stage=request.stage,
        final_output=request.final_output,
        target=request.target,
    ) as session:
        session.publish(_request_for_session(request, session))

    with CollectFileLock(
        request.stage,
        request.final_output,
        transaction_id="after-publish",
    ):
        observations.append("released")

    assert observations == ["recovery", "cleanup", "released"]


def test_normal_publish_returns_matching_paths_and_hashes(tmp_path):
    api = _publication_api()
    request = _request(tmp_path)

    returned_api, result, journal_path = _publish_request(request)

    assert returned_api is api
    assert isinstance(result, api.PublicationResult)
    assert result.transaction_id == request.transaction_id
    assert result.final_output == request.final_output
    assert result.data_sha256 == atomic_io.sha256_file(request.final_output)
    assert result.result_manifest_target_kind == request.target.kind
    assert result.result_manifest_path == request.target.path
    assert result.manifest_sha256 == atomic_io.sha256_file(request.target.path)
    assert result.previous_output_sha256 is None
    assert result.backup_path is None
    assert result.backup_sha256 is None
    assert not journal_path.exists()
    assert not list(request.work_dir.rglob("*.candidate"))


def test_pending_journal_records_result_manifest_target_kind_and_path(
    tmp_path, monkeypatch
):
    api = _publication_api()
    request = _request(tmp_path)
    journal_path = _journal_path(request.final_output)
    pending_records = []
    real_replace = os.replace

    def recording_replace(source, destination, *args, **kwargs):
        source = Path(source)
        destination = Path(destination)
        if destination == journal_path:
            record = yaml.safe_load(source.read_text(encoding="utf-8"))
            if record["state"] == "pending":
                pending_records.append(record)
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(api.os, "replace", recording_replace)

    _publish_request(request)

    assert len(pending_records) == 1
    pending = pending_records[0]
    assert pending == {
        "transaction_id": request.transaction_id,
        "state": "pending",
        "stage": "md",
        "final_output": "MD_data.extxyz",
        "data_candidate_path": pending["data_candidate_path"],
        "data_candidate_sha256": pending["data_candidate_sha256"],
        "result_manifest_target_kind": "current-stage",
        "result_manifest_path": "md/manifest.yaml",
        "manifest_candidate_path": pending["manifest_candidate_path"],
        "manifest_candidate_sha256": pending["manifest_candidate_sha256"],
        "previous_output_sha256": None,
        "previous_manifest_sha256": pending["previous_manifest_sha256"],
        "backup_path": None,
        "backup_sha256": None,
    }
    assert pending["data_candidate_path"].startswith(".MD_data.extxyz.")
    assert pending["manifest_candidate_path"].startswith("md/.manifest.yaml.")
    assert len(pending["data_candidate_sha256"]) == 64
    assert len(pending["manifest_candidate_sha256"]) == 64
    assert len(pending["previous_manifest_sha256"]) == 64


def test_normal_publish_supports_compatibility_result_manifest(tmp_path):
    api = _publication_api()
    request = _request(tmp_path, target_kind="compatibility")
    legacy_manifest = manifest_path(request.work_dir, "md")
    legacy_manifest.parent.mkdir(parents=True, exist_ok=True)
    legacy_bytes = b"stage: md\ndirectories:\n  - run-a\nlegacy_key: keep-me\n"
    legacy_manifest.write_bytes(legacy_bytes)

    _returned_api, result, journal_path = _publish_request(request)

    assert result.result_manifest_target_kind == api.ResultManifestTargetKind.MD_COMPATIBILITY
    assert result.result_manifest_path == request.work_dir / "MD_data.collect.yaml"
    assert result.data_sha256 == atomic_io.sha256_file(request.final_output)
    assert result.manifest_sha256 == atomic_io.sha256_file(request.target.path)
    data = yaml.safe_load(request.target.path.read_text(encoding="utf-8"))
    assert data["collect"]["transaction_id"] == request.transaction_id
    assert data["collect"]["output_sha256"] == result.data_sha256
    assert legacy_manifest.read_bytes() == legacy_bytes
    assert not journal_path.exists()
