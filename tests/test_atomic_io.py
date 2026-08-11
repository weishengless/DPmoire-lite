import hashlib
import os

import pytest

import dpmoire_lite.atomic_io as atomic_io


def test_atomic_text_publish_replaces_only_after_candidate_fsync(tmp_path, monkeypatch):
    destination = tmp_path / "manifest.yaml"
    events = []
    real_replace = os.replace

    def record_fsync(handle):
        events.append(("fsync", handle.name))

    def record_replace(source, target):
        events.append(("replace", str(source), str(target)))
        real_replace(source, target)

    monkeypatch.setattr(atomic_io, "_fsync_file", record_fsync)
    monkeypatch.setattr(atomic_io.os, "replace", record_replace)

    atomic_io.atomic_text_publish(destination, "schema_version: 2\n")

    assert [event[0] for event in events] == ["fsync", "replace"]
    assert destination.read_text(encoding="utf-8") == "schema_version: 2\n"


def test_atomic_publish_failure_preserves_previous_bytes(tmp_path, monkeypatch):
    destination = tmp_path / "manifest.yaml"
    previous = b"previous manifest bytes\n"
    destination.write_bytes(previous)

    def fail_fsync(_handle):
        raise OSError("candidate fsync failed")

    monkeypatch.setattr(atomic_io, "_fsync_file", fail_fsync)

    with pytest.raises(OSError, match="candidate fsync failed"):
        atomic_io.atomic_text_publish(destination, "new manifest\n")

    assert destination.read_bytes() == previous


def test_atomic_publish_uses_same_directory_candidate(tmp_path, monkeypatch):
    destination = tmp_path / "nested" / "manifest.yaml"
    destination.parent.mkdir()
    observed = {}
    real_replace = os.replace

    def record_replace(source, target):
        observed["source"] = source
        observed["target"] = target
        real_replace(source, target)

    monkeypatch.setattr(atomic_io.os, "replace", record_replace)

    atomic_io.atomic_text_publish(destination, "manifest\n")

    assert observed["source"].parent == destination.parent
    assert observed["target"] == destination


def test_sha256_file_matches_known_bytes(tmp_path):
    payload = b"DPmoire-lite atomic bytes\x00\xff\n"
    path = tmp_path / "payload.bin"
    path.write_bytes(payload)

    assert atomic_io.sha256_file(path) == hashlib.sha256(payload).hexdigest()


def test_atomic_publish_removes_its_candidate_on_pre_replace_failure(
    tmp_path, monkeypatch
):
    destination = tmp_path / "manifest.yaml"

    def fail_fsync(_handle):
        raise OSError("stop before replace")

    monkeypatch.setattr(atomic_io, "_fsync_file", fail_fsync)

    with pytest.raises(OSError, match="stop before replace"):
        atomic_io.atomic_text_publish(destination, "manifest\n")

    assert list(tmp_path.glob(".manifest.yaml.*.candidate")) == []
    assert not destination.exists()


def test_atomic_publish_does_not_swallow_replace_failure(tmp_path, monkeypatch):
    destination = tmp_path / "manifest.yaml"
    destination.write_bytes(b"previous\n")

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr(atomic_io.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_io.atomic_text_publish(destination, "new\n")

    assert destination.read_bytes() == b"previous\n"


def test_atomic_directory_publish_installs_complete_candidate(tmp_path):
    candidate = tmp_path / ".workspace-candidate"
    destination = tmp_path / "init_mlff"
    (candidate / "bottom").mkdir(parents=True)
    (candidate / "bottom" / "POSCAR").write_text("prepared\n", encoding="utf-8")

    atomic_io.atomic_directory_publish_no_replace(candidate, destination)

    assert not candidate.exists()
    assert (destination / "bottom" / "POSCAR").read_text(encoding="utf-8") == (
        "prepared\n"
    )


def test_atomic_directory_publish_refuses_existing_empty_directory(tmp_path):
    candidate = tmp_path / ".workspace-candidate"
    destination = tmp_path / "init_mlff"
    candidate.mkdir()
    (candidate / "manifest.yaml").write_text("candidate\n", encoding="utf-8")
    destination.mkdir()

    with pytest.raises(FileExistsError):
        atomic_io.atomic_directory_publish_no_replace(candidate, destination)

    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert (candidate / "manifest.yaml").read_text(encoding="utf-8") == "candidate\n"


def test_atomic_directory_publish_refuses_dangling_symlink(tmp_path):
    candidate = tmp_path / ".workspace-candidate"
    destination = tmp_path / "init_mlff"
    candidate.mkdir()
    (candidate / "manifest.yaml").write_text("candidate\n", encoding="utf-8")
    try:
        destination.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises(FileExistsError):
        atomic_io.atomic_directory_publish_no_replace(candidate, destination)

    assert destination.is_symlink()
    assert (candidate / "manifest.yaml").read_text(encoding="utf-8") == "candidate\n"


def test_atomic_file_publish_installs_candidate_without_replacement(tmp_path):
    candidate = tmp_path / ".ML_AB.candidate"
    destination = tmp_path / "ML_AB"
    candidate.write_bytes(b"verified-seed\n")

    atomic_io.atomic_file_publish_no_replace(candidate, destination)

    assert not candidate.exists()
    assert destination.read_bytes() == b"verified-seed\n"


def test_atomic_file_publish_refuses_existing_destination(tmp_path):
    candidate = tmp_path / ".ML_AB.candidate"
    destination = tmp_path / "ML_AB"
    candidate.write_bytes(b"candidate\n")
    destination.write_bytes(b"existing\n")

    with pytest.raises(FileExistsError):
        atomic_io.atomic_file_publish_no_replace(candidate, destination)

    assert candidate.read_bytes() == b"candidate\n"
    assert destination.read_bytes() == b"existing\n"
