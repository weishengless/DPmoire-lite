from __future__ import annotations

import errno
import os
import stat
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

import numpy as np
from ase.io.vasp import read_vasp

from .atomic_io import (
    AtomicCopyPlan,
    atomic_copy_pair_transaction_no_replace,
    sha256_file,
)
from .inputs import PreparedSource, prepare_source
from .manifest import (
    INIT_WORKFLOW_SCHEMA,
    Manifest,
    read_manifest,
    set_init_workflow_state,
    write_manifest,
)
from .mlab import MlabParseResult, seed_prefix_identity, parse_mlab
from .mlff_seed import SeedPrefixVerifier


if os.name == "nt":
    import msvcrt
else:
    import fcntl


class InitMlffWorkflowError(RuntimeError):
    """A bounded init-MLFF lifecycle or evidence failure."""

    def __init__(
        self,
        message: str,
        *,
        calculation_exit_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.calculation_exit_code = calculation_exit_code


def _lock_file_is_safe(file_stat: os.stat_result) -> bool:
    return (
        stat.S_ISREG(file_stat.st_mode)
        and file_stat.st_nlink == 1
        and getattr(file_stat, "st_reparse_tag", 0) == 0
    )


def _same_file_identity(
    first: os.stat_result,
    second: os.stat_result,
) -> bool:
    return first.st_dev == second.st_dev and first.st_ino == second.st_ino


def _verify_open_lock_file(descriptor: int, lock_path: Path) -> os.stat_result:
    opened = os.fstat(descriptor)
    current = lock_path.lstat()
    if not _lock_file_is_safe(opened) or not _lock_file_is_safe(current):
        raise OSError(
            errno.EPERM,
            "lock path is not a single-link regular file",
            os.fspath(lock_path),
        )
    if not _same_file_identity(opened, current):
        raise OSError(
            errno.EPERM,
            "lock path changed while it was being opened",
            os.fspath(lock_path),
        )
    return current


def _open_verified_lock_file(lock_path: Path) -> int:
    try:
        before = lock_path.lstat()
    except FileNotFoundError:
        before = None
    else:
        if not _lock_file_is_safe(before):
            raise OSError(
                errno.EPERM,
                "lock path is not a single-link regular file",
                os.fspath(lock_path),
            )

    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(os.fspath(lock_path), flags, 0o666)
    try:
        current = _verify_open_lock_file(descriptor, lock_path)
        if before is not None and not _same_file_identity(before, current):
            raise OSError(
                errno.EPERM,
                "lock path changed while it was being opened",
                os.fspath(lock_path),
            )
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


@contextmanager
def init_mlff_manifest_lock(work_dir: Path) -> Iterator[None]:
    """Serialize submission evidence with the workflow's first state transition."""
    lock_path = Path(work_dir) / ".dpmoire-lite-init-mlff.lock"
    descriptor = -1
    handle = None
    try:
        descriptor = _open_verified_lock_file(lock_path)
        handle = os.fdopen(descriptor, "r+b")
        descriptor = -1
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _verify_open_lock_file(handle.fileno(), lock_path)
    except OSError as exc:
        if handle is not None:
            handle.close()
        elif descriptor != -1:
            os.close(descriptor)
        raise InitMlffWorkflowError(
            "init MLFF manifest lock invariant failed"
        ) from exc

    try:
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@dataclass(frozen=True)
class InitMlffCalculation:
    phase: str
    role: str
    name: str
    directory: Path


class InitMlffCalculationAdapter(Protocol):
    def run(self, request: InitMlffCalculation) -> int:
        """Run one phase synchronously and return its process exit code."""


@dataclass(frozen=True)
class _ValidatedPhaseOutput:
    parsed: MlabParseResult
    ml_ab_sha256: str
    ml_ff_sha256: str


@dataclass(frozen=True)
class ValidatedPublishedInitMlffSeed:
    parsed: MlabParseResult
    ml_ab: PreparedSource
    ml_ff: PreparedSource


class InitMlffWorkflow:
    """Execute one fresh two-phase init-MLFF transaction."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = Path(work_dir).resolve(strict=False)
        self.root_dir = self.work_dir / "init_mlff"

    def run(self, adapter: InitMlffCalculationAdapter) -> Manifest:
        bottom_dir = self.root_dir / "bottom"
        top_dir = self.root_dir / "top"
        with init_mlff_manifest_lock(self.work_dir):
            manifest = self._load_fresh_manifest()
            self._preflight(manifest, bottom_dir, top_dir)
            manifest = self._transition(manifest, "step-1-running")
        step1_request = InitMlffCalculation(
            phase="step1",
            role="step-1",
            name="bottom",
            directory=bottom_dir,
        )
        self._invoke_adapter(
            adapter,
            step1_request,
            manifest,
            failure_state="step-1-failed",
        )
        try:
            self._verify_phase_static(manifest, "bottom", bottom_dir)
            self._verify_phase_static(manifest, "top", top_dir)
            step1_output = _validate_phase_output(
                step1_request,
                prior_seed=None,
            )
        except InitMlffWorkflowError as exc:
            self._fail(
                manifest,
                "step-1-failed",
                exc,
            )

        manifest = self._transition(manifest, "step-1-complete")
        try:
            with atomic_copy_pair_transaction_no_replace(
                AtomicCopyPlan(
                    source=bottom_dir / "ML_ABN",
                    destination=top_dir / "ML_AB",
                    sha256=step1_output.ml_ab_sha256,
                ),
                AtomicCopyPlan(
                    source=bottom_dir / "ML_FFN",
                    destination=top_dir / "ML_FF",
                    sha256=step1_output.ml_ff_sha256,
                ),
            ):
                manifest = self._transition(manifest, "step-2-ready")
        except Exception as exc:
            error = InitMlffWorkflowError(
                "init MLFF step1 handoff invariant failed; step2 is not ready"
            )
            self._fail(
                manifest,
                "step-1-failed",
                error,
                cause=exc,
            )

        manifest = self._transition(manifest, "step-2-running")
        step2_request = InitMlffCalculation(
            phase="step2",
            role="step-2",
            name="top",
            directory=top_dir,
        )
        self._invoke_adapter(
            adapter,
            step2_request,
            manifest,
            failure_state="step-2-failed",
        )
        try:
            self._verify_phase_static(manifest, "top", top_dir)
            step2_output = _validate_phase_output(
                step2_request,
                prior_seed=step1_output,
            )
        except InitMlffWorkflowError as exc:
            self._fail(
                manifest,
                "step-2-failed",
                exc,
            )

        try:
            with atomic_copy_pair_transaction_no_replace(
                AtomicCopyPlan(
                    source=top_dir / "ML_ABN",
                    destination=self.root_dir / "ML_ABN",
                    sha256=step2_output.ml_ab_sha256,
                ),
                AtomicCopyPlan(
                    source=top_dir / "ML_FFN",
                    destination=self.root_dir / "ML_FFN",
                    sha256=step2_output.ml_ff_sha256,
                ),
            ):
                seed_evidence = _published_seed_evidence(
                    self.root_dir,
                    step2_output.parsed,
                )
                manifest = self._transition(
                    manifest,
                    "complete",
                    mlff_seed=seed_evidence,
                )
        except Exception as exc:
            error = InitMlffWorkflowError(
                "init MLFF step2 publication invariant failed; final seed is unpublished"
            )
            self._fail(
                manifest,
                "step-2-failed",
                error,
                cause=exc,
            )
        return manifest

    def _load_fresh_manifest(self) -> Manifest:
        try:
            result = read_manifest(self.work_dir, "init_mlff")
        except Exception as exc:
            raise InitMlffWorkflowError(
                "init MLFF preflight invariant failed: workflow manifest is invalid"
            ) from exc
        if result.kind != "current" or result.manifest is None:
            raise InitMlffWorkflowError(
                "init MLFF preflight invariant failed: current workflow manifest is required"
            )
        manifest = result.manifest
        workflow = manifest.init_workflow
        if not workflow:
            raise InitMlffWorkflowError(
                "init MLFF preflight invariant failed: automated workflow evidence is missing"
            )
        if workflow["schema"] != INIT_WORKFLOW_SCHEMA:
            raise InitMlffWorkflowError(
                "init MLFF preflight invariant failed: the current submit adapter "
                "workflow schema is required for execution"
            )
        state = workflow["state"]
        if state != "step-1-ready":
            raise InitMlffWorkflowError(
                "init MLFF state invariant failed: a fresh step-1-ready workflow is required; "
                f"found {state!r}. No implicit resume, retry, or overwrite is allowed"
            )
        if manifest.jobs and manifest.jobs[0].get("status") != "SUBMITTED":
            raise InitMlffWorkflowError(
                "init MLFF submission evidence is not complete; refusing to start "
                "a workflow whose sbatch request was not recorded as submitted"
            )
        return manifest

    def _preflight(
        self,
        manifest: Manifest,
        bottom_dir: Path,
        top_dir: Path,
    ) -> None:
        if self.root_dir.is_symlink() or not self.root_dir.is_dir():
            raise InitMlffWorkflowError(
                "init MLFF preflight invariant failed: workflow root must be a real directory"
            )
        for destination in (
            self.root_dir / "ML_ABN",
            self.root_dir / "ML_FFN",
            top_dir / "ML_AB",
            top_dir / "ML_FF",
            bottom_dir / "ML_ABN",
            bottom_dir / "ML_FFN",
            top_dir / "ML_ABN",
            top_dir / "ML_FFN",
        ):
            if destination.exists() or destination.is_symlink():
                raise InitMlffWorkflowError(
                    "init MLFF preflight invariant failed: preexisting workflow output "
                    f"{destination.name!r} conflicts with a fresh transaction"
                )
        if any(self.root_dir.rglob("*.candidate")):
            raise InitMlffWorkflowError(
                "init MLFF preflight invariant failed: stale publication candidate exists"
            )

        for name, expected_dir in (("bottom", bottom_dir), ("top", top_dir)):
            self._verify_phase_static(manifest, name, expected_dir)

    def _verify_phase_static(
        self,
        manifest: Manifest,
        name: str,
        expected_dir: Path,
    ) -> None:
        phase = "step1" if name == "bottom" else "step2"
        record = manifest.init_workflow["phases"][name]
        phase_dir = self.work_dir / record["directory"]
        if phase_dir != expected_dir or phase_dir.is_symlink() or not phase_dir.is_dir():
            raise InitMlffWorkflowError(
                f"init MLFF {phase} static-input invariant failed: "
                f"{name} phase directory is invalid"
            )
        for filename, identity in record["static_inputs"].items():
            path = phase_dir / filename
            if not _matches_file_identity(path, identity):
                raise InitMlffWorkflowError(
                    f"init MLFF {phase} static-input invariant failed: "
                    f"{name} static input {filename!r} changed after planning"
                )

    def _invoke_adapter(
        self,
        adapter: InitMlffCalculationAdapter,
        request: InitMlffCalculation,
        manifest: Manifest,
        *,
        failure_state: str,
    ) -> None:
        try:
            exit_code = adapter.run(request)
        except Exception as exc:
            error = InitMlffWorkflowError(
                f"init MLFF {request.phase} adapter invariant failed: synchronous adapter raised "
                f"{type(exc).__name__}"
            )
            self._fail(
                manifest,
                failure_state,
                error,
                cause=exc,
                suppress_context=True,
            )
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            error = InitMlffWorkflowError(
                f"init MLFF {request.phase} adapter invariant failed: exit code must be an integer"
            )
            self._fail(manifest, failure_state, error)
        if exit_code != 0:
            error = InitMlffWorkflowError(
                f"init MLFF {request.phase} calculation failed with exit code {exit_code}",
                calculation_exit_code=exit_code,
            )
            self._fail(manifest, failure_state, error)

    def _transition(
        self,
        manifest: Manifest,
        workflow_state: str,
        *,
        mlff_seed: dict[str, object] | None = None,
    ) -> Manifest:
        candidate = deepcopy(manifest)
        if mlff_seed is not None:
            candidate.mlff_seed = dict(mlff_seed)
        try:
            set_init_workflow_state(candidate, workflow_state)
            write_manifest(self.work_dir, candidate)
        except Exception as exc:
            raise InitMlffWorkflowError(
                "init MLFF state publication invariant failed for "
                f"{workflow_state!r}"
            ) from exc
        return candidate

    def _fail(
        self,
        manifest: Manifest,
        state: str,
        error: InitMlffWorkflowError,
        *,
        cause: Exception | None = None,
        suppress_context: bool = False,
    ) -> None:
        try:
            self._transition(manifest, state)
        except Exception as state_exc:
            raise InitMlffWorkflowError(
                "init MLFF failure-state publication invariant failed"
            ) from state_exc
        if suppress_context:
            raise error from None
        if cause is not None:
            raise error from cause
        raise error


def validate_published_init_mlff_seed(
    work_dir: Path,
) -> ValidatedPublishedInitMlffSeed | None:
    """Return a trusted automated seed, or None for a legacy/manual workspace."""
    work_dir = Path(work_dir).resolve(strict=False)
    root = work_dir / "init_mlff"
    phase_layout_exists = (root / "bottom").exists() or (root / "top").exists()
    try:
        result = read_manifest(work_dir, "init_mlff")
    except Exception as exc:
        raise InitMlffWorkflowError(
            "automated init workflow manifest invariant failed: manifest is invalid"
        ) from exc
    if result.kind != "current" or result.manifest is None:
        if phase_layout_exists:
            raise InitMlffWorkflowError(
                "automated init workflow manifest invariant failed: current manifest is missing"
            )
        return None
    manifest = result.manifest
    if not manifest.init_workflow:
        if phase_layout_exists:
            raise InitMlffWorkflowError(
                "automated init workflow manifest invariant failed: lifecycle evidence is missing"
            )
        return None

    state = manifest.init_workflow["state"]
    if state != "complete":
        raise InitMlffWorkflowError(
            f"automated init workflow state {state!r} is not complete; "
            "Stage1 refuses an unpublished seed"
        )
    seed_path = root / "ML_ABN"
    force_field_path = root / "ML_FFN"
    _require_nonempty_regular_file(seed_path, "published", "ML_ABN")
    _require_nonempty_regular_file(force_field_path, "published", "ML_FFN")
    parsed = _parse_complete_mlab(seed_path, "published")

    evidence = manifest.mlff_seed
    if evidence["configurations"] != parsed.complete_count:
        raise InitMlffWorkflowError(
            "published init MLFF seed evidence invariant failed: configuration count changed"
        )
    if evidence["seed_prefix_sha256"] != seed_prefix_identity(parsed).sha256:
        raise InitMlffWorkflowError(
            "published init MLFF seed evidence invariant failed: canonical seed hash changed"
        )
    try:
        ml_ab = prepare_source(seed_path)
        ml_ff = prepare_source(force_field_path)
    except (OSError, RuntimeError) as exc:
        raise InitMlffWorkflowError(
            "published init MLFF seed evidence invariant failed: file identity is unstable"
        ) from exc
    if evidence["ml_ab_sha256"] != ml_ab.sha256:
        raise InitMlffWorkflowError(
            "published init MLFF seed evidence invariant failed: ML_ABN hash changed"
        )
    if evidence["ml_ff_sha256"] != ml_ff.sha256:
        raise InitMlffWorkflowError(
            "published init MLFF seed evidence invariant failed: ML_FFN hash changed"
        )
    return ValidatedPublishedInitMlffSeed(parsed=parsed, ml_ab=ml_ab, ml_ff=ml_ff)


def _validate_phase_output(
    request: InitMlffCalculation,
    *,
    prior_seed: _ValidatedPhaseOutput | None,
) -> _ValidatedPhaseOutput:
    seed_path = request.directory / "ML_ABN"
    force_field_path = request.directory / "ML_FFN"
    _require_nonempty_regular_file(seed_path, request.phase, "ML_ABN")
    _require_nonempty_regular_file(force_field_path, request.phase, "ML_FFN")
    try:
        seed_hash = sha256_file(seed_path)
        force_field_hash = sha256_file(force_field_path)
    except OSError as exc:
        raise InitMlffWorkflowError(
            f"init MLFF {request.phase} output invariant failed: output is unreadable"
        ) from exc
    parsed = _parse_complete_mlab(seed_path, request.phase)
    try:
        expected_atoms = read_vasp(request.directory / "POSCAR")
    except Exception as exc:
        raise InitMlffWorkflowError(
            f"init MLFF {request.phase} output invariant failed: POSCAR evidence is unreadable"
        ) from exc

    if prior_seed is None:
        configurations = parsed.configurations
    else:
        prior_parsed = prior_seed.parsed
        if parsed.complete_count <= prior_parsed.complete_count:
            raise InitMlffWorkflowError(
                f"init MLFF {request.phase} output invariant failed: "
                "ML_ABN contains no new top configuration"
            )
        verification = SeedPrefixVerifier(prior_parsed.configurations).verify(
            parsed.configurations
        )
        if verification.outcome == "mismatch":
            raise InitMlffWorkflowError(
                f"init MLFF {request.phase} output invariant failed: "
                "ML_ABN seed prefix does not match trusted step1 output"
            )
        configurations = parsed.configurations[prior_parsed.complete_count :]

    if not configurations or any(
        not _configuration_has_valid_phase_structure(configuration, expected_atoms)
        for configuration in configurations
    ):
        raise InitMlffWorkflowError(
            f"init MLFF {request.phase} output invariant failed: "
            "ML_ABN phase structure does not match POSCAR evidence"
        )
    if any(
        not _configuration_matches_species_order(configuration, expected_atoms)
        for configuration in configurations
    ):
        raise InitMlffWorkflowError(
            f"init MLFF {request.phase} output invariant failed: "
            "ML_ABN species order does not match POSCAR evidence"
        )
    # A training run may add displaced, variable-cell MD frames, but each phase
    # must retain one configuration that jointly anchors lattice and positions
    # to POSCAR.
    if not any(
        _configuration_matches_phase_anchor(configuration, expected_atoms)
        for configuration in configurations
    ):
        raise InitMlffWorkflowError(
            f"init MLFF {request.phase} output invariant failed: "
            "ML_ABN lattice and positions do not contain a joint periodic POSCAR anchor"
        )
    try:
        if (
            sha256_file(seed_path) != seed_hash
            or sha256_file(force_field_path) != force_field_hash
        ):
            raise InitMlffWorkflowError(
                f"init MLFF {request.phase} output invariant failed: "
                "output changed during validation"
            )
    except OSError as exc:
        raise InitMlffWorkflowError(
            f"init MLFF {request.phase} output invariant failed: output changed during validation"
        ) from exc
    return _ValidatedPhaseOutput(
        parsed=parsed,
        ml_ab_sha256=seed_hash,
        ml_ff_sha256=force_field_hash,
    )


def _parse_complete_mlab(path: Path, phase: str) -> MlabParseResult:
    try:
        parsed = parse_mlab(path)
    except Exception:
        raise InitMlffWorkflowError(
            f"init MLFF {phase} output invariant failed: ML_ABN is malformed"
        ) from None
    if parsed.status != "complete" or parsed.declared_count != parsed.complete_count:
        raise InitMlffWorkflowError(
            f"init MLFF {phase} output invariant failed: ML_ABN must be complete "
            f"(status={parsed.status!r}, declared={parsed.declared_count}, "
            f"complete={parsed.complete_count})"
        )
    if parsed.complete_count <= 0:
        raise InitMlffWorkflowError(
            f"init MLFF {phase} output invariant failed: ML_ABN has no configurations"
        )
    return parsed


def _configuration_has_valid_phase_structure(configuration, atoms) -> bool:
    if configuration.n_atoms != len(atoms):
        return False
    lattice = np.asarray(configuration.lattice, dtype=float)
    if lattice.shape != (3, 3) or not np.all(np.isfinite(lattice)):
        return False
    try:
        np.linalg.solve(lattice, np.eye(3))
    except (np.linalg.LinAlgError, ValueError):
        return False
    return True


def _configuration_matches_phase_anchor(configuration, atoms) -> bool:
    if not _configuration_matches_poscar_lattice(configuration, atoms):
        return False
    return _configuration_matches_periodic_positions(configuration, atoms)


def _configuration_matches_poscar_lattice(configuration, atoms) -> bool:
    return bool(
        np.allclose(
            np.asarray(configuration.lattice, dtype=float),
            np.asarray(atoms.cell.array, dtype=float),
            rtol=1.0e-10,
            atol=1.0e-8,
        )
    )


def _configuration_matches_species_order(configuration, atoms) -> bool:
    actual_symbols = tuple(
        element
        for element, count in zip(
            configuration.elements,
            configuration.counts,
            strict=True,
        )
        for _ in range(count)
    )
    return actual_symbols == tuple(atoms.get_chemical_symbols())


def _configuration_matches_periodic_positions(configuration, atoms) -> bool:
    lattice = np.asarray(configuration.lattice, dtype=float)
    positions = np.asarray(configuration.positions, dtype=float)
    try:
        actual_scaled = np.linalg.solve(lattice.T, positions.T).T
    except np.linalg.LinAlgError:
        return False
    expected_scaled = np.asarray(atoms.get_scaled_positions(wrap=True), dtype=float)
    if actual_scaled.shape != expected_scaled.shape:
        return False
    delta = actual_scaled - expected_scaled
    periodic = np.asarray(atoms.pbc, dtype=bool)
    delta[:, periodic] -= np.rint(delta[:, periodic])
    cartesian_delta = delta @ np.asarray(atoms.cell.array, dtype=float)
    return bool(np.allclose(cartesian_delta, 0.0, rtol=0.0, atol=1.0e-7))


def _require_nonempty_regular_file(path: Path, phase: str, name: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise InitMlffWorkflowError(
            f"init MLFF {phase} output invariant failed: {name} is missing"
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise InitMlffWorkflowError(
            f"init MLFF {phase} output invariant failed: {name} is unreadable"
        ) from exc
    if size == 0:
        raise InitMlffWorkflowError(
            f"init MLFF {phase} output invariant failed: {name} is empty"
        )


def _matches_file_identity(path: Path, identity: dict[str, object]) -> bool:
    try:
        return (
            not path.is_symlink()
            and path.is_file()
            and path.stat().st_size == identity["size"]
            and sha256_file(path) == identity["sha256"]
        )
    except OSError:
        return False


def _published_seed_evidence(
    root: Path,
    parsed: MlabParseResult,
) -> dict[str, object]:
    identity = seed_prefix_identity(parsed)
    return {
        "source": "init_mlff/ML_ABN",
        "configurations": parsed.complete_count,
        "digest_schema": identity.schema,
        "seed_prefix_sha256": identity.sha256,
        "ml_ab_sha256": sha256_file(root / "ML_ABN"),
        "ml_ff_sha256": sha256_file(root / "ML_FFN"),
    }
