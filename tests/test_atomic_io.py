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
