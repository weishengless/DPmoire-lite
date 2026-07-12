from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import warnings as pywarnings

import numpy as np
from ase.io.vasp import read_vasp

from .config import DPmoireLiteConfig
from .incar import IncarAnalysis, parse_incar
from .inputs import get_ordered_elements, read_enmax, resolve_potcar_dir
from .mlab import MlabParseResult, parse_mlab
from .structures import StructureHandler, generate_stackings, validate_cartesian_z_slab_cell


VASP_RELAXATION_CONVERGED_PHRASE = (
    "reached required accuracy - stopping structural energy minimisation"
)


@dataclass(frozen=True)
class PreflightDiagnostic:
    domain: str
    path: Path
    reason: str
    tag: str | None = None
    block: str | None = None

    def format(self) -> str:
        context = []
        if self.tag is not None:
            context.append(f"tag={self.tag}")
        if self.block is not None:
            context.append(f"block={self.block}")
        suffix = f" ({', '.join(context)})" if context else ""
        return f"[{self.domain}] {self.path.as_posix()}{suffix}: {self.reason}"


@dataclass(frozen=True)
class BuildPreflightResult:
    stage: int | str
    structures: StructureHandler | None
    stackings: tuple[tuple[int, int], ...] = ()
    rcut: float | None = None
    templates: dict[str, IncarAnalysis] = field(default_factory=dict)
    potcar_sources: dict[str, Path] = field(default_factory=dict)
    initial_seed: MlabParseResult | None = None
    warnings: tuple[PreflightDiagnostic, ...] = ()


def preflight_stage0(config: DPmoireLiteConfig) -> BuildPreflightResult:
    diagnostics: list[PreflightDiagnostic] = []
    warning_diagnostics: list[PreflightDiagnostic] = []
    templates = _validate_templates(
        _stage0_templates(config),
        config,
        diagnostics,
        warning_diagnostics,
    )
    _validate_submit_script(config, diagnostics)

    structures = _read_structures(config, diagnostics)
    rcut = None
    potcar_sources: dict[str, Path] = {}
    if structures is not None:
        try:
            rcut = _resolve_rcut(config, structures)
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="structure",
                    path=config.input_dir,
                    reason=str(exc),
                )
            )

        elements = get_ordered_elements(structures.new_struct)
        potcar_sources = _validate_potcars(config, elements, diagnostics)

    stackings = tuple(generate_stackings(config.n_sectors))
    _raise_if_failed(diagnostics)
    _emit_warnings(warning_diagnostics)
    return BuildPreflightResult(
        stage=config.stage,
        structures=structures,
        stackings=stackings,
        rcut=rcut,
        templates=templates,
        potcar_sources=potcar_sources,
        warnings=tuple(warning_diagnostics),
    )


def preflight_stage1(
    config: DPmoireLiteConfig,
    stackings: list[tuple[int, int]],
) -> BuildPreflightResult:
    diagnostics: list[PreflightDiagnostic] = []
    warning_diagnostics: list[PreflightDiagnostic] = []
    templates = _validate_templates(
        _stage1_templates(config),
        config,
        diagnostics,
        warning_diagnostics,
    )
    _validate_submit_script(config, diagnostics)

    structures = _read_structures(config, diagnostics)
    rcut = None
    potcar_sources: dict[str, Path] = {}
    if structures is not None:
        try:
            rcut = _resolve_rcut(config, structures)
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="structure",
                    path=config.input_dir,
                    reason=str(exc),
                )
            )
        elements = get_ordered_elements(structures.new_struct)
        potcar_sources = _validate_potcars(config, elements, diagnostics)

    for stacking in stackings:
        _validate_relaxation(config.work_dir / "rlx" / f"{stacking[0]}_{stacking[1]}", diagnostics)

    initial_seed = None
    if config.vasp_ml:
        initial_seed = _validate_initial_seed(config, diagnostics)

    _raise_if_failed(diagnostics)
    _emit_warnings(warning_diagnostics)
    return BuildPreflightResult(
        stage=config.stage,
        structures=structures,
        stackings=tuple(stackings),
        rcut=rcut,
        templates=templates,
        potcar_sources=potcar_sources,
        initial_seed=initial_seed,
        warnings=tuple(warning_diagnostics),
    )


def _stage0_templates(config: DPmoireLiteConfig) -> list[tuple[str, Path]]:
    templates = []
    if config.init_mlff:
        templates.append(("init_INCAR", config.input_dir / "init_INCAR"))
    if config.do_relaxation:
        templates.append(("rlx_INCAR", config.input_dir / "rlx_INCAR"))
    if config.twist_val:
        templates.append(("val_INCAR", config.input_dir / "val_INCAR"))
    return templates


def _stage1_templates(config: DPmoireLiteConfig) -> list[tuple[str, Path]]:
    templates = [("MD_INCAR", config.input_dir / "MD_INCAR")]
    if config.include_monolayer_md:
        templates.append(("MD_monolayer_INCAR", config.input_dir / "MD_monolayer_INCAR"))
    return templates


def _validate_templates(
    templates: list[tuple[str, Path]],
    config: DPmoireLiteConfig,
    diagnostics: list[PreflightDiagnostic],
    warning_diagnostics: list[PreflightDiagnostic],
) -> dict[str, IncarAnalysis]:
    del config
    analyses: dict[str, IncarAnalysis] = {}
    for name, path in templates:
        if not path.is_file():
            diagnostics.append(
                PreflightDiagnostic(
                    domain="template",
                    path=path,
                    reason=f"missing required template {name}",
                )
            )
            continue

        try:
            analysis = parse_incar(
                path.read_text(encoding="utf-8"),
                source_name=str(path),
            ).analyze()
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="incar",
                    path=path,
                    reason=str(exc),
                )
            )
            continue

        analyses[name] = analysis
        for duplicate in analysis.blocking_conflicts:
            values = ", ".join(
                f"line {assignment.location.line}: {assignment.value}"
                for assignment in duplicate.assignments
            )
            diagnostics.append(
                PreflightDiagnostic(
                    domain="incar",
                    path=path,
                    tag=duplicate.tag,
                    reason=f"conflicting definitions: {values}",
                )
            )

        for duplicate in analysis.repairable_warnings:
            warning_diagnostics.append(
                PreflightDiagnostic(
                    domain="incar",
                    path=path,
                    tag=duplicate.tag,
                    reason="repairable duplicate will be normalized during generation",
                )
            )

        luse_vdw_conflict = any(
            duplicate.tag == "LUSE_VDW" for duplicate in analysis.blocking_conflicts
        )
        if not luse_vdw_conflict and analysis.is_effectively_true("LUSE_VDW"):
            kernel = path.parent / "vdw_kernel.bindat"
            if not kernel.is_file():
                diagnostics.append(
                    PreflightDiagnostic(
                        domain="vdw",
                        path=kernel,
                        reason=f"required by {path}",
                    )
                )

    return analyses


def _validate_submit_script(
    config: DPmoireLiteConfig,
    diagnostics: list[PreflightDiagnostic],
) -> None:
    path = config.script_dir / config.dft_script
    if not path.is_file():
        diagnostics.append(
            PreflightDiagnostic(
                domain="script",
                path=path,
                reason="required submit script is missing",
            )
        )


def _read_structures(
    config: DPmoireLiteConfig,
    diagnostics: list[PreflightDiagnostic],
) -> StructureHandler | None:
    reader = object.__new__(StructureHandler)
    input_atoms = {}
    for name in ("top_layer", "bot_layer"):
        path = config.input_dir / f"{name}.poscar"
        try:
            input_atoms[name] = reader.read_atoms(path)
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="structure",
                    path=path,
                    reason=str(exc),
                )
            )

    if len(input_atoms) != 2:
        return None

    for name, atoms in input_atoms.items():
        try:
            validate_cartesian_z_slab_cell(atoms, f"{name}.poscar")
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="structure",
                    path=config.input_dir / f"{name}.poscar",
                    reason=str(exc),
                )
            )

    try:
        return StructureHandler(
            config.input_dir,
            config.work_dir,
            config.n_sectors,
            config.d,
            config.d_mode,
            config.d_reference,
        )
    except Exception as exc:
        diagnostics.append(
            PreflightDiagnostic(
                domain="structure",
                path=config.input_dir,
                reason=str(exc),
            )
        )
        return None


def _resolve_rcut(config: DPmoireLiteConfig, structures: StructureHandler) -> float:
    if config.r_cut >= 0:
        return config.r_cut
    if structures.top_atoms is None or structures.bot_atoms is None:
        raise ValueError("Input layer cells are required to resolve default r_cut")
    max_layer_a = max(
        float(np.linalg.norm(atoms.cell.array[index]))
        for atoms in (structures.top_atoms, structures.bot_atoms)
        for index in (0, 1)
    )
    return float(np.sqrt(max_layer_a**2 + config.d**2) * 1.1)


def _validate_potcars(
    config: DPmoireLiteConfig,
    elements: list[str],
    diagnostics: list[PreflightDiagnostic],
) -> dict[str, Path]:
    sources: dict[str, Path] = {}
    for element in elements:
        try:
            source_dir = resolve_potcar_dir(
                element,
                config.potcar_dir,
                potcar_policy=config.potcar_policy,
            )
            read_enmax(source_dir / "POTCAR")
            sources[element] = source_dir
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="potcar",
                    path=config.potcar_dir / element / "POTCAR",
                    reason=str(exc),
                )
            )
    return sources


def _validate_relaxation(
    directory: Path,
    diagnostics: list[PreflightDiagnostic],
) -> None:
    display_directory = Path("rlx") / directory.name
    outcar = directory / "OUTCAR"
    if not outcar.is_file():
        diagnostics.append(
            PreflightDiagnostic(
                domain="relaxation",
                path=display_directory,
                reason="Missing OUTCAR",
            )
        )
    else:
        try:
            text = outcar.read_text(encoding="utf-8", errors="ignore")
            if VASP_RELAXATION_CONVERGED_PHRASE not in text:
                diagnostics.append(
                    PreflightDiagnostic(
                        domain="relaxation",
                        path=display_directory,
                        reason="Relaxation did not converge",
                    )
                )
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="relaxation",
                    path=display_directory,
                    reason=str(exc),
                )
            )

    contcar = directory / "CONTCAR"
    if not contcar.is_file():
        diagnostics.append(
            PreflightDiagnostic(
                domain="relaxation",
                path=display_directory,
                reason="Missing CONTCAR",
            )
        )
    else:
        try:
            read_vasp(contcar)
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="relaxation",
                    path=display_directory,
                    reason=f"CONTCAR is not readable by ASE: {exc}",
                )
            )


def _validate_initial_seed(
    config: DPmoireLiteConfig,
    diagnostics: list[PreflightDiagnostic],
) -> MlabParseResult | None:
    init_dir = config.work_dir / "init_mlff"
    seed_path = init_dir / "ML_ABN"
    force_field_path = init_dir / "ML_FFN"
    seed_display_path = Path("init_mlff") / "ML_ABN"
    force_field_display_path = Path("init_mlff") / "ML_FFN"
    if not seed_path.is_file():
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=seed_display_path,
                reason="missing required MLFF file",
            )
        )
    elif seed_path.stat().st_size == 0:
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=seed_display_path,
                reason="ML_ABN seed is empty",
            )
        )

    if not force_field_path.is_file():
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=force_field_display_path,
                reason="missing required MLFF file",
            )
        )
    elif force_field_path.stat().st_size == 0:
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=force_field_display_path,
                reason="ML_FFN file is empty",
            )
        )

    if not seed_path.is_file() or seed_path.stat().st_size == 0:
        return None
    try:
        return parse_mlab(seed_path)
    except Exception as exc:
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=seed_display_path,
                reason=str(exc),
            )
        )
        return None


def _raise_if_failed(diagnostics: list[PreflightDiagnostic]) -> None:
    if not diagnostics:
        return
    details = "\n".join(f"- {diagnostic.format()}" for diagnostic in diagnostics)
    raise RuntimeError(
        f"Build preflight failed:\n{details}\nNo calculation directories were generated."
    )


def _emit_warnings(diagnostics: list[PreflightDiagnostic]) -> None:
    for diagnostic in diagnostics:
        pywarnings.warn(diagnostic.format(), UserWarning, stacklevel=3)
