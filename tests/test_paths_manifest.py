from pathlib import Path

import pytest

from dpmoire_lite.manifest import Manifest, read_manifest, write_manifest
from dpmoire_lite.paths import backup_existing_directory, manifest_path, relative_to_workdir, stage_dir


def test_stage_dir_layout(tmp_path):
    work = tmp_path / "work"
    assert stage_dir(work, "rlx") == work / "rlx"
    assert stage_dir(work, "md") == work / "md"
    assert manifest_path(work, "validation") == work / "validation" / "manifest.yaml"


def test_relative_to_workdir_uses_posix_paths(tmp_path):
    work = tmp_path / "work"
    target = work / "rlx" / "0_0"
    assert relative_to_workdir(work, target) == "rlx/0_0"


def test_backup_existing_directory_moves_single_target(tmp_path):
    work = tmp_path / "work"
    target = work / "rlx" / "0_0"
    target.mkdir(parents=True)
    (target / "OUTCAR").write_text("result", encoding="utf-8")
    backup = backup_existing_directory(work, "rlx", target, timestamp="20260526-211500")
    assert backup == work / "backups" / "rlx" / "0_0_20260526-211500"
    assert (backup / "OUTCAR").read_text(encoding="utf-8") == "result"
    assert not target.exists()


def test_backup_existing_directory_rejects_outside_workdir_target(tmp_path):
    work = tmp_path / "work"
    target = tmp_path / "outside" / "0_0"
    target.mkdir(parents=True)

    with pytest.raises(ValueError, match="inside work_dir"):
        backup_existing_directory(work, "rlx", target, timestamp="20260526-211500")

    assert target.exists()


@pytest.mark.parametrize("stage", ["../evil", "bad"])
def test_backup_existing_directory_rejects_invalid_stage(tmp_path, stage):
    work = tmp_path / "work"
    target = work / "rlx" / "0_0"
    target.mkdir(parents=True)

    with pytest.raises(ValueError, match="Unknown stage"):
        backup_existing_directory(work, stage, target, timestamp="20260526-211500")

    assert target.exists()


def test_backup_existing_directory_raises_on_existing_backup_collision(tmp_path):
    work = tmp_path / "work"
    target = work / "rlx" / "0_0"
    backup = work / "backups" / "rlx" / "0_0_20260526-211500"
    target.mkdir(parents=True)
    backup.mkdir(parents=True)

    with pytest.raises(FileExistsError):
        backup_existing_directory(work, "rlx", target, timestamp="20260526-211500")

    assert target.exists()


def test_backup_existing_directory_rejects_file_target(tmp_path):
    work = tmp_path / "work"
    target = work / "rlx" / "0_0"
    target.parent.mkdir(parents=True)
    target.write_text("not a directory", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        backup_existing_directory(work, "rlx", target, timestamp="20260526-211500")

    assert target.exists()


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
