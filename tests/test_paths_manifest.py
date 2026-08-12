from dpmoire_lite.manifest import Manifest, read_manifest, write_manifest
from dpmoire_lite.paths import manifest_path, relative_to_workdir, stage_dir


def test_stage_dir_layout(tmp_path):
    work = tmp_path / "work"
    assert stage_dir(work, "rlx") == work / "rlx"
    assert stage_dir(work, "md") == work / "md"
    assert manifest_path(work, "validation") == work / "validation" / "manifest.yaml"


def test_relative_to_workdir_uses_posix_paths(tmp_path):
    work = tmp_path / "work"
    target = work / "rlx" / "0_0"
    assert relative_to_workdir(work, target) == "rlx/0_0"


def test_manifest_round_trip(tmp_path):
    work = tmp_path / "work"
    manifest = Manifest(
        stage="rlx",
        generated_at="2026-05-26T21:15:00",
        config_summary={"stage": 0},
        directories=["rlx/0_0"],
        backups=["backups/rlx/0_0_20260526-211500"],
        jobs=[{"id": "123", "path": "rlx/0_0", "status": "SUBMITTED"}],
        collect={"frames": 8},
        skipped=[{"path": "rlx/0_1", "reason": "missing OUTCAR"}],
        failed=[],
        stackings=[[0, 0]],
        angles=[],
    )
    write_manifest(work, manifest)
    loaded = read_manifest(work, "rlx")
    assert loaded.stage == "rlx"
    assert loaded.directories == ["rlx/0_0"]
    assert loaded.stackings == [[0, 0]]
