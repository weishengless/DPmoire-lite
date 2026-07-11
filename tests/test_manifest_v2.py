from dataclasses import asdict

import pytest
import yaml

from dpmoire_lite.manifest import Manifest, read_manifest, write_manifest
from dpmoire_lite.paths import manifest_path


def _write_yaml(work_dir, stage, data):
    path = manifest_path(work_dir, stage)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_read_manifest_reports_missing_separately(tmp_path):
    work_dir = tmp_path / "work"

    result = read_manifest(work_dir, "rlx")

    assert result.kind == "missing"
    assert result.manifest is None
    assert result.raw_data is None


def test_read_manifest_detects_legacy_without_mutating_it(tmp_path):
    work_dir = tmp_path / "work"
    legacy = {
        "stage": "rlx",
        "generated_at": "2026-07-11T12:00:00",
        "directories": ["rlx/0_0"],
        "stackings": [[0, 0]],
    }
    path = _write_yaml(work_dir, "rlx", legacy)
    original_bytes = path.read_bytes()

    result = read_manifest(work_dir, "rlx")

    assert result.kind == "legacy"
    assert result.manifest is None
    assert result.raw_data == legacy
    assert path.read_bytes() == original_bytes


def test_read_manifest_rejects_invalid_v2_schema(tmp_path):
    work_dir = tmp_path / "work"
    _write_yaml(
        work_dir,
        "rlx",
        {"schema_version": 2, "stage": "rlx", "directories": "rlx/0_0"},
    )

    with pytest.raises(ValueError, match="generated_at|directories"):
        read_manifest(work_dir, "rlx")


def test_manifest_v2_round_trip_preserves_extension_sections(tmp_path):
    work_dir = tmp_path / "work"
    manifest = Manifest(
        schema_version=2,
        stage="rlx",
        generated_at="2026-07-11T12:00:00",
        directories=["rlx/0_0"],
        stackings=[[0, 0]],
        structure_provenance={"sc_rlx": False, "input_sha256": {"top": "abc"}},
        grid_shift_anchors={"0_0": [0.0, 0.0]},
        mlff_seed={"algorithm": "mlab-seed-v1", "sha256": "def"},
        partial=[{"path": "rlx/0_0", "reason": "truncated tail"}],
        collect={
            "status": "degraded",
            "transaction_id": "collect-test-1",
            "output_sha256": "123",
            "previous_sha256": "456",
            "backup": "backups/collect/rlx_data.collect-test-1.extxyz",
        },
    )

    write_manifest(work_dir, manifest)
    result = read_manifest(work_dir, "rlx")

    assert result.kind == "current"
    assert result.manifest is not None
    assert asdict(result.manifest) == asdict(manifest)


def test_read_manifest_never_creates_a_file(tmp_path):
    work_dir = tmp_path / "work"
    path = manifest_path(work_dir, "md")

    result = read_manifest(work_dir, "md")

    assert result.kind == "missing"
    assert not path.exists()
