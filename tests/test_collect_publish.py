from __future__ import annotations

import importlib
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
