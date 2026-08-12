from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket

from .lock_safety import open_verified_lock_file, verify_open_lock_file


if os.name == "nt":
    import msvcrt
else:
    import fcntl


class CollectLockError(RuntimeError):
    """Raised when a collection lock cannot be acquired or used."""


def _lock_invariant_error(lock_path: Path) -> CollectLockError:
    return CollectLockError(f"collection lock invariant failed: {lock_path}")


def _verify_collection_lock_file(
    descriptor: int,
    lock_path: Path,
) -> os.stat_result:
    try:
        return verify_open_lock_file(descriptor, lock_path)
    except OSError as exc:
        raise _lock_invariant_error(lock_path) from exc


def _open_verified_collection_lock_file(lock_path: Path) -> int:
    try:
        return open_verified_lock_file(lock_path)
    except OSError as exc:
        raise _lock_invariant_error(lock_path) from exc


class CollectFileLock:
    def __init__(
        self,
        stage: str,
        final_output: Path,
        *,
        transaction_id: str | None = None,
    ):
        normalized_output = Path(final_output).resolve(strict=False)
        normalized_text = os.path.normcase(os.fspath(normalized_output))
        identity = (stage + "\0" + normalized_text).encode("utf-8")
        digest = hashlib.sha256(identity).hexdigest()

        self._stage = stage
        self._lock_path = normalized_output.parent / (
            f".dpmoire-lite-collect-{digest}.lock"
        )
        self._transaction_id = transaction_id
        self._handle = None
        self._held = False
        self._pid = None
        self._hostname = None
        self._acquired_at = None

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def _open_handle(self):
        descriptor = _open_verified_collection_lock_file(self._lock_path)
        try:
            handle = os.fdopen(descriptor, "r+b")
        except BaseException:
            os.close(descriptor)
            raise

        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                _verify_collection_lock_file(handle.fileno(), self._lock_path)
                handle.seek(0)
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
        except BaseException:
            handle.close()
            raise
        return handle

    @staticmethod
    def _lock_handle(handle) -> None:
        if os.name == "nt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_handle(handle) -> None:
        if os.name == "nt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _diagnostic_payload(self, transaction_id: str | None) -> bytes:
        payload = {
            "pid": self._pid,
            "hostname": self._hostname,
            "acquired_at": self._acquired_at,
            "stage": self._stage,
            "transaction_id": transaction_id,
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def _write_diagnostics(self, transaction_id: str | None) -> None:
        handle = self._handle
        if handle is None:
            raise CollectLockError("collection lock is not held")

        payload = self._diagnostic_payload(transaction_id)
        _verify_collection_lock_file(handle.fileno(), self._lock_path)
        handle.seek(1)
        handle.write(payload)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())

    def _release(self) -> None:
        handle = self._handle
        if handle is None:
            self._held = False
            return

        try:
            self._unlock_handle(handle)
        finally:
            try:
                handle.close()
            finally:
                self._handle = None
                self._held = False

    def __enter__(self) -> CollectFileLock:
        if self._held or self._handle is not None:
            raise CollectLockError("collection lock object is already held")

        handle = self._open_handle()
        try:
            self._lock_handle(handle)
        except OSError as exc:
            try:
                handle.close()
            finally:
                raise CollectLockError(
                    f"collection lock is already held: {self._lock_path}"
                ) from exc

        self._handle = handle
        self._held = True
        self._pid = os.getpid()
        self._hostname = socket.gethostname()
        self._acquired_at = datetime.now(timezone.utc).isoformat()
        try:
            self._write_diagnostics(self._transaction_id)
        except BaseException:
            self._release()
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self._release()
        return False

    def update_diagnostics(self, *, transaction_id: str | None) -> None:
        if not self._held or self._handle is None:
            raise CollectLockError("collection lock is not held")
        self._write_diagnostics(transaction_id)
        self._transaction_id = transaction_id
