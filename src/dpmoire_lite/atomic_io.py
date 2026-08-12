from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import platform
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator, TextIO


@dataclass(frozen=True)
class AtomicPublishResult:
    destination: Path
    sha256: str | None


@dataclass(frozen=True)
class AtomicCopyPlan:
    source: Path
    destination: Path
    sha256: str


@dataclass(frozen=True)
class _PublishedCopy:
    destination: Path
    sha256: str


@dataclass(frozen=True)
class DirectoryIdentity:
    path: Path
    device: int
    inode: int


def verify_plain_directory(
    path: Path,
    expected: DirectoryIdentity | None = None,
) -> DirectoryIdentity:
    path = Path(path)
    observed = path.lstat()
    if not stat.S_ISDIR(observed.st_mode) or getattr(observed, "st_reparse_tag", 0):
        raise OSError(
            errno.EPERM,
            "directory path is not a plain directory",
            os.fspath(path),
        )
    identity = DirectoryIdentity(
        path=path,
        device=observed.st_dev,
        inode=observed.st_ino,
    )
    if expected is not None and identity != expected:
        raise OSError(
            errno.EPERM,
            "directory path identity changed",
            os.fspath(path),
        )
    return identity


def claim_directory_no_replace(
    path: Path,
    *,
    parent: DirectoryIdentity | None = None,
) -> DirectoryIdentity:
    path = Path(path)
    if parent is not None:
        if path.parent != parent.path:
            raise ValueError("Claimed directory must be a direct child of its parent")
        verify_plain_directory(parent.path, parent)
    path.mkdir()
    if parent is not None:
        verify_plain_directory(parent.path, parent)
    return verify_plain_directory(path)


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


@contextmanager
def atomic_copy_pair_transaction_no_replace(
    first: AtomicCopyPlan,
    second: AtomicCopyPlan,
) -> Iterator[None]:
    """Publish two verified copies and roll both back unless the caller commits.

    The caller's context is the formal commit boundary (for example, a manifest
    transition). A failure reported after a rename is reconciled before rollback
    so it cannot leave an unregistered half-pair.
    """
    prepared: list[tuple[Path, AtomicCopyPlan]] = []
    published: list[_PublishedCopy] = []
    try:
        for plan in (first, second):
            source = Path(plan.source)
            destination = Path(plan.destination)
            if source.is_symlink() or not source.is_file():
                raise OSError(f"source is not a regular file: {source.name}")
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(destination)
            if sha256_file(source) != plan.sha256:
                raise OSError(f"validated source changed: {source.name}")
            expected_size = source.stat().st_size
            candidate = create_candidate(destination)
            prepared.append((candidate, plan))
            shutil.copyfile(source, candidate)
            fsync_path(candidate)
            if (
                candidate.stat().st_size != expected_size
                or sha256_file(candidate) != plan.sha256
            ):
                raise OSError(
                    f"candidate copy verification failed: {destination.name}"
                )
        for candidate, plan in prepared:
            destination = Path(plan.destination)
            try:
                atomic_file_publish_no_replace(candidate, destination)
            except BaseException:
                _record_completed_rename(candidate, plan, published)
                raise
            published.append(
                _PublishedCopy(destination=destination, sha256=plan.sha256)
            )
            if sha256_file(destination) != plan.sha256:
                raise OSError(
                    f"published copy verification failed: {destination.name}"
                )

        yield
    except BaseException:
        _rollback_published_copies(tuple(published))
        raise
    finally:
        for candidate, _plan in prepared:
            candidate.unlink(missing_ok=True)


def _record_completed_rename(
    candidate: Path,
    plan: AtomicCopyPlan,
    published: list[_PublishedCopy],
) -> None:
    destination = Path(plan.destination)
    if candidate.exists() or candidate.is_symlink():
        return
    if destination.exists() or destination.is_symlink():
        published.append(
            _PublishedCopy(destination=destination, sha256=plan.sha256)
        )


def _rollback_published_copies(published: tuple[_PublishedCopy, ...]) -> None:
    parents: set[Path] = set()
    for record in reversed(published):
        destination = record.destination
        if destination.is_symlink() or not destination.is_file():
            raise OSError(
                "atomic copy rollback refused a changed published destination: "
                f"{destination.name}"
            )
        if sha256_file(destination) != record.sha256:
            raise OSError(
                "atomic copy rollback refused a changed published hash: "
                f"{destination.name}"
            )
        destination.unlink()
        parents.add(destination.parent)
    for parent in parents:
        fsync_directory(parent)


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
