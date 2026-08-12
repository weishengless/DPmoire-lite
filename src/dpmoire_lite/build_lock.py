from __future__ import annotations

import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

from .atomic_io import PinnedDirectory, pinned_directory, verify_plain_directory
from .lock_safety import open_verified_lock_file, verify_open_lock_file


if os.name == "nt":
    import msvcrt
else:
    import fcntl


class BuildExecutionLockError(RuntimeError):
    """A build could not establish its work-directory serialization boundary."""


@contextmanager
def build_execution_lock(work_dir: Path) -> Iterator[PinnedDirectory]:
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        work_directory = verify_plain_directory(work_dir)
    except OSError as exc:
        raise BuildExecutionLockError(
            "build work directory is not a stable plain directory"
        ) from exc

    descriptor = -1
    handle = None
    with ExitStack() as stack:
        try:
            pinned_work_directory = stack.enter_context(
                pinned_directory(work_dir, work_directory)
            )
            lock_path = pinned_work_directory.write_path / ".dpmoire-lite-build.lock"
            descriptor = open_verified_lock_file(lock_path, exclusive_create=False)
            pinned_work_directory.verify()
            handle = os.fdopen(descriptor, "r+b")
            descriptor = -1
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                verify_open_lock_file(handle.fileno(), lock_path)
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            verify_open_lock_file(handle.fileno(), lock_path)
            pinned_work_directory.verify()
        except OSError as exc:
            if handle is not None:
                handle.close()
            elif descriptor != -1:
                os.close(descriptor)
            raise BuildExecutionLockError(
                "build execution lock invariant failed"
            ) from exc

        try:
            yield pinned_work_directory
        finally:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
