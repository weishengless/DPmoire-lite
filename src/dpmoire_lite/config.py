from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when a DPmoire-lite config is invalid."""


DEFAULT_OUTCAR_PATTERNS = (
    r"^OUTCAR$",
    r"^OUTCAR\d+$",
    r"^OUT\d+$",
    r"^out\d+$",
)

OLD_FIELD_NAMES = {
    "VASP_ML",
    "K-mesh",
    "POTCAR_dir",
    "DFT_script",
    "ENMAX",
    "OUTCAR_collect_freq",
}

REQUIRED_FIELDS = (
    "dft_script",
    "potcar_dir",
    "script_dir",
    "input_dir",
    "work_dir",
    "n_nodes",
    "stage",
    "submit",
    "auto_resub",
    "vasp_ml",
    "outcar_collect_freq",
    "do_relaxation",
    "init_mlff",
    "sc_rlx",
    "n_sectors",
    "sc",
    "d",
    "k_mesh",
    "encut_factor",
    "r_cut",
    "symm_reduce",
    "twist_val",
    "min_val_n",
    "max_val_n",
    "include_monolayer_md",
)


def normalize_pair(value: Any, field: str) -> tuple[int, int]:
    if isinstance(value, bool):
        raise ConfigError(f"{field} must be a positive integer or a two-element list")
    if isinstance(value, int):
        if value <= 0:
            raise ConfigError(f"{field} values must be positive")
        return value, value
    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise ConfigError(f"{field} must be a positive integer or a two-element list")
        x, y = value
        if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int) or not isinstance(y, int):
            raise ConfigError(f"{field} values must be integers")
        if x <= 0 or y <= 0:
            raise ConfigError(f"{field} values must be positive")
        return x, y
    raise ConfigError(f"{field} must be a positive integer or a two-element list")


@dataclass(frozen=True)
class DPmoireLiteConfig:
    config_path: Path
    dft_script: str
    potcar_dir: Path
    script_dir: Path
    input_dir: Path
    work_dir: Path
    n_nodes: int
    stage: int | str
    submit: bool
    auto_resub: bool
    vasp_ml: bool
    outcar_collect_freq: int
    do_relaxation: bool
    init_mlff: bool
    sc_rlx: bool
    n_sectors: tuple[int, int]
    sc: tuple[int, int]
    d: float
    k_mesh: int
    encut_factor: float
    r_cut: float
    symm_reduce: bool
    twist_val: bool
    min_val_n: int
    max_val_n: int
    include_monolayer_md: bool
    outcar_patterns: tuple[str, ...] = field(default_factory=lambda: DEFAULT_OUTCAR_PATTERNS)

    @property
    def sc_x(self) -> int:
        return self.sc[0]

    @property
    def sc_y(self) -> int:
        return self.sc[1]

    @property
    def n_sector_x(self) -> int:
        return self.n_sectors[0]

    @property
    def n_sector_y(self) -> int:
        return self.n_sectors[1]

    def validate_build_mode(self, wait: bool) -> None:
        if self.stage == "all" and (not self.submit or not wait):
            raise ConfigError("stage: all requires submit: true and DPmoireLite build ... --wait")


def _require(data: dict[str, Any], field_name: str) -> Any:
    if field_name not in data:
        raise ConfigError(f"Missing required config field: {field_name}")
    return data[field_name]


def _resolve_path(base: Path, value: Any) -> Path:
    path = Path(str(value))
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{field_name} must be positive")
    result = int(value)
    if result <= 0:
        raise ConfigError(f"{field_name} must be positive")
    return result


def _normalize_stage(value: Any) -> int | str:
    if isinstance(value, bool):
        raise ConfigError("stage must be 0, 1, or all")
    if value in ("0", "1"):
        value = int(value)
    if value not in (0, 1, "all"):
        raise ConfigError("stage must be 0, 1, or all")
    return value


def load_config(path: Path) -> DPmoireLiteConfig:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ConfigError("Config file must contain a YAML mapping")

    old_keys = sorted(set(raw).intersection(OLD_FIELD_NAMES))
    if old_keys:
        raise ConfigError(f"Use snake_case config fields instead of old names: {', '.join(old_keys)}")

    missing = [field_name for field_name in REQUIRED_FIELDS if field_name not in raw]
    if missing:
        raise ConfigError(f"Missing required config field: {missing[0]}")

    base = config_path.parent
    outcar_patterns = raw.get("outcar_patterns", DEFAULT_OUTCAR_PATTERNS)

    return DPmoireLiteConfig(
        config_path=config_path,
        dft_script=str(_require(raw, "dft_script")),
        potcar_dir=_resolve_path(base, _require(raw, "potcar_dir")),
        script_dir=_resolve_path(base, _require(raw, "script_dir")),
        input_dir=_resolve_path(base, _require(raw, "input_dir")),
        work_dir=_resolve_path(base, _require(raw, "work_dir")),
        n_nodes=_positive_int(_require(raw, "n_nodes"), "n_nodes"),
        stage=_normalize_stage(_require(raw, "stage")),
        submit=bool(_require(raw, "submit")),
        auto_resub=bool(_require(raw, "auto_resub")),
        vasp_ml=bool(_require(raw, "vasp_ml")),
        outcar_collect_freq=_positive_int(_require(raw, "outcar_collect_freq"), "outcar_collect_freq"),
        do_relaxation=bool(_require(raw, "do_relaxation")),
        init_mlff=bool(_require(raw, "init_mlff")),
        sc_rlx=bool(_require(raw, "sc_rlx")),
        n_sectors=normalize_pair(_require(raw, "n_sectors"), field="n_sectors"),
        sc=normalize_pair(_require(raw, "sc"), field="sc"),
        d=float(_require(raw, "d")),
        k_mesh=int(_require(raw, "k_mesh")),
        encut_factor=float(_require(raw, "encut_factor")),
        r_cut=float(_require(raw, "r_cut")),
        symm_reduce=bool(_require(raw, "symm_reduce")),
        twist_val=bool(_require(raw, "twist_val")),
        min_val_n=int(_require(raw, "min_val_n")),
        max_val_n=int(_require(raw, "max_val_n")),
        include_monolayer_md=bool(_require(raw, "include_monolayer_md")),
        outcar_patterns=tuple(outcar_patterns),
    )
