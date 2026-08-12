from __future__ import annotations

import errno
import os
from pathlib import Path
import stat


def lock_file_is_safe(file_stat: os.stat_result) -> bool:
    return (
        stat.S_ISREG(file_stat.st_mode)
        and file_stat.st_nlink == 1
        and getattr(file_stat, "st_reparse_tag", 0) == 0
    )


def same_file_identity(
    first: os.stat_result,
    second: os.stat_result,
) -> bool:
    return first.st_dev == second.st_dev and first.st_ino == second.st_ino


def verify_open_lock_file(
    descriptor: int,
    lock_path: Path,
) -> os.stat_result:
    opened = os.fstat(descriptor)
    current = lock_path.lstat()
    if not lock_file_is_safe(opened) or not lock_file_is_safe(current):
        raise OSError(
            errno.EPERM,
            "lock path is not a single-link regular file",
            os.fspath(lock_path),
        )
    if not same_file_identity(opened, current):
        raise OSError(
            errno.EPERM,
            "lock path changed while it was being opened",
            os.fspath(lock_path),
        )
    return current


def open_verified_lock_file(lock_path: Path) -> int:
    try:
        before = lock_path.lstat()
    except FileNotFoundError:
        before = None
    else:
        if not lock_file_is_safe(before):
            raise OSError(
                errno.EPERM,
                "lock path is not a single-link regular file",
                os.fspath(lock_path),
            )

    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if before is None:
        flags |= os.O_EXCL
    descriptor = os.open(os.fspath(lock_path), flags, 0o666)
    try:
        current = verify_open_lock_file(descriptor, lock_path)
        if before is not None and not same_file_identity(before, current):
            raise OSError(
                errno.EPERM,
                "lock path changed while it was being opened",
                os.fspath(lock_path),
            )
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor
