from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import posixpath
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Sequence
import warnings

from .atomic_io import sha256_file
from .mlab import (
    MlabConfiguration,
    MlabIdentity,
    MlabParseError,
    parse_mlab,
    seed_prefix_identity,
)


SEED_PREFIX_EQUIVALENCE_SCHEMA = "vasp-seed-prefix-equivalence-v1"
_SCALED_TOLERANCE = 1e-12
_NUMERIC_FIELDS = ("lattice", "positions", "energy", "forces", "stress_kbar")


@dataclass(frozen=True)
class SeedNumericDelta:
    absolute: float
    scaled: float


@dataclass(frozen=True)
class SeedPrefixDeltas:
    lattice: SeedNumericDelta
    positions: SeedNumericDelta
    energy: SeedNumericDelta
    forces: SeedNumericDelta
    stress_kbar: SeedNumericDelta

    @classmethod
    def zero(cls) -> SeedPrefixDeltas:
        zero = SeedNumericDelta(absolute=0.0, scaled=0.0)
        return cls(
            lattice=zero,
            positions=zero,
            energy=zero,
            forces=zero,
            stress_kbar=zero,
        )

    @classmethod
    def _from_maxima(
        cls,
        maxima: dict[str, tuple[float, float]],
    ) -> SeedPrefixDeltas:
        return cls(
            **{
                field: SeedNumericDelta(absolute=absolute, scaled=scaled)
                for field, (absolute, scaled) in maxima.items()
            }
        )


@dataclass(frozen=True)
class SeedPrefixMismatch:
    configuration_index: int | None
    field: str
    component: tuple[int, ...] | None
    expected: object
    actual: object
    absolute: float | None
    scaled: float | None
    reason: str


@dataclass(frozen=True)
class SeedReference:
    source: str
    raw_sha256: str
    trust: str


@dataclass(frozen=True)
class SeedReferenceFailure:
    code: str
    source: str
    reason: str
    expected: object = None
    actual: object = None


@dataclass(frozen=True)
class SeedPrefixVerification:
    schema: str
    outcome: str
    configurations: int | None
    expected_exact_identity: MlabIdentity | None
    actual_exact_identity: MlabIdentity | None
    max_deltas: SeedPrefixDeltas
    first_mismatch: SeedPrefixMismatch | None
    reference: SeedReference | None = None
    reference_failure: SeedReferenceFailure | None = None

    def as_diagnostic(self) -> dict[str, object]:
        diagnostic: dict[str, object] = {
            "status": self.outcome,
            "schema": self.schema,
        }
        if self.configurations is not None:
            diagnostic["configurations"] = self.configurations
        if self.expected_exact_identity is not None:
            diagnostic["expected_exact_sha256"] = (
                self.expected_exact_identity.sha256
            )
        if self.actual_exact_identity is not None:
            diagnostic["actual_exact_sha256"] = self.actual_exact_identity.sha256
        if self.reference is not None:
            diagnostic["reference"] = {
                "source": self.reference.source,
                "raw_sha256": self.reference.raw_sha256,
                "trust": self.reference.trust,
            }
        diagnostic["max_deltas"] = {
            field: {
                "absolute": getattr(self.max_deltas, field).absolute,
                "scaled": getattr(self.max_deltas, field).scaled,
            }
            for field in _NUMERIC_FIELDS
        }
        if self.first_mismatch is not None:
            mismatch = self.first_mismatch
            diagnostic["first_mismatch"] = {
                "configuration_index": mismatch.configuration_index,
                "field": mismatch.field,
                "component": _diagnostic_value(mismatch.component),
                "expected": _diagnostic_value(mismatch.expected),
                "actual": _diagnostic_value(mismatch.actual),
                "absolute": mismatch.absolute,
                "scaled": mismatch.scaled,
                "reason": mismatch.reason,
            }
        if self.reference_failure is not None:
            failure = self.reference_failure
            diagnostic["reference_failure"] = {
                "code": failure.code,
                "source": failure.source,
                "reason": failure.reason,
                "expected": _diagnostic_value(failure.expected),
                "actual": _diagnostic_value(failure.actual),
            }
        return diagnostic


class SeedPrefixVerifier:
    """Compare one trusted seed with the same-length prefix of parsed VASP output."""

    def __init__(self, reference_configurations: Sequence[MlabConfiguration]) -> None:
        self._reference = tuple(reference_configurations)
        self._expected_exact_identity = seed_prefix_identity(self._reference)
        self._configurations = len(self._reference)
        self._adapter = "trusted-memory"
        self._reference_loaded = True
        self._reference_record = None
        self._reference_failure = None

    @classmethod
    def from_current_manifest(
        cls,
        *,
        work_dir: Path,
        evidence: Mapping[str, Any],
    ) -> SeedPrefixVerifier:
        if not isinstance(evidence, Mapping):
            raise ValueError("mlff_seed evidence must be a mapping")

        configurations = evidence.get("configurations")
        if (
            isinstance(configurations, bool)
            or not isinstance(configurations, int)
            or configurations <= 0
        ):
            raise ValueError("mlff_seed.configurations must be a positive integer")
        if evidence.get("digest_schema") != "mlab-seed-v1":
            raise ValueError("mlff_seed.digest_schema must be 'mlab-seed-v1'")

        exact_sha256 = _require_sha256(
            evidence.get("seed_prefix_sha256"),
            "mlff_seed.seed_prefix_sha256",
        )
        raw_sha256 = _require_sha256(
            evidence.get("ml_ab_sha256"),
            "mlff_seed.ml_ab_sha256",
        )
        resolved_work_dir = Path(work_dir).resolve(strict=False)
        source = _contained_source(
            evidence.get("source"),
            resolved_work_dir,
            "mlff_seed.source",
        )

        verifier = cls.__new__(cls)
        verifier._reference = None
        verifier._expected_exact_identity = MlabIdentity(
            schema="mlab-seed-v1",
            sha256=exact_sha256,
        )
        verifier._configurations = configurations
        verifier._adapter = "manifest-v2"
        verifier._work_dir = resolved_work_dir
        verifier._reference_source = source
        verifier._expected_raw_sha256 = raw_sha256
        verifier._reference_loaded = False
        verifier._reference_record = None
        verifier._reference_failure = None
        return verifier

    @classmethod
    def from_legacy_work_dir(
        cls,
        *,
        work_dir: Path,
    ) -> SeedPrefixVerifier:
        verifier = cls.__new__(cls)
        verifier._reference = None
        verifier._expected_exact_identity = None
        verifier._configurations = None
        verifier._adapter = "legacy-rebuilt"
        verifier._work_dir = Path(work_dir).resolve(strict=False)
        verifier._reference_source = "init_mlff/ML_ABN"
        verifier._expected_raw_sha256 = None
        verifier._reference_loaded = False
        verifier._reference_record = None
        verifier._reference_failure = None
        return verifier

    def verify(
        self,
        final_configurations: Sequence[MlabConfiguration],
    ) -> SeedPrefixVerification:
        final = tuple(final_configurations)
        if self._adapter == "legacy-rebuilt" and not self._reference_loaded:
            self._load_legacy_reference()
        if (
            self._reference_failure is not None
            and (
                self._configurations is None
                or self._expected_exact_identity is None
            )
        ):
            return self._reference_failure_result(None)
        if self._configurations is None or self._expected_exact_identity is None:
            raise AssertionError("verified reference metadata is unavailable")

        if len(final) < self._configurations:
            return SeedPrefixVerification(
                schema=SEED_PREFIX_EQUIVALENCE_SCHEMA,
                outcome="mismatch",
                configurations=self._configurations,
                expected_exact_identity=self._expected_exact_identity,
                actual_exact_identity=seed_prefix_identity(final),
                max_deltas=SeedPrefixDeltas.zero(),
                first_mismatch=SeedPrefixMismatch(
                    configuration_index=None,
                    field="configurations",
                    component=None,
                    expected=self._configurations,
                    actual=len(final),
                    absolute=None,
                    scaled=None,
                    reason="short_prefix",
                ),
                reference=(
                    self._reference_record
                    if self._adapter == "legacy-rebuilt"
                    else None
                ),
            )

        actual_identity = seed_prefix_identity(final[: self._configurations])
        if actual_identity == self._expected_exact_identity:
            return SeedPrefixVerification(
                schema=SEED_PREFIX_EQUIVALENCE_SCHEMA,
                outcome="exact",
                configurations=self._configurations,
                expected_exact_identity=self._expected_exact_identity,
                actual_exact_identity=actual_identity,
                max_deltas=SeedPrefixDeltas.zero(),
                first_mismatch=None,
                reference=(
                    self._reference_record
                    if self._adapter == "legacy-rebuilt"
                    else None
                ),
            )

        if self._reference is None:
            self._load_current_reference()
        if self._reference_failure is not None:
            return self._reference_failure_result(actual_identity)
        if self._reference is None:
            raise AssertionError("reference loader returned neither data nor failure")

        maxima = {field: (0.0, 0.0) for field in _NUMERIC_FIELDS}
        first_mismatch = None
        pairs = zip(self._reference, final[: self._configurations], strict=True)
        for configuration_index, (reference, rewritten) in enumerate(pairs):
            structural_mismatch = _structural_mismatch(
                configuration_index,
                reference,
                rewritten,
            )
            if structural_mismatch is not None:
                return SeedPrefixVerification(
                    schema=SEED_PREFIX_EQUIVALENCE_SCHEMA,
                    outcome="mismatch",
                    configurations=self._configurations,
                    expected_exact_identity=self._expected_exact_identity,
                    actual_exact_identity=actual_identity,
                    max_deltas=SeedPrefixDeltas._from_maxima(maxima),
                    first_mismatch=first_mismatch or structural_mismatch,
                    reference=self._reference_record,
                )

            for field in _NUMERIC_FIELDS:
                expected_values = _numeric_components(reference, field)
                actual_values = _numeric_components(rewritten, field)
                if len(expected_values) != len(actual_values):
                    shape_mismatch = SeedPrefixMismatch(
                        configuration_index=configuration_index,
                        field=field,
                        component=None,
                        expected=len(expected_values),
                        actual=len(actual_values),
                        absolute=None,
                        scaled=None,
                        reason="shape_mismatch",
                    )
                    return SeedPrefixVerification(
                        schema=SEED_PREFIX_EQUIVALENCE_SCHEMA,
                        outcome="mismatch",
                        configurations=self._configurations,
                        expected_exact_identity=self._expected_exact_identity,
                        actual_exact_identity=actual_identity,
                        max_deltas=SeedPrefixDeltas._from_maxima(maxima),
                        first_mismatch=first_mismatch or shape_mismatch,
                        reference=self._reference_record,
                    )
                components = zip(expected_values, actual_values, strict=True)
                for (component, expected), (actual_component, actual) in components:
                    if component != actual_component:
                        shape_mismatch = SeedPrefixMismatch(
                            configuration_index=configuration_index,
                            field=field,
                            component=component,
                            expected=component,
                            actual=actual_component,
                            absolute=None,
                            scaled=None,
                            reason="shape_mismatch",
                        )
                        return SeedPrefixVerification(
                            schema=SEED_PREFIX_EQUIVALENCE_SCHEMA,
                            outcome="mismatch",
                            configurations=self._configurations,
                            expected_exact_identity=self._expected_exact_identity,
                            actual_exact_identity=actual_identity,
                            max_deltas=SeedPrefixDeltas._from_maxima(maxima),
                            first_mismatch=first_mismatch or shape_mismatch,
                            reference=self._reference_record,
                        )
                    absolute = abs(expected - actual)
                    scaled = absolute / max(1.0, abs(expected), abs(actual))
                    maximum_absolute, maximum_scaled = maxima[field]
                    maxima[field] = (
                        max(maximum_absolute, absolute),
                        max(maximum_scaled, scaled),
                    )
                    if scaled > _SCALED_TOLERANCE and first_mismatch is None:
                        first_mismatch = SeedPrefixMismatch(
                            configuration_index=configuration_index,
                            field=field,
                            component=component,
                            expected=expected,
                            actual=actual,
                            absolute=absolute,
                            scaled=scaled,
                            reason="numeric_delta_exceeds_v1",
                        )

        deltas = SeedPrefixDeltas._from_maxima(maxima)
        if first_mismatch is not None:
            return SeedPrefixVerification(
                schema=SEED_PREFIX_EQUIVALENCE_SCHEMA,
                outcome="mismatch",
                configurations=self._configurations,
                expected_exact_identity=self._expected_exact_identity,
                actual_exact_identity=actual_identity,
                max_deltas=deltas,
                first_mismatch=first_mismatch,
                reference=self._reference_record,
            )

        return SeedPrefixVerification(
            schema=SEED_PREFIX_EQUIVALENCE_SCHEMA,
            outcome="vasp_equivalent",
            configurations=self._configurations,
            expected_exact_identity=self._expected_exact_identity,
            actual_exact_identity=actual_identity,
            max_deltas=deltas,
            first_mismatch=None,
            reference=self._reference_record,
        )

    def _load_current_reference(self) -> None:
        if self._reference_loaded:
            return
        self._reference_loaded = True

        source_path = (self._work_dir / self._reference_source).resolve(strict=False)
        try:
            source_path.relative_to(self._work_dir)
        except ValueError:
            self._set_reference_failure(
                "source_outside_work_dir",
                "reference source resolves outside work_dir",
            )
            return
        if not source_path.is_file():
            self._set_reference_failure(
                "source_missing",
                "reference source is missing or is not a regular file",
            )
            return

        try:
            actual_raw_sha256 = sha256_file(source_path)
        except OSError:
            self._set_reference_failure(
                "source_read_failed",
                "reference source could not be read",
            )
            return
        if actual_raw_sha256 != self._expected_raw_sha256:
            self._set_reference_failure(
                "raw_sha256_mismatch",
                "reference raw SHA-256 does not match manifest evidence",
                expected=self._expected_raw_sha256,
                actual=actual_raw_sha256,
            )
            return

        try:
            parsed = parse_mlab(source_path)
        except MlabParseError as error:
            self._set_reference_failure(
                "parse_failed",
                _parse_failure_reason(error),
            )
            return
        if parsed.status != "complete":
            self._set_reference_failure(
                "reference_incomplete",
                "reference ML_ABN must parse completely",
                expected="complete",
                actual=parsed.status,
            )
            return
        if parsed.complete_count != self._configurations:
            self._set_reference_failure(
                "configuration_count_mismatch",
                "reference configuration count does not match manifest evidence",
                expected=self._configurations,
                actual=parsed.complete_count,
            )
            return

        actual_identity = seed_prefix_identity(parsed.configurations)
        if actual_identity != self._expected_exact_identity:
            self._set_reference_failure(
                "exact_identity_mismatch",
                "reference exact identity does not match manifest evidence",
                expected=self._expected_exact_identity.sha256,
                actual=actual_identity.sha256,
            )
            return

        self._reference = parsed.configurations
        self._reference_record = SeedReference(
            source=self._reference_source,
            raw_sha256=actual_raw_sha256,
            trust="manifest-v2",
        )

    def _load_legacy_reference(self) -> None:
        if self._reference_loaded:
            return
        self._reference_loaded = True

        source_path = (self._work_dir / self._reference_source).resolve(strict=False)
        try:
            source_path.relative_to(self._work_dir)
        except ValueError:
            self._set_reference_failure(
                "source_outside_work_dir",
                "legacy reference source resolves outside work_dir",
            )
            return
        if not source_path.is_file():
            self._set_reference_failure(
                "source_missing",
                "legacy reference source is missing or is not a regular file",
            )
            return

        try:
            raw_sha256 = sha256_file(source_path)
        except OSError:
            self._set_reference_failure(
                "source_read_failed",
                "legacy reference source could not be read",
            )
            return
        try:
            parsed = parse_mlab(source_path)
        except MlabParseError as error:
            self._set_reference_failure(
                "parse_failed",
                _parse_failure_reason(error),
            )
            return
        if parsed.status != "complete":
            self._set_reference_failure(
                "reference_incomplete",
                "legacy reference ML_ABN must parse completely",
                expected="complete",
                actual=parsed.status,
            )
            return

        identity = seed_prefix_identity(parsed.configurations)
        self._reference = parsed.configurations
        self._configurations = parsed.complete_count
        self._expected_exact_identity = identity
        self._reference_record = SeedReference(
            source=self._reference_source,
            raw_sha256=raw_sha256,
            trust="legacy-rebuilt",
        )
        warnings.warn(
            "Legacy seed evidence was rebuilt from init_mlff/ML_ABN using "
            "mlab-seed-v1; the final prefix is being verified.",
            UserWarning,
            stacklevel=3,
        )

    def _set_reference_failure(
        self,
        code: str,
        reason: str,
        *,
        expected: object = None,
        actual: object = None,
    ) -> None:
        self._reference_failure = SeedReferenceFailure(
            code=code,
            source=self._reference_source,
            reason=reason,
            expected=expected,
            actual=actual,
        )

    def _reference_failure_result(
        self,
        actual_identity: MlabIdentity | None,
    ) -> SeedPrefixVerification:
        failure = self._reference_failure
        if failure is None:
            raise AssertionError("reference failure result requires a cached failure")
        return SeedPrefixVerification(
            schema=SEED_PREFIX_EQUIVALENCE_SCHEMA,
            outcome="mismatch",
            configurations=self._configurations,
            expected_exact_identity=self._expected_exact_identity,
            actual_exact_identity=actual_identity,
            max_deltas=SeedPrefixDeltas.zero(),
            first_mismatch=SeedPrefixMismatch(
                configuration_index=None,
                field="reference",
                component=None,
                expected="trusted_reference",
                actual=failure.code,
                absolute=None,
                scaled=None,
                reason="reference_failure",
            ),
            reference_failure=failure,
        )


def _numeric_components(
    configuration: MlabConfiguration,
    field: str,
) -> tuple[tuple[tuple[int, ...] | None, float], ...]:
    value = getattr(configuration, field)
    if field == "energy":
        return ((None, float(value)),)
    if field in {"lattice", "positions", "forces"}:
        return tuple(
            ((row_index, component_index), float(component))
            for row_index, row in enumerate(value)
            for component_index, component in enumerate(row)
        )
    return tuple(
        ((component_index,), float(component))
        for component_index, component in enumerate(value)
    )


def _diagnostic_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _diagnostic_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_diagnostic_value(item) for item in value]
    return value


def _structural_mismatch(
    configuration_index: int,
    reference: MlabConfiguration,
    rewritten: MlabConfiguration,
) -> SeedPrefixMismatch | None:
    for field in ("elements", "counts"):
        expected = getattr(reference, field)
        actual = getattr(rewritten, field)
        if expected != actual:
            component_index = next(
                index
                for index in range(max(len(expected), len(actual)))
                if (
                    (expected[index] if index < len(expected) else None)
                    != (actual[index] if index < len(actual) else None)
                )
            )
            return SeedPrefixMismatch(
                configuration_index=configuration_index,
                field=field,
                component=(component_index,),
                expected=(
                    expected[component_index]
                    if component_index < len(expected)
                    else None
                ),
                actual=(
                    actual[component_index]
                    if component_index < len(actual)
                    else None
                ),
                absolute=None,
                scaled=None,
                reason="structural_mismatch",
            )
    if reference.n_atoms != rewritten.n_atoms:
        return SeedPrefixMismatch(
            configuration_index=configuration_index,
            field="n_atoms",
            component=None,
            expected=reference.n_atoms,
            actual=rewritten.n_atoms,
            absolute=None,
            scaled=None,
            reason="structural_mismatch",
        )
    return None


def _require_sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a 64-character lowercase hexadecimal string")
    return value


def _parse_failure_reason(error: MlabParseError) -> str:
    details = [error.reason]
    if error.configuration_number is not None:
        details.append(f"configuration {error.configuration_number}")
    if error.block:
        details.append(f"block {error.block}")
    if error.line_number is not None:
        details.append(f"line {error.line_number}")
    return ", ".join(details)


def _contained_source(value: object, work_dir: Path, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a safe relative path")
    raw = value.replace("\\", "/")
    posix_path = PurePosixPath(raw)
    windows_path = PureWindowsPath(raw)
    if (
        not raw
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in posix_path.parts
    ):
        raise ValueError(f"{field} must be a safe relative path")
    normalized = posixpath.normpath(raw)
    if normalized in {"", "."}:
        raise ValueError(f"{field} must be a safe relative path")
    resolved_source = (work_dir / normalized).resolve(strict=False)
    try:
        resolved_source.relative_to(work_dir)
    except ValueError as exc:
        raise ValueError(f"{field} escapes work_dir") from exc
    return normalized
