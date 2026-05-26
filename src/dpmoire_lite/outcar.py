from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from ase.io.vasp import read_vasp_out

from .config import DEFAULT_OUTCAR_PATTERNS


def find_outcar_series(directory: Path, patterns: Iterable[str] = DEFAULT_OUTCAR_PATTERNS) -> list[Path]:
    compiled = [re.compile(pattern) for pattern in patterns]
    matches: list[Path] = []
    if not directory.exists():
        return matches
    for child in directory.iterdir():
        if not child.is_file():
            continue
        if any(pattern.match(child.name) for pattern in compiled):
            matches.append(child)
    return sorted(matches, key=lambda path: (path.stat().st_mtime, path.name))


def read_outcar_frames(path: Path):
    return read_vasp_out(str(path), ":")
