from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any
import warnings

from ase.data import atomic_numbers
import yaml


class ConfigError(ValueError):
    """Raised when a DPmoire-lite config is invalid."""


DEFAULT_OUTCAR_PATTERNS = (
    r"^OUTCAR\d+$",
    r"^OUT\d+$",
    r"^out\d+$",
    r"^OUTCAR$",
)

OUTCAR_PATTERNS_SHAPE_ERROR = (
    "outcar_patterns must be a non-empty YAML list of non-empty regex strings"
)
OUTCAR_PATTERNS_EXAMPLE = (
    "outcar_patterns:\n"
    "  - '^OUTCAR\\d+$'\n"
    "  - '^OUT\\d+$'\n"
    "  - '^out\\d+$'\n"
    "  - '^OUTCAR$'"
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

VALID_D_MODES = {"surface_gap", "reference_plane_gap"}
VALID_INIT_MLFF_MODES = {"manual", "single-job"}
VALID_POTCAR_POLICIES = {"recommend", "minimal"}


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


def normalize_d_mode(value: Any) -> str:
    mode = str(value).strip()
    if mode not in VALID_D_MODES:
        allowed = ", ".join(sorted(VALID_D_MODES))
        raise ConfigError(f"d_mode must be one of: {allowed}")
    return mode


def normalize_potcar_policy(value: Any) -> str:
    policy = str(value).strip()
    if policy not in VALID_POTCAR_POLICIES:
        allowed = ", ".join(sorted(VALID_POTCAR_POLICIES))
        raise ConfigError(f"potcar_policy must be one of: {allowed}")
    return policy


def normalize_init_mlff_mode(value: Any) -> str:
    mode = str(value).strip()
    if mode not in VALID_INIT_MLFF_MODES:
        allowed = ", ".join(sorted(VALID_INIT_MLFF_MODES))
        raise ConfigError(f"init_mlff_mode must be one of: {allowed}")
    return mode


def _normalize_element_symbol(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{field} must contain element symbols")
    text = value.strip()
    if not text:
        raise ConfigError(f"{field} must contain at least one non-empty element symbol")
    if text not in atomic_numbers or atomic_numbers[text] == 0:
        raise ConfigError(f"{field} contains invalid element symbol: {text}")
    return text


def normalize_reference_selector(value: Any, field: str) -> str | tuple[str, ...]:
    if value is None:
        return "all"
    if isinstance(value, str):
        text = value.strip()
        if text == "all":
            return "all"
        return (_normalize_element_symbol(text, field),)
    if isinstance(value, (list, tuple)):
        if not value:
            raise ConfigError(f"{field} must contain at least one non-empty element symbol")
        items = tuple(_normalize_element_symbol(item, field) for item in value)
        return items
    raise ConfigError(f"{field} must be 'all', an element symbol, or a list of element symbols")


def normalize_d_reference(value: Any) -> dict[str, str | tuple[str, ...]] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError("d_reference must be a mapping with optional top and bot keys")
    allowed = {"top", "bot"}
    extra = sorted(set(value) - allowed)
    if extra:
        raise ConfigError(f"d_reference has unknown keys: {', '.join(extra)}")
    return {
        "top": normalize_reference_selector(value.get("top"), "d_reference.top"),
        "bot": normalize_reference_selector(value.get("bot"), "d_reference.bot"),
    }


def _normalize_outcar_patterns(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        message = OUTCAR_PATTERNS_SHAPE_ERROR
        if isinstance(value, str):
            message = f"{message}\nUse a YAML list, for example:\n{OUTCAR_PATTERNS_EXAMPLE}"
        raise ConfigError(message)

    patterns: list[str] = []
    first_indices: dict[str, int] = {}
    for index, pattern in enumerate(value):
        if not isinstance(pattern, str) or pattern == "":
            raise ConfigError(f"outcar_patterns[{index}] must be a non-empty string")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(
                f"Invalid outcar_patterns[{index}] {pattern!r}: {exc}"
            ) from exc
        if pattern in first_indices:
            warnings.warn(
                f"Duplicate outcar_patterns[{index}] {pattern!r} ignored; "
                f"first occurrence is outcar_patterns[{first_indices[pattern]}].",
                UserWarning,
                stacklevel=2,
            )
            continue
        first_indices[pattern] = index
        patterns.append(pattern)
    return tuple(patterns)


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
    d_mode: str
    d_reference: dict[str, str | tuple[str, ...]] | None
    potcar_policy: str
    k_mesh: int
    encut_factor: float
    r_cut: float
    symm_reduce: bool
    twist_val: bool
    min_val_n: int
    max_val_n: int
    include_monolayer_md: bool
    preserve_grid_shift_md: bool = False
    init_mlff_mode: str = "manual"
    init_bottom_incar: Path | None = None
    init_top_incar: Path | None = None
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
        if self.stage == "all" or (self.submit and wait):
            raise ConfigError(
                "stage: all and submitted --wait workflows are temporarily disabled because "
                "Slurm terminal-state validation and failure propagation are not yet reliable. "
                "Generate stages with submit: false and submit them manually."
            )


def _require(data: dict[str, Any], field_name: str) -> Any:
    if field_name not in data:
        raise ConfigError(f"Missing required config field: {field_name}")
    return data[field_name]


def _resolve_path(base: Path, value: Any) -> Path:
    path = Path(str(value))
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _resolve_optional_input_path(
    input_dir: Path,
    value: Any,
    field_name: str,
) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field_name} must be a non-empty path relative to input_dir")
    raw_path = Path(value.strip())
    if raw_path.is_absolute():
        raise ConfigError(f"{field_name} must be relative to input_dir")
    resolved = (input_dir / raw_path).resolve()
    try:
        resolved.relative_to(input_dir)
    except ValueError as exc:
        raise ConfigError(f"{field_name} must remain inside input_dir") from exc
    return resolved


def _bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    raise ConfigError(f"{field_name} must be a boolean")


def _int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{field_name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as exc:
            raise ConfigError(f"{field_name} must be an integer") from exc
    raise ConfigError(f"{field_name} must be an integer")


def _positive_int(value: Any, field_name: str) -> int:
    result = _int(value, field_name)
    if result <= 0:
        raise ConfigError(f"{field_name} must be positive")
    return result


def _float(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{field_name} must be a number")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError as exc:
            raise ConfigError(f"{field_name} must be a number") from exc
    raise ConfigError(f"{field_name} must be a number")


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
    if "outcar_patterns" in raw:
        outcar_patterns = _normalize_outcar_patterns(raw["outcar_patterns"])
    else:
        outcar_patterns = _normalize_outcar_patterns(list(DEFAULT_OUTCAR_PATTERNS))
    d_mode = normalize_d_mode(raw.get("d_mode", "surface_gap"))
    d_reference = normalize_d_reference(raw.get("d_reference")) if d_mode == "reference_plane_gap" else None
    potcar_policy = normalize_potcar_policy(raw.get("potcar_policy", "recommend"))
    input_dir = _resolve_path(base, _require(raw, "input_dir"))
    init_mlff = _bool(_require(raw, "init_mlff"), "init_mlff")
    init_mlff_mode = normalize_init_mlff_mode(raw.get("init_mlff_mode", "manual"))
    init_bottom_incar = _resolve_optional_input_path(
        input_dir,
        raw.get("init_bottom_incar"),
        "init_bottom_incar",
    )
    init_top_incar = _resolve_optional_input_path(
        input_dir,
        raw.get("init_top_incar"),
        "init_top_incar",
    )
    if init_mlff_mode == "single-job":
        if not init_mlff:
            raise ConfigError("init_mlff_mode: single-job requires init_mlff: true")
        missing_templates = [
            field_name
            for field_name, value in (
                ("init_bottom_incar", init_bottom_incar),
                ("init_top_incar", init_top_incar),
            )
            if value is None
        ]
        if missing_templates:
            raise ConfigError(
                "init_mlff_mode: single-job requires explicit scientific templates: "
                + ", ".join(missing_templates)
            )

    return DPmoireLiteConfig(
        config_path=config_path,
        dft_script=str(_require(raw, "dft_script")),
        potcar_dir=_resolve_path(base, _require(raw, "potcar_dir")),
        script_dir=_resolve_path(base, _require(raw, "script_dir")),
        input_dir=input_dir,
        work_dir=_resolve_path(base, _require(raw, "work_dir")),
        n_nodes=_positive_int(_require(raw, "n_nodes"), "n_nodes"),
        stage=_normalize_stage(_require(raw, "stage")),
        submit=_bool(_require(raw, "submit"), "submit"),
        auto_resub=_bool(_require(raw, "auto_resub"), "auto_resub"),
        vasp_ml=_bool(_require(raw, "vasp_ml"), "vasp_ml"),
        outcar_collect_freq=_positive_int(_require(raw, "outcar_collect_freq"), "outcar_collect_freq"),
        do_relaxation=_bool(_require(raw, "do_relaxation"), "do_relaxation"),
        init_mlff=init_mlff,
        sc_rlx=_bool(_require(raw, "sc_rlx"), "sc_rlx"),
        n_sectors=normalize_pair(_require(raw, "n_sectors"), field="n_sectors"),
        sc=normalize_pair(_require(raw, "sc"), field="sc"),
        d=_float(_require(raw, "d"), "d"),
        d_mode=d_mode,
        d_reference=d_reference,
        potcar_policy=potcar_policy,
        k_mesh=_int(_require(raw, "k_mesh"), "k_mesh"),
        encut_factor=_float(_require(raw, "encut_factor"), "encut_factor"),
        r_cut=_float(_require(raw, "r_cut"), "r_cut"),
        symm_reduce=_bool(_require(raw, "symm_reduce"), "symm_reduce"),
        twist_val=_bool(_require(raw, "twist_val"), "twist_val"),
        min_val_n=_int(_require(raw, "min_val_n"), "min_val_n"),
        max_val_n=_int(_require(raw, "max_val_n"), "max_val_n"),
        include_monolayer_md=_bool(_require(raw, "include_monolayer_md"), "include_monolayer_md"),
        preserve_grid_shift_md=_bool(raw.get("preserve_grid_shift_md", False), "preserve_grid_shift_md"),
        init_mlff_mode=init_mlff_mode,
        init_bottom_incar=init_bottom_incar,
        init_top_incar=init_top_incar,
        outcar_patterns=outcar_patterns,
    )
