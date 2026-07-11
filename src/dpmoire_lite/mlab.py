from __future__ import annotations

import hashlib
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


_CONFIGURATION_RE = re.compile(r"^Configuration num\.\s+(\d+)\s*$")


class MlabParseError(ValueError):
    """A structured ML_AB/ML_ABN format or validation error."""

    def __init__(
        self,
        path: Path,
        *,
        configuration_number: int | None,
        block: str,
        reason: str,
        line_number: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.configuration_number = configuration_number
        self.block = block
        self.reason = reason
        self.line_number = line_number

        location = f"line {line_number}" if line_number is not None else "unknown line"
        configuration = (
            f"configuration {configuration_number}"
            if configuration_number is not None
            else "header"
        )
        super().__init__(f"{self.path}: {configuration}, {location}, {block}: {reason}")


@dataclass(frozen=True)
class MlabConfiguration:
    """One validated ML_AB/ML_ABN configuration in source units."""

    source_path: Path
    source_configuration_number: int
    source_line: int
    elements: tuple[str, ...]
    counts: tuple[int, ...]
    n_atoms: int
    lattice: tuple[tuple[float, float, float], ...]
    positions: tuple[tuple[float, float, float], ...]
    energy: float
    forces: tuple[tuple[float, float, float], ...]
    stress_kbar: tuple[float, float, float, float, float, float]

    @property
    def atom_types(self) -> tuple[str, ...]:
        return self.elements

    @property
    def atom_counts(self) -> tuple[int, ...]:
        return self.counts

    @property
    def lattice_vectors(self) -> tuple[tuple[float, float, float], ...]:
        return self.lattice

    @property
    def total_energy(self) -> float:
        return self.energy

    @property
    def stress(self) -> tuple[float, float, float, float, float, float]:
        return self.stress_kbar


@dataclass(frozen=True)
class MlabParseResult:
    """The validated result of parsing one complete ML_AB/ML_ABN source."""

    source_path: Path
    status: str
    declared_count: int
    configurations: tuple[MlabConfiguration, ...]
    discarded_configuration_number: int | None = None
    discarded_block: str | None = None
    discarded_reason: str | None = None

    @property
    def complete_count(self) -> int:
        return len(self.configurations)

    @property
    def accepted_count(self) -> int:
        return len(self.configurations)


@dataclass(frozen=True)
class MlabIdentity:
    schema: str
    sha256: str

    @property
    def schema_name(self) -> str:
        return self.schema

    @property
    def digest(self) -> str:
        return self.sha256


ParseResult = MlabParseResult
Configuration = MlabConfiguration


def _is_separator(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and all(character in "*=-" for character in stripped)


def _next_content(lines: Sequence[str], index: int) -> int:
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped and not _is_separator(lines[index]):
            return index
        index += 1
    return index


def _configuration_number(line: str) -> int | None:
    match = _CONFIGURATION_RE.match(line.strip())
    return int(match.group(1)) if match else None


def _raise(
    path: Path,
    configuration_number: int | None,
    block: str,
    reason: str,
    line_index: int | None,
) -> None:
    raise MlabParseError(
        path,
        configuration_number=configuration_number,
        block=block,
        reason=reason,
        line_number=line_index + 1 if line_index is not None else None,
    )


def _expect_label(
    lines: Sequence[str],
    index: int,
    label: str,
    path: Path,
    configuration_number: int | None,
    block: str,
) -> int:
    index = _next_content(lines, index)
    if index >= len(lines):
        _raise(path, configuration_number, block, f"missing {label!r}", index)
    if lines[index].strip() != label:
        _raise(
            path,
            configuration_number,
            block,
            f"expected {label!r}, found {lines[index].strip()!r}",
            index,
        )
    return index + 1


def _read_text_value(
    lines: Sequence[str],
    index: int,
    path: Path,
    configuration_number: int | None,
    block: str,
) -> tuple[str, int]:
    index = _next_content(lines, index)
    if index >= len(lines):
        _raise(path, configuration_number, block, "unexpected end of file", index)
    if _configuration_number(lines[index]) is not None:
        _raise(path, configuration_number, block, "unexpected configuration marker", index)
    return lines[index].strip(), index + 1


def _read_integer(
    lines: Sequence[str],
    index: int,
    path: Path,
    configuration_number: int | None,
    block: str,
) -> tuple[int, int]:
    value, index = _read_text_value(lines, index, path, configuration_number, block)
    fields = value.split()
    if len(fields) != 1:
        _raise(path, configuration_number, block, "expected one integer", index - 1)
    try:
        parsed = int(fields[0])
    except ValueError:
        _raise(path, configuration_number, block, f"invalid integer {fields[0]!r}", index - 1)
    return parsed, index


def _read_float_row(
    lines: Sequence[str],
    index: int,
    width: int,
    path: Path,
    configuration_number: int | None,
    block: str,
) -> tuple[tuple[float, ...], int]:
    value, index = _read_text_value(lines, index, path, configuration_number, block)
    fields = value.split()
    if len(fields) != width:
        _raise(
            path,
            configuration_number,
            block,
            f"shape mismatch: expected {width} values, got {len(fields)}",
            index - 1,
        )

    parsed: list[float] = []
    for token in fields:
        try:
            number = float(token)
        except ValueError:
            _raise(path, configuration_number, block, f"invalid float {token!r}", index - 1)
        if not math.isfinite(number):
            _raise(path, configuration_number, block, f"non-finite float {token!r}", index - 1)
        parsed.append(number)
    return tuple(parsed), index


def _read_float_rows(
    lines: Sequence[str],
    index: int,
    count: int,
    width: int,
    path: Path,
    configuration_number: int | None,
    block: str,
) -> tuple[tuple[tuple[float, ...], ...], int]:
    rows: list[tuple[float, ...]] = []
    for _ in range(count):
        row, index = _read_float_row(
            lines,
            index,
            width,
            path,
            configuration_number,
            block,
        )
        rows.append(row)
    return tuple(rows), index


def _parse_header(lines: Sequence[str], path: Path, first_configuration: int) -> int:
    required_labels = (
        "The number of configurations",
        "The maximum number of atom type",
        "The atom types in the data file",
        "The maximum number of atoms per system",
        "The maximum number of atoms per atom type",
    )
    header = lines[:first_configuration]
    for label in required_labels:
        if not any(line.strip() == label for line in header):
            _raise(path, None, "header", f"missing {label!r}", None)

    count_label = next(
        index for index, line in enumerate(header) if line.strip() == "The number of configurations"
    )
    count_index = _next_content(header, count_label + 1)
    if count_index >= len(header):
        _raise(path, None, "header", "missing configuration count", count_index)
    fields = header[count_index].strip().split()
    if len(fields) != 1:
        _raise(path, None, "header", "configuration count must be one integer", count_index)
    try:
        declared_count = int(fields[0])
    except ValueError:
        _raise(path, None, "header", "invalid configuration count", count_index)
    if declared_count < 0:
        _raise(path, None, "header", "configuration count must be non-negative", count_index)
    return declared_count


def _parse_configuration(
    lines: Sequence[str],
    marker_index: int,
    path: Path,
) -> tuple[MlabConfiguration, int]:
    configuration_number = _configuration_number(lines[marker_index])
    assert configuration_number is not None
    index = marker_index + 1

    index = _expect_label(
        lines, index, "System name", path, configuration_number, "system name"
    )
    _system_name, index = _read_text_value(
        lines, index, path, configuration_number, "system name"
    )

    index = _expect_label(
        lines,
        index,
        "The number of atom types",
        path,
        configuration_number,
        "atom type count",
    )
    n_types, index = _read_integer(
        lines, index, path, configuration_number, "atom type count"
    )
    if n_types <= 0:
        _raise(path, configuration_number, "atom type count", "must be positive", index - 1)

    index = _expect_label(
        lines, index, "The number of atoms", path, configuration_number, "atom count"
    )
    n_atoms, index = _read_integer(lines, index, path, configuration_number, "atom count")
    if n_atoms <= 0:
        _raise(path, configuration_number, "atom count", "must be positive", index - 1)

    index = _expect_label(
        lines,
        index,
        "Atom types and atom numbers",
        path,
        configuration_number,
        "atom type counts",
    )
    elements: list[str] = []
    counts: list[int] = []
    for _ in range(n_types):
        value, index = _read_text_value(
            lines, index, path, configuration_number, "atom type counts"
        )
        fields = value.split()
        if len(fields) != 2:
            _raise(
                path,
                configuration_number,
                "atom type counts",
                "shape mismatch: expected element and count",
                index - 1,
            )
        try:
            count = int(fields[1])
        except ValueError:
            _raise(
                path,
                configuration_number,
                "atom type counts",
                f"invalid atom count {fields[1]!r}",
                index - 1,
            )
        if count < 0:
            _raise(path, configuration_number, "atom type counts", "counts must be non-negative", index - 1)
        elements.append(fields[0])
        counts.append(count)

    if sum(counts) != n_atoms:
        _raise(
            path,
            configuration_number,
            "atom type counts",
            f"sum {sum(counts)} does not equal atom count {n_atoms}",
            index - 1,
        )

    index = _expect_label(lines, index, "CTIFOR", path, configuration_number, "CTIFOR")
    _ctifor, index = _read_float_row(
        lines, index, 1, path, configuration_number, "CTIFOR"
    )

    index = _expect_label(
        lines,
        index,
        "Primitive lattice vectors (ang.)",
        path,
        configuration_number,
        "lattice",
    )
    lattice, index = _read_float_rows(
        lines, index, 3, 3, path, configuration_number, "lattice"
    )

    index = _expect_label(
        lines,
        index,
        "Atomic positions (ang.)",
        path,
        configuration_number,
        "positions",
    )
    positions, index = _read_float_rows(
        lines, index, n_atoms, 3, path, configuration_number, "positions"
    )

    index = _expect_label(
        lines, index, "Total energy (eV)", path, configuration_number, "energy"
    )
    energy_row, index = _read_float_row(
        lines, index, 1, path, configuration_number, "energy"
    )

    index = _expect_label(
        lines,
        index,
        "Forces (eV ang.^-1)",
        path,
        configuration_number,
        "forces",
    )
    forces, index = _read_float_rows(
        lines, index, n_atoms, 3, path, configuration_number, "forces"
    )

    index = _expect_label(
        lines, index, "Stress (kbar)", path, configuration_number, "stress"
    )
    index = _expect_label(
        lines, index, "XX YY ZZ", path, configuration_number, "stress"
    )
    stress_xx_yy_zz, index = _read_float_row(
        lines, index, 3, path, configuration_number, "stress"
    )
    index = _expect_label(
        lines, index, "XY YZ ZX", path, configuration_number, "stress"
    )
    stress_xy_yz_zx, index = _read_float_row(
        lines, index, 3, path, configuration_number, "stress"
    )

    stress = (
        stress_xx_yy_zz[0],
        stress_xx_yy_zz[1],
        stress_xx_yy_zz[2],
        stress_xy_yz_zx[0],
        stress_xy_yz_zx[1],
        stress_xy_yz_zx[2],
    )
    configuration = MlabConfiguration(
        source_path=path,
        source_configuration_number=configuration_number,
        source_line=marker_index + 1,
        elements=tuple(elements),
        counts=tuple(counts),
        n_atoms=n_atoms,
        lattice=(
            (lattice[0][0], lattice[0][1], lattice[0][2]),
            (lattice[1][0], lattice[1][1], lattice[1][2]),
            (lattice[2][0], lattice[2][1], lattice[2][2]),
        ),
        positions=tuple(
            (row[0], row[1], row[2]) for row in positions
        ),
        energy=energy_row[0],
        forces=tuple(
            (row[0], row[1], row[2]) for row in forces
        ),
        stress_kbar=stress,
    )
    return configuration, index


def _error_reaches_eof(error: MlabParseError, lines: Sequence[str]) -> bool:
    if error.line_number is None:
        return True
    return _next_content(lines, error.line_number) >= len(lines)


def _is_tail_partial_error(error: MlabParseError, lines: Sequence[str]) -> bool:
    if not _error_reaches_eof(error, lines):
        return False
    if "unexpected end of file" in error.reason or error.reason.startswith("missing "):
        return True
    if not error.reason.startswith("shape mismatch:"):
        return False
    match = re.search(r"expected (\d+) values, got (\d+)", error.reason)
    return match is not None and int(match.group(2)) < int(match.group(1))


def _reclassified_error(error: MlabParseError, reason: str) -> MlabParseError:
    return MlabParseError(
        error.path,
        configuration_number=error.configuration_number,
        block=error.block,
        reason=reason,
        line_number=error.line_number,
    )


def _identity_error(configuration: MlabConfiguration | None, reason: str) -> None:
    path = configuration.source_path if configuration is not None else Path("<memory>")
    number = configuration.source_configuration_number if configuration is not None else None
    raise MlabParseError(
        path,
        configuration_number=number,
        block="identity",
        reason=reason,
    )


def _uint64(value: int) -> bytes:
    if not isinstance(value, int) or value < 0 or value > 2**64 - 1:
        raise ValueError(f"integer outside unsigned 64-bit range: {value!r}")
    return struct.pack(">Q", value)


def _float64(value: float, configuration: MlabConfiguration) -> bytes:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        _identity_error(configuration, f"invalid float value {value!r}")
        raise AssertionError("unreachable") from exc
    if not math.isfinite(number):
        _identity_error(configuration, f"non-finite float value {value!r}")
    if number == 0.0:
        number = 0.0
    return struct.pack(">d", number)


def _validated_configuration(configuration: MlabConfiguration) -> None:
    if not isinstance(configuration.n_atoms, int) or configuration.n_atoms <= 0:
        _identity_error(configuration, "atom count must be positive")
    if len(configuration.elements) != len(configuration.counts):
        _identity_error(configuration, "element/count shape mismatch")
    if sum(configuration.counts) != configuration.n_atoms:
        _identity_error(configuration, "element/count sum does not equal atom count")
    for element, count in zip(configuration.elements, configuration.counts):
        if not isinstance(element, str) or not element:
            _identity_error(configuration, "element symbols must be non-empty strings")
        if not isinstance(count, int) or count < 0:
            _identity_error(configuration, "atom counts must be non-negative integers")

    if len(configuration.lattice) != 3 or any(len(row) != 3 for row in configuration.lattice):
        _identity_error(configuration, "lattice shape mismatch")
    if len(configuration.positions) != configuration.n_atoms or any(
        len(row) != 3 for row in configuration.positions
    ):
        _identity_error(configuration, "positions shape mismatch")
    if len(configuration.forces) != configuration.n_atoms or any(
        len(row) != 3 for row in configuration.forces
    ):
        _identity_error(configuration, "forces shape mismatch")
    if len(configuration.stress_kbar) != 6:
        _identity_error(configuration, "stress shape mismatch")

    _float64(configuration.energy, configuration)
    for rows in (configuration.lattice, configuration.positions, configuration.forces):
        for row in rows:
            for value in row:
                _float64(value, configuration)
    for value in configuration.stress_kbar:
        _float64(value, configuration)


def _canonical_chunks(configuration: MlabConfiguration):
    _validated_configuration(configuration)

    yield _uint64(len(configuration.elements))
    for element, count in zip(configuration.elements, configuration.counts):
        encoded = element.encode("utf-8")
        yield _uint64(len(encoded))
        yield encoded
        yield _uint64(count)

    yield _uint64(configuration.n_atoms)

    yield _uint64(len(configuration.lattice))
    yield _uint64(3)
    for row in configuration.lattice:
        for value in row:
            yield _float64(value, configuration)

    yield _uint64(len(configuration.positions))
    yield _uint64(3)
    for row in configuration.positions:
        for value in row:
            yield _float64(value, configuration)

    yield _float64(configuration.energy, configuration)

    yield _uint64(len(configuration.forces))
    yield _uint64(3)
    for row in configuration.forces:
        for value in row:
            yield _float64(value, configuration)

    yield _uint64(len(configuration.stress_kbar))
    for value in configuration.stress_kbar:
        yield _float64(value, configuration)


def canonical_configuration_bytes(configuration: MlabConfiguration) -> bytes:
    """Return the v1 canonical scientific byte stream for one configuration."""

    return b"".join(_canonical_chunks(configuration))


def update_canonical_configuration(digest, configuration: MlabConfiguration) -> None:
    """Feed one configuration's canonical fields into an existing hash object."""

    for chunk in _canonical_chunks(configuration):
        digest.update(chunk)


def _configurations_for_identity(
    source: MlabParseResult | Sequence[MlabConfiguration] | str | Path,
) -> tuple[tuple[MlabConfiguration, ...], Path | None]:
    if isinstance(source, (str, Path)):
        result = parse_mlab(source)
        return result.configurations, result.source_path
    if isinstance(source, MlabParseResult):
        return source.configurations, source.source_path
    return tuple(source), None


def config_identity(configuration: MlabConfiguration) -> MlabIdentity:
    digest = hashlib.sha256()
    update_canonical_configuration(digest, configuration)
    return MlabIdentity(schema="mlab-config-v1", sha256=digest.hexdigest())


def seed_prefix_identity(
    source: MlabParseResult | Sequence[MlabConfiguration] | str | Path,
    *,
    n_configurations: int | None = None,
) -> MlabIdentity:
    configurations, source_path = _configurations_for_identity(source)
    count = len(configurations) if n_configurations is None else n_configurations
    if not isinstance(count, int) or count < 0:
        raise MlabParseError(
            source_path or Path("<memory>"),
            configuration_number=None,
            block="identity",
            reason="invalid prefix count",
        )
    if count > len(configurations):
        raise MlabParseError(
            source_path or Path("<memory>"),
            configuration_number=None,
            block="identity",
            reason=(
                f"short source: requested {count} configurations, "
                f"only {len(configurations)} complete configurations available"
            ),
        )

    digest = hashlib.sha256()
    for configuration in configurations[:count]:
        update_canonical_configuration(digest, configuration)
    return MlabIdentity(schema="mlab-seed-v1", sha256=digest.hexdigest())


configuration_identity = config_identity


def seed_prefix_digest(
    source: MlabParseResult | Sequence[MlabConfiguration] | str | Path,
    *,
    n_configurations: int | None = None,
) -> str:
    return seed_prefix_identity(source, n_configurations=n_configurations).sha256


def parse_mlab(path: str | Path) -> MlabParseResult:
    """Parse and validate a complete ML_AB/ML_ABN source."""

    source_path = Path(path)
    try:
        text = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise MlabParseError(
            source_path,
            configuration_number=None,
            block="file",
            reason=f"invalid UTF-8: {exc}",
        ) from exc

    lines = text.splitlines()
    configuration_markers = [
        index
        for index, line in enumerate(lines)
        if _configuration_number(line) is not None
    ]
    if not configuration_markers:
        _raise(source_path, None, "header", "no configuration marker", None)

    declared_count = _parse_header(lines, source_path, configuration_markers[0])
    configurations: list[MlabConfiguration] = []
    for marker_position, marker_index in enumerate(configuration_markers):
        try:
            configuration, end_index = _parse_configuration(
                lines, marker_index, source_path
            )
        except MlabParseError as error:
            is_final_marker = marker_position == len(configuration_markers) - 1
            if is_final_marker and _is_tail_partial_error(error, lines):
                if not configurations:
                    raise _reclassified_error(
                        error,
                        f"first configuration incomplete: {error.reason}",
                    ) from error
                if declared_count == len(configurations) + 1:
                    return MlabParseResult(
                        source_path=source_path,
                        status="partial",
                        declared_count=declared_count,
                        configurations=tuple(configurations),
                        discarded_configuration_number=error.configuration_number,
                        discarded_block=error.block,
                        discarded_reason=error.reason,
                    )
                if declared_count == len(configurations):
                    raise _reclassified_error(
                        error,
                        f"incomplete tail with declared count {declared_count} "
                        f"equal to complete count {len(configurations)}",
                    ) from error
                raise MlabParseError(
                    source_path,
                    configuration_number=None,
                    block="header",
                    reason=(
                        f"declared count {declared_count} does not equal "
                        f"complete count plus one {len(configurations) + 1}"
                    ),
                ) from error
            if marker_position < len(configuration_markers) - 1:
                raise _reclassified_error(
                    error,
                    f"internal corruption: {error.reason}",
                ) from error
            raise

        configurations.append(configuration)
        next_content = _next_content(lines, end_index)
        if marker_position + 1 < len(configuration_markers):
            expected_next = configuration_markers[marker_position + 1]
            if next_content != expected_next:
                _raise(
                    source_path,
                    configuration.source_configuration_number,
                    "configuration",
                    "internal corruption: unexpected content before next configuration",
                    next_content,
                )
        elif next_content < len(lines):
            _raise(
                source_path,
                configuration.source_configuration_number,
                "configuration",
                f"unexpected content {lines[next_content].strip()!r}",
                next_content,
            )

    if len(configurations) != declared_count:
        _raise(
            source_path,
            None,
            "header",
            f"declared count {declared_count} does not equal complete count "
            f"{len(configurations)}",
            None,
        )

    return MlabParseResult(
        source_path=source_path,
        status="complete",
        declared_count=declared_count,
        configurations=tuple(configurations),
    )


parse_ml_ab = parse_mlab
