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


def test_manifest_v2_rejects_stage_path_escape(tmp_path):
    work_dir = tmp_path / "work"

    with pytest.raises(ValueError, match="directories|work_dir"):
        write_manifest(
            work_dir,
            Manifest(
                stage="rlx",
                generated_at="2026-07-11T12:00:00",
                directories=["rlx/../../outside"],
            ),
        )


def test_manifest_v2_rejects_absolute_directory_outside_workdir(tmp_path):
    work_dir = tmp_path / "work"
    outside = (tmp_path / "outside" / "0_0").resolve()

    with pytest.raises(ValueError, match="directories|work_dir"):
        write_manifest(
            work_dir,
            Manifest(
                stage="rlx",
                generated_at="2026-07-11T12:00:00",
                directories=[str(outside)],
            ),
        )


def test_manifest_directory_accepts_normalized_relative_path(tmp_path):
    work_dir = tmp_path / "work"
    write_manifest(
        work_dir,
        Manifest(
            stage="rlx",
            generated_at="2026-07-11T12:00:00",
            directories=[r"rlx\0_0"],
        ),
    )

    raw = yaml.safe_load(manifest_path(work_dir, "rlx").read_text(encoding="utf-8"))
    result = read_manifest(work_dir, "rlx")

    assert raw["directories"] == ["rlx/0_0"]
    assert result.manifest is not None
    assert result.manifest.directories == ["rlx/0_0"]


def test_manifest_directory_rejects_parent_traversal(tmp_path):
    work_dir = tmp_path / "work"

    with pytest.raises(ValueError, match="directories"):
        write_manifest(
            work_dir,
            Manifest(
                stage="rlx",
                generated_at="2026-07-11T12:00:00",
                directories=["rlx/../rlx/0_0"],
            ),
        )


def test_manifest_directory_rejects_symlink_escape_when_resolvable(tmp_path):
    work_dir = tmp_path / "work"
    outside = tmp_path / "outside"
    work_dir.mkdir()
    outside.mkdir()
    link = work_dir / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(ValueError, match="directories|work_dir"):
        write_manifest(
            work_dir,
            Manifest(
                stage="rlx",
                generated_at="2026-07-11T12:00:00",
                directories=["link/0_0"],
            ),
        )


def test_manifest_output_path_must_match_stage_contract(tmp_path):
    with pytest.raises(ValueError, match="output|stage"):
        write_manifest(
            tmp_path / "work",
            Manifest(
                stage="md",
                generated_at="2026-07-11T12:00:00",
                collect={"output": "rlx_data.extxyz"},
            ),
        )


def test_manifest_stage_must_match_manifest_location(tmp_path):
    work_dir = tmp_path / "work"
    _write_yaml(
        work_dir,
        "rlx",
        {
            "schema_version": 2,
            "stage": "md",
            "generated_at": "2026-07-11T12:00:00",
            "directories": [],
            "backups": [],
            "jobs": [],
            "collect": {},
            "skipped": [],
            "failed": [],
            "stackings": [],
            "angles": [],
            "config_summary": {},
            "structure_provenance": {},
            "grid_shift_anchors": {},
            "mlff_seed": {},
            "partial": [],
        },
    )

    with pytest.raises(ValueError, match="stage|manifest"):
        read_manifest(work_dir, "rlx")
