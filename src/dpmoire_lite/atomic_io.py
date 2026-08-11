from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import platform
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TextIO


@dataclass(frozen=True)
class AtomicPublishResult:
    destination: Path
    sha256: str | None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_candidate(destination: Path) -> Path:
    """Create a unique candidate beside its eventual destination."""
    return _new_candidate(Path(destination))


def fsync_path(path: Path) -> None:
    """Flush an already-written candidate file to durable storage."""
    with Path(path).open("r+b") as handle:
        _fsync_file(handle)


def atomic_text_publish(
    destination: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    compute_sha256: bool = False,
) -> AtomicPublishResult:
    destination = Path(destination)
    candidate = _new_candidate(destination)
    replace_started = False
    try:
        with candidate.open("w", encoding=encoding, newline="") as handle:
            handle.write(text)
            _fsync_file(handle)
        digest = sha256_file(candidate) if compute_sha256 else None
        replace_started = True
        os.replace(candidate, destination)
        _fsync_directory(destination.parent)
        return AtomicPublishResult(destination=destination, sha256=digest)
    except BaseException:
        if not replace_started:
            candidate.unlink(missing_ok=True)
        raise


def atomic_bytes_publish(
    destination: Path,
    payload: bytes,
    *,
    compute_sha256: bool = False,
) -> AtomicPublishResult:
    destination = Path(destination)
    candidate = _new_candidate(destination)
    replace_started = False
    try:
        with candidate.open("wb") as handle:
            handle.write(payload)
            _fsync_file(handle)
        digest = sha256_file(candidate) if compute_sha256 else None
        replace_started = True
        os.replace(candidate, destination)
        _fsync_directory(destination.parent)
        return AtomicPublishResult(destination=destination, sha256=digest)
    except BaseException:
        if not replace_started:
            candidate.unlink(missing_ok=True)
        raise


def atomic_directory_publish_no_replace(candidate: Path, destination: Path) -> None:
    """Atomically publish a sibling directory without replacing any target entry."""
    candidate = Path(candidate)
    destination = Path(destination)
    if candidate.parent.resolve() != destination.parent.resolve():
        raise ValueError("Atomic directory publication requires sibling paths")
    if not candidate.is_dir():
        raise ValueError(f"Directory publication candidate is not a directory: {candidate}")

    if os.name == "nt":
        os.rename(candidate, destination)
    elif platform.system() == "Linux":
        _linux_rename_no_replace(candidate, destination)
    else:
        raise OSError(
            errno.ENOTSUP,
            "Atomic no-replace directory publication is unsupported on this platform",
            str(destination),
        )
    _fsync_directory(destination.parent)


def atomic_file_publish_no_replace(candidate: Path, destination: Path) -> None:
    """Atomically publish a sibling file without replacing any target entry."""
    candidate = Path(candidate)
    destination = Path(destination)
    if candidate.parent.resolve() != destination.parent.resolve():
        raise ValueError("Atomic file publication requires sibling paths")
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError(f"File publication candidate is not a regular file: {candidate}")

    if os.name == "nt":
        os.rename(candidate, destination)
    elif platform.system() == "Linux":
        _linux_rename_no_replace(candidate, destination)
    else:
        raise OSError(
            errno.ENOTSUP,
            "Atomic no-replace file publication is unsupported on this platform",
            str(destination),
        )
    _fsync_directory(destination.parent)


def _linux_rename_no_replace(candidate: Path, destination: Path) -> None:
    at_fdcwd = -100
    rename_noreplace = 1
    source_bytes = os.fsencode(candidate)
    destination_bytes = os.fsencode(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is not None:
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            at_fdcwd,
            source_bytes,
            at_fdcwd,
            destination_bytes,
            rename_noreplace,
        )
    else:
        syscall_numbers = {
            "aarch64": 276,
            "amd64": 316,
            "x86_64": 316,
        }
        syscall_number = syscall_numbers.get(platform.machine().casefold())
        if syscall_number is None:
            raise OSError(
                errno.ENOTSUP,
                "renameat2 is unavailable for atomic no-replace publication",
                str(destination),
            )
        libc.syscall.restype = ctypes.c_long
        result = libc.syscall(
            ctypes.c_long(syscall_number),
            ctypes.c_int(at_fdcwd),
            ctypes.c_char_p(source_bytes),
            ctypes.c_int(at_fdcwd),
            ctypes.c_char_p(destination_bytes),
            ctypes.c_uint(rename_noreplace),
        )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            str(destination),
        )


def _new_candidate(destination: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".candidate",
    )
    os.close(descriptor)
    return Path(name)


def _fsync_file(handle: BinaryIO | TextIO) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _fsync_directory(directory: Path) -> bool:
    if os.name == "nt":
        return False

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True


def fsync_directory(directory: Path) -> bool:
    """Flush a directory entry when the platform supports directory fsync."""
    return _fsync_directory(Path(directory))
