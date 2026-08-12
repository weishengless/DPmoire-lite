import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest


def _lock_api():
    try:
        module = importlib.import_module("dpmoire_lite.file_lock")
    except ModuleNotFoundError as exc:
        if exc.name == "dpmoire_lite.file_lock":
            pytest.fail("CollectFileLock is not implemented")
        raise

    collect_file_lock = getattr(module, "CollectFileLock", None)
    collect_lock_error = getattr(module, "CollectLockError", None)
    assert collect_file_lock is not None, "CollectFileLock is not implemented"
    assert collect_lock_error is not None, "CollectLockError is not implemented"
    return collect_file_lock, collect_lock_error


def _child_environment():
    src_directory = Path(__file__).resolve().parents[1] / "src"
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(src_directory), existing) if part
    )
    return environment


def _run_child(source, *arguments):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(source),
            *(str(argument) for argument in arguments),
        ],
        capture_output=True,
        check=False,
        env=_child_environment(),
        text=True,
        timeout=10,
    )


def _read_diagnostics(lock_path):
    with lock_path.open("rb") as handle:
        handle.seek(1)
        payload = handle.read()

    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError(
            "lock diagnostics after reserved byte are not UTF-8 JSON"
        ) from exc

    if not isinstance(value, dict):
        raise AssertionError("lock diagnostics are not a JSON object")
    return value


def test_collect_lock_allows_one_holder(tmp_path):
    collect_file_lock, _ = _lock_api()
    output = tmp_path / "MD_data.extxyz"
    lock = collect_file_lock("md", output, transaction_id="transaction-one")

    with lock as held:
        assert held is lock
        assert isinstance(held.lock_path, Path)
        assert held.lock_path.parent == output.parent
        assert held.lock_path.exists()
        diagnostics = _read_diagnostics(held.lock_path)

    assert diagnostics["stage"] == "md"
    assert diagnostics["transaction_id"] == "transaction-one"
    assert isinstance(diagnostics["pid"], int)
    assert diagnostics["hostname"]
    assert diagnostics["acquired_at"]


def test_collect_lock_rejects_a_hard_link_before_touching_its_target(tmp_path):
    collect_file_lock, collect_lock_error = _lock_api()
    output = tmp_path / "MD_data.extxyz"
    lock = collect_file_lock("md", output, transaction_id="transaction-one")
    external_target = tmp_path / "outside-lock-target"
    original = b"outside content must stay unchanged"
    external_target.write_bytes(original)
    os.link(external_target, lock.lock_path)

    with pytest.raises(collect_lock_error, match="lock invariant"):
        with lock:
            pass

    assert external_target.read_bytes() == original


def test_collect_lock_rejects_a_symlink_before_touching_its_target(tmp_path):
    collect_file_lock, collect_lock_error = _lock_api()
    output = tmp_path / "MD_data.extxyz"
    lock = collect_file_lock("md", output, transaction_id="transaction-one")
    external_target = tmp_path / "outside-symlink-target"
    original = b"outside content must stay unchanged"
    external_target.write_bytes(original)
    try:
        lock.lock_path.symlink_to(external_target)
    except OSError as exc:
        pytest.skip(f"file symlinks are unavailable on this platform: {exc}")

    with pytest.raises(collect_lock_error, match="lock invariant"):
        with lock:
            pass

    assert external_target.read_bytes() == original


def test_collect_lock_rejects_a_non_regular_lock_path(tmp_path):
    collect_file_lock, collect_lock_error = _lock_api()
    output = tmp_path / "MD_data.extxyz"
    lock = collect_file_lock("md", output, transaction_id="transaction-one")
    lock.lock_path.mkdir()

    with pytest.raises(collect_lock_error, match="lock invariant"):
        with lock:
            pass

    assert lock.lock_path.is_dir()


def test_collect_lock_rejects_windows_reparse_metadata_before_diagnostics(
    monkeypatch,
    tmp_path,
):
    collect_file_lock, collect_lock_error = _lock_api()
    output = tmp_path / "MD_data.extxyz"
    lock = collect_file_lock("md", output, transaction_id="transaction-one")
    original = b"existing lock content"
    lock.lock_path.write_bytes(original)
    real_lstat = Path.lstat

    def lstat_with_reparse_metadata(path):
        result = real_lstat(path)
        if path == lock.lock_path:
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_nlink=result.st_nlink,
                st_reparse_tag=0xA000000C,
            )
        return result

    monkeypatch.setattr(Path, "lstat", lstat_with_reparse_metadata)

    with pytest.raises(collect_lock_error, match="lock invariant"):
        with lock:
            pass

    assert lock.lock_path.read_bytes() == original


def test_collect_lock_rejects_path_identity_change_after_open(
    monkeypatch,
    tmp_path,
):
    module = importlib.import_module("dpmoire_lite.file_lock")
    collect_file_lock, collect_lock_error = _lock_api()
    output = tmp_path / "MD_data.extxyz"
    lock = collect_file_lock("md", output, transaction_id="transaction-one")
    original = b"existing lock content"
    lock.lock_path.write_bytes(original)
    replacement = tmp_path / "replacement-lock"
    replacement_content = b"replacement lock content"
    replacement.write_bytes(replacement_content)
    real_open = module.os.open
    real_lstat = Path.lstat
    opened = False

    def open_then_expose_replacement(path, flags, mode=0o777):
        nonlocal opened
        descriptor = real_open(path, flags, mode)
        opened = True
        return descriptor

    def lstat_with_replacement_identity(path):
        if path == lock.lock_path and opened:
            return real_lstat(replacement)
        return real_lstat(path)

    monkeypatch.setattr(module.os, "open", open_then_expose_replacement)
    monkeypatch.setattr(Path, "lstat", lstat_with_replacement_identity)

    with pytest.raises(collect_lock_error, match="lock invariant"):
        with lock:
            pass

    assert lock.lock_path.read_bytes() == original
    assert replacement.read_bytes() == replacement_content


def test_collect_lock_rechecks_before_initializing_an_empty_lock(
    monkeypatch,
    tmp_path,
):
    module = importlib.import_module("dpmoire_lite.file_lock")
    collect_file_lock, collect_lock_error = _lock_api()
    output = tmp_path / "MD_data.extxyz"
    lock = collect_file_lock("md", output, transaction_id="transaction-one")
    external_alias = tmp_path / "outside-lock-alias"
    real_fdopen = module.os.fdopen

    def fdopen_then_add_link(descriptor, *args, **kwargs):
        handle = real_fdopen(descriptor, *args, **kwargs)
        os.link(lock.lock_path, external_alias)
        return handle

    monkeypatch.setattr(module.os, "fdopen", fdopen_then_add_link)

    with pytest.raises(collect_lock_error, match="lock invariant"):
        with lock:
            pass

    assert external_alias.read_bytes() == b""


def test_second_process_cannot_acquire_same_stage_output_lock(tmp_path):
    output = tmp_path / "MD_data.extxyz"
    alias_parent = output.parent / "alias-parent"
    alias_parent.mkdir()
    alias = alias_parent / ".." / output.name
    assert os.fspath(alias) != os.fspath(output)
    assert alias.resolve(strict=False) == output.resolve(strict=False)

    collect_file_lock, collect_lock_error = _lock_api()
    child = """
        import sys
        from pathlib import Path
        from dpmoire_lite.file_lock import CollectFileLock, CollectLockError

        try:
            with CollectFileLock(sys.argv[1], Path(sys.argv[2]), transaction_id="child"):
                raise SystemExit("child unexpectedly acquired the lock")
        except CollectLockError:
            raise SystemExit(0)
    """

    with collect_file_lock("md", output, transaction_id="parent"):
        result = _run_child(child, "md", alias)

    assert result.returncode == 0, result.stderr or result.stdout
    assert collect_lock_error.__name__ == "CollectLockError"


def test_different_stage_outputs_use_independent_locks(tmp_path):
    collect_file_lock, _ = _lock_api()
    first_output = tmp_path / "first.extxyz"
    second_output = tmp_path / "second.extxyz"

    with collect_file_lock("md", first_output, transaction_id="first") as first:
        with collect_file_lock("md", second_output, transaction_id="second") as second:
            assert first.lock_path != second.lock_path
        with collect_file_lock("rlx", first_output, transaction_id="third") as third:
            assert third.lock_path != first.lock_path


def test_lock_released_after_normal_exit(tmp_path):
    collect_file_lock, _ = _lock_api()
    output = tmp_path / "MD_data.extxyz"
    child = """
        import sys
        from pathlib import Path
        from dpmoire_lite.file_lock import CollectFileLock

        with CollectFileLock(sys.argv[1], Path(sys.argv[2]), transaction_id="child"):
            pass
    """

    with collect_file_lock("md", output, transaction_id="parent"):
        pass

    result = _run_child(child, "md", output)
    assert result.returncode == 0, result.stderr or result.stdout


def test_lock_released_after_exception(tmp_path):
    collect_file_lock, _ = _lock_api()
    output = tmp_path / "MD_data.extxyz"
    child = """
        import sys
        from pathlib import Path
        from dpmoire_lite.file_lock import CollectFileLock

        with CollectFileLock(sys.argv[1], Path(sys.argv[2]), transaction_id="child"):
            pass
    """

    with pytest.raises(RuntimeError, match="synthetic failure"):
        with collect_file_lock("md", output, transaction_id="parent"):
            raise RuntimeError("synthetic failure")

    result = _run_child(child, "md", output)
    assert result.returncode == 0, result.stderr or result.stdout


def test_stale_metadata_does_not_override_os_lock_state(tmp_path):
    collect_file_lock, _ = _lock_api()
    output = tmp_path / "MD_data.extxyz"
    lock = collect_file_lock("md", output, transaction_id="new-transaction")
    lock.lock_path.write_bytes(b'{"transaction_id":"stale-transaction"}')
    reserved_byte = lock.lock_path.read_bytes()[:1]
    assert reserved_byte == b"{"

    with lock:
        diagnostics = _read_diagnostics(lock.lock_path)
        assert diagnostics["stage"] == "md"
        assert diagnostics["transaction_id"] == "new-transaction"
        assert diagnostics["transaction_id"] != "stale-transaction"

    assert lock.lock_path.read_bytes()[:1] == reserved_byte


def test_only_lock_holder_may_update_diagnostic_metadata(tmp_path):
    collect_file_lock, collect_lock_error = _lock_api()
    output = tmp_path / "MD_data.extxyz"
    lock = collect_file_lock("md", output, transaction_id="initial")

    with pytest.raises(collect_lock_error):
        lock.update_diagnostics(transaction_id="before")

    with lock as held:
        before = _read_diagnostics(held.lock_path)
        held.update_diagnostics(transaction_id="updated")
        after = _read_diagnostics(held.lock_path)
        assert after["transaction_id"] == "updated"
        for key in ("pid", "hostname", "acquired_at", "stage"):
            assert after[key] == before[key]

    with pytest.raises(collect_lock_error):
        lock.update_diagnostics(transaction_id="after")


def test_collect_lock_rechecks_hard_links_before_updating_diagnostics(tmp_path):
    collect_file_lock, collect_lock_error = _lock_api()
    output = tmp_path / "MD_data.extxyz"
    lock = collect_file_lock("md", output, transaction_id="initial")
    external_alias = tmp_path / "outside-lock-alias"

    with lock as held:
        before = _read_diagnostics(held.lock_path)
        os.link(held.lock_path, external_alias)

        with pytest.raises(collect_lock_error, match="lock invariant"):
            held.update_diagnostics(transaction_id="updated")

        assert _read_diagnostics(held.lock_path) == before
        assert _read_diagnostics(external_alias) == before
