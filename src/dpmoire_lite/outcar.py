from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ase.io.vasp import iread_vasp_out, read_vasp_out

from .config import DEFAULT_OUTCAR_PATTERNS


@dataclass(frozen=True)
class OutcarSelection:
    path: Path
    pattern: str
    pattern_index: int
    order: int

    def __fspath__(self) -> str:
        return str(self.path)


def _natural_name_key(name: str):
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"(\d+)", name)
    )


def find_outcar_series(
    directory: Path,
    patterns: Iterable[str] = DEFAULT_OUTCAR_PATTERNS,
) -> tuple[OutcarSelection, ...]:
    compiled = [(pattern, re.compile(pattern)) for pattern in patterns]
    families: list[list[Path]] = [[] for _pattern, _compiled_pattern in compiled]
    if not directory.exists() or not directory.is_dir():
        return ()
    for child in directory.iterdir():
        if not child.is_file():
            continue
        for pattern_index, (_pattern, compiled_pattern) in enumerate(compiled):
            if compiled_pattern.match(child.name):
                families[pattern_index].append(child)
                break

    ordered_paths: list[tuple[Path, str, int]] = []
    for pattern_index, ((pattern, _compiled_pattern), family) in enumerate(
        zip(compiled, families, strict=True)
    ):
        ordered_paths.extend(
            (path, pattern, pattern_index)
            for path in sorted(
                family,
                key=lambda path: (_natural_name_key(path.name), path.name),
            )
        )
    return tuple(
        OutcarSelection(
            path=path,
            pattern=pattern,
            pattern_index=pattern_index,
            order=order,
        )
        for order, (path, pattern, pattern_index) in enumerate(ordered_paths)
    )


@contextmanager
def open_outcar_frames(path: Path):
    with Path(path).open(
        "r",
        encoding="utf-8",
        errors="strict",
    ) as file_object:
        yield iread_vasp_out(file_object, index=":")


def _find_outcar_tail_evidence(path: Path, complete_count: int) -> int | None:
    """Find a structural ionic-step start after completed energy chunks."""
    energy_chunks = 0
    remaining_energy_lines = 0
    boundary_reached = complete_count == 0
    with Path(path).open("r", encoding="utf-8", errors="strict") as file_object:
        for line_number, line in enumerate(file_object, start=1):
            if not boundary_reached:
                if remaining_energy_lines:
                    remaining_energy_lines -= 1
                    if remaining_energy_lines == 0:
                        energy_chunks += 1
                        if energy_chunks >= complete_count:
                            boundary_reached = True
                    continue
                if "FREE ENERGIE" in line:
                    remaining_energy_lines = 4
                continue

            if "Iteration" in line or (
                "POSITION" in line and "TOTAL-FORCE" in line
            ):
                return line_number
    return None


def read_outcar_frames(path: Path):
    return read_vasp_out(str(path), ":")
