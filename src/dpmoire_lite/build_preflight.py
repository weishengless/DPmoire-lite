from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import warnings as pywarnings

import numpy as np
from ase import Atoms
from ase.build import make_supercell, sort
from ase.io.vasp import read_vasp

from .config import DPmoireLiteConfig
from .incar import IncarAnalysis, parse_incar
from .inputs import (
    PreparedIncarTemplate,
    PreparedPotcar,
    PreparedSource,
    get_ordered_elements,
    prepare_source,
    read_enmax,
    resolve_potcar_dir,
)
from .init_mlff import (
    InitMlffWorkflowError,
    validate_published_init_mlff_seed,
)
from .init_mlff_submit import (
    PreparedInitMlffSubmitAdapter,
    prepare_init_mlff_submit_adapter,
)
from .mlab import MlabParseResult, parse_mlab
from .manifest import ManifestReadResult, read_manifest
from .paths import relative_to_workdir, stage_dir
from . import provenance as provenance_module
from .structures import (
    StructureHandler,
    generate_stackings,
    supercell_matrix,
    validate_cartesian_z_slab_cell,
)


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
class PreparedRelaxation:
    stacking: tuple[int, int]
    atoms: Atoms


@dataclass(frozen=True)
class PreparedValidationStructure:
    angle: str
    atoms: Atoms


@dataclass(frozen=True)
class PreparedWorkflowCutoff:
    selected_potcars: tuple[PreparedPotcar, ...]
    governing_potcar: PreparedPotcar
    encut_factor: float
    encut: float

    @property
    def max_enmax(self) -> float:
        return self.governing_potcar.enmax

    def audit_record(self) -> dict[str, object]:
        governing = self.governing_potcar
        return {
            "schema": "dpmoire-lite.workflow-cutoff.v1",
            "selected_potcars": [
                {
                    "element": potcar.element,
                    "directory": potcar.source.path.parent.name,
                    "enmax": potcar.enmax,
                }
                for potcar in sorted(
                    self.selected_potcars,
                    key=lambda item: (item.element, item.source.path.parent.name),
                )
            ],
            "governing_element": governing.element,
            "governing_potcar_directory": governing.source.path.parent.name,
            "max_enmax": self.max_enmax,
            "encut_factor": self.encut_factor,
            "encut": self.encut,
        }


@dataclass(frozen=True)
class PreparedInitMlffPhase:
    name: str
    role: str
    initial_state: str
    target_dir: Path
    atoms: Atoms
    template: PreparedIncarTemplate


@dataclass(frozen=True)
class PreparedInitMlffWorkflow:
    mode: str
    initial_state: str
    root_dir: Path
    submit_adapter: PreparedInitMlffSubmitAdapter
    phases: tuple[PreparedInitMlffPhase, ...]


@dataclass(frozen=True)
class PreparedInitialSeed:
    parsed: MlabParseResult
    ml_ab: PreparedSource
    ml_ff: PreparedSource


@dataclass(frozen=True)
class BuildPreflightResult:
    stage: int | str
    structures: StructureHandler | None
    stackings: tuple[tuple[int, int], ...] = ()
    rcut: float | None = None
    templates: tuple[PreparedIncarTemplate, ...] = ()
    workflow_cutoff: PreparedWorkflowCutoff | None = None
    submit_script: PreparedSource | None = None
    stage_targets: tuple[tuple[str, Path], ...] = ()
    output_dirs: tuple[Path, ...] = ()
    initial_seed: PreparedInitialSeed | None = None
    relaxations: tuple[PreparedRelaxation, ...] = ()
    validation_structures: tuple[PreparedValidationStructure, ...] = ()
    init_mlff_workflow: PreparedInitMlffWorkflow | None = None
    warnings: tuple[PreflightDiagnostic, ...] = ()
    provenance: provenance_module.Stage1ProvenanceResult | None = None

    @property
    def potcars(self) -> tuple[PreparedPotcar, ...]:
        if self.workflow_cutoff is None:
            return ()
        return self.workflow_cutoff.selected_potcars


def _stage0_target_stages(
    config: DPmoireLiteConfig,
) -> tuple[tuple[str, Path], ...]:
    targets = []
    if config.init_mlff:
        targets.append(("init_mlff", stage_dir(config.work_dir, "init_mlff")))
    if config.do_relaxation:
        targets.append(("rlx", stage_dir(config.work_dir, "rlx")))
    if config.twist_val:
        targets.append(("validation", stage_dir(config.work_dir, "validation")))
    return tuple(targets)


def _stage1_target_stages(
    config: DPmoireLiteConfig,
) -> tuple[tuple[str, Path], ...]:
    return (("md", stage_dir(config.work_dir, "md")),)


def check_target_stages_absent(
    config: DPmoireLiteConfig,
    targets: tuple[tuple[str, Path], ...],
) -> None:
    conflicts = [
        (stage, path)
        for stage, path in targets
        if path.exists() or path.is_symlink()
    ]
    if not conflicts:
        return
    details = "\n".join(
        f"- {stage} ({path.relative_to(config.work_dir).as_posix()}) already exists"
        for stage, path in conflicts
    )
    raise RuntimeError(
        "Build target conflict(s):\n"
        f"{details}\n"
        "DPmoire-lite does not rebuild stages in place. "
        "Delete the listed stage directories and rerun the build. "
        "No files were modified."
    )


def _stage0_output_dirs(
    config: DPmoireLiteConfig,
    stackings: tuple[tuple[int, int], ...],
    validation_structures: tuple[PreparedValidationStructure, ...],
) -> tuple[Path, ...]:
    output_dirs = []
    if config.init_mlff:
        init_root = stage_dir(config.work_dir, "init_mlff")
        if config.init_mlff_mode == "single-job":
            output_dirs.extend((init_root / "bottom", init_root / "top"))
        else:
            output_dirs.append(init_root)
    if config.do_relaxation:
        relaxation_dir = stage_dir(config.work_dir, "rlx")
        output_dirs.extend(relaxation_dir / f"{i}_{j}" for i, j in stackings)
    if config.twist_val:
        validation_dir = stage_dir(config.work_dir, "validation")
        output_dirs.extend(
            validation_dir / record.angle for record in validation_structures
        )
    return tuple(output_dirs)


def _stage1_output_dirs(
    config: DPmoireLiteConfig,
    stackings: tuple[tuple[int, int], ...],
) -> tuple[Path, ...]:
    md_dir = stage_dir(config.work_dir, "md")
    output_dirs = [md_dir / f"{i}_{j}" for i, j in stackings]
    if config.include_monolayer_md:
        output_dirs.extend((md_dir / "top_layer", md_dir / "bot_layer"))
    return tuple(output_dirs)


def _validate_output_dirs(
    config: DPmoireLiteConfig,
    paths: tuple[Path, ...],
    diagnostics: list[PreflightDiagnostic],
) -> None:
    for path in paths:
        try:
            relative_to_workdir(config.work_dir, path)
        except ValueError:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="path",
                    path=path,
                    reason="validated output directory must remain inside work_dir",
                )
            )


def preflight_stage0(config: DPmoireLiteConfig) -> BuildPreflightResult:
    stage_targets = _stage0_target_stages(config)
    check_target_stages_absent(config, stage_targets)
    diagnostics: list[PreflightDiagnostic] = []
    warning_diagnostics: list[PreflightDiagnostic] = []
    templates = _validate_templates(
        _stage0_templates(config),
        config,
        diagnostics,
        warning_diagnostics,
    )
    submit_script = _validate_submit_script(config, diagnostics)

    structures = _read_structures(config, diagnostics)
    rcut = None
    workflow_cutoff = None
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
        potcars = _validate_potcars(config, elements, diagnostics)
        workflow_cutoff = _prepare_workflow_cutoff(config, potcars)

    stackings = tuple(generate_stackings(config.n_sectors))
    validation_structures: tuple[PreparedValidationStructure, ...] = ()
    if structures is not None:
        if config.symm_reduce:
            try:
                stackings = tuple(structures.find_sym_reduced_stackings())
            except Exception as exc:
                diagnostics.append(
                    PreflightDiagnostic(
                        domain="symmetry",
                        path=config.input_dir,
                        reason=str(exc),
                    )
                )
        if config.twist_val:
            try:
                angles, atoms_list = structures.make_twist_struct(
                    config.min_val_n,
                    config.max_val_n,
                )
                validation_structures = tuple(
                    PreparedValidationStructure(str(angle), atoms.copy())
                    for angle, atoms in zip(angles, atoms_list, strict=True)
                )
            except Exception as exc:
                diagnostics.append(
                    PreflightDiagnostic(
                        domain="structure",
                        path=config.input_dir,
                        reason=f"twist validation preparation failed: {exc}",
                    )
                )
    output_dirs = _stage0_output_dirs(config, stackings, validation_structures)
    init_mlff_workflow = _prepare_init_mlff_workflow(
        config,
        structures,
        templates,
        submit_script,
        output_dirs,
        diagnostics,
    )
    _validate_output_dirs(
        config,
        tuple(path for _stage, path in stage_targets) + output_dirs,
        diagnostics,
    )
    _raise_if_failed(diagnostics)
    _emit_warnings(warning_diagnostics)
    return BuildPreflightResult(
        stage=config.stage,
        structures=structures,
        stackings=stackings,
        rcut=rcut,
        templates=templates,
        workflow_cutoff=workflow_cutoff,
        submit_script=submit_script,
        stage_targets=stage_targets,
        output_dirs=output_dirs,
        init_mlff_workflow=init_mlff_workflow,
        warnings=tuple(warning_diagnostics),
        validation_structures=validation_structures,
    )


def _prepare_init_mlff_workflow(
    config: DPmoireLiteConfig,
    structures: StructureHandler | None,
    templates: tuple[PreparedIncarTemplate, ...],
    submit_script: PreparedSource | None,
    output_dirs: tuple[Path, ...],
    diagnostics: list[PreflightDiagnostic],
) -> PreparedInitMlffWorkflow | None:
    if config.init_mlff_mode != "single-job" or not config.init_mlff:
        return None
    if structures is None or structures.bot_atoms is None or structures.top_atoms is None:
        return None

    templates_by_name = {template.name: template for template in templates}
    bottom_template = templates_by_name.get("init_bottom_INCAR")
    top_template = templates_by_name.get("init_top_INCAR")
    if (
        bottom_template is None
        or top_template is None
        or submit_script is None
        or len(output_dirs) < 2
    ):
        return None

    if not _is_safe_basename(config.dft_script):
        diagnostics.append(
            PreflightDiagnostic(
                domain="script",
                path=submit_script.path,
                reason=(
                    "single-job init submit script must use a safe basename, "
                    "not a nested or absolute path"
                ),
            )
        )
        return None

    phase_templates = (
        ("bottom", bottom_template),
        ("top", top_template),
    )
    if _record_init_phase_filename_collisions(
        phase_templates,
        submit_script,
        diagnostics,
    ):
        return None

    root_dir = stage_dir(config.work_dir, "init_mlff")
    bottom_dir, top_dir = output_dirs[:2]
    if (bottom_dir, top_dir) != (root_dir / "bottom", root_dir / "top"):
        diagnostics.append(
            PreflightDiagnostic(
                domain="path",
                path=root_dir,
                reason="single-job init phase targets do not match the required layout",
            )
        )
        return None
    try:
        submit_adapter = prepare_init_mlff_submit_adapter(submit_script)
    except Exception as exc:
        diagnostics.append(
            PreflightDiagnostic(
                domain="script",
                path=submit_script.path,
                reason=str(exc),
            )
        )
        return None
    if config.submit and submit_adapter.sbatch_wait_requested:
        diagnostics.append(
            PreflightDiagnostic(
                domain="script",
                path=submit_script.path,
                reason=(
                    "single-job sbatch wait directives are incompatible with "
                    "fire-and-forget automatic submission"
                ),
            )
        )
        return None
    if config.submit and submit_adapter.translated_scheduler_prefixes:
        diagnostics.append(
            PreflightDiagnostic(
                domain="script",
                path=submit_script.path,
                reason=(
                    "single-job translated scheduler directives (#PBS/#BSUB) "
                    "are incompatible with automatic submission"
                ),
            )
        )
        return None
    try:
        bottom_atoms = sort(
            make_supercell(
                prim=structures.bot_atoms.copy(),
                P=supercell_matrix(config.sc),
            )
        )
        top_atoms = sort(
            make_supercell(
                prim=structures.top_atoms.copy(),
                P=supercell_matrix(config.sc),
            )
        )
    except Exception as exc:
        diagnostics.append(
            PreflightDiagnostic(
                domain="structure",
                path=root_dir,
                reason=f"single-job init phase preparation failed: {exc}",
            )
        )
        return None
    return PreparedInitMlffWorkflow(
        mode="single-job",
        initial_state="step-1-ready",
        root_dir=root_dir,
        submit_adapter=submit_adapter,
        phases=(
            PreparedInitMlffPhase(
                name="bottom",
                role="step-1",
                initial_state="step-1-ready",
                target_dir=bottom_dir,
                atoms=bottom_atoms,
                template=bottom_template,
            ),
            PreparedInitMlffPhase(
                name="top",
                role="step-2",
                initial_state="planned",
                target_dir=top_dir,
                atoms=top_atoms,
                template=top_template,
            ),
        ),
    )


def _is_safe_basename(value: str) -> bool:
    if (
        not value
        or value in {".", ".."}
        or value != value.strip()
        or value.startswith("-")
    ):
        return False
    invalid_characters = '<>:"/\\|?*'
    if any(character in invalid_characters or ord(character) < 32 for character in value):
        return False
    if value.endswith("."):
        return False
    reserved_stems = {"con", "prn", "aux", "nul"}
    reserved_stems.update(f"com{index}" for index in range(1, 10))
    reserved_stems.update(f"lpt{index}" for index in range(1, 10))
    return value.split(".", 1)[0].casefold() not in reserved_stems


def _portable_filename_key(value: str) -> str:
    return value.rstrip(" .").casefold()


def _record_init_phase_filename_collisions(
    phase_templates: tuple[tuple[str, PreparedIncarTemplate], ...],
    submit_script: PreparedSource,
    diagnostics: list[PreflightDiagnostic],
) -> bool:
    collision_found = False
    fixed_inputs = ("POSCAR", "POTCAR", "INCAR", "KPOINTS")
    for phase_name, template in phase_templates:
        owners = {
            _portable_filename_key(name): (name, "generated VASP input")
            for name in fixed_inputs
        }
        sources = [(submit_script.path.name, "submit script")]
        if template.vdw_source is not None:
            sources.append((template.vdw_source.path.name, "supporting input"))
        for name, owner in sources:
            key = _portable_filename_key(name)
            previous = owners.get(key)
            if previous is not None:
                previous_name, previous_owner = previous
                diagnostics.append(
                    PreflightDiagnostic(
                        domain="path",
                        path=submit_script.path,
                        reason=(
                            f"single-job init {phase_name} filename collision: "
                            f"{name!r} conflicts with {previous_name!r}; used by "
                            f"both {previous_owner} and {owner}"
                        ),
                    )
                )
                collision_found = True
            else:
                owners[key] = (name, owner)
    return collision_found


def preflight_stage1(config: DPmoireLiteConfig) -> BuildPreflightResult:
    stage_targets = _stage1_target_stages(config)
    check_target_stages_absent(config, stage_targets)
    diagnostics: list[PreflightDiagnostic] = []
    warning_diagnostics: list[PreflightDiagnostic] = []
    templates = _validate_templates(
        _stage1_templates(config),
        config,
        diagnostics,
        warning_diagnostics,
    )
    submit_script = _validate_submit_script(config, diagnostics)

    structures = _read_structures(config, diagnostics)
    rcut = None
    workflow_cutoff = None
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
        potcars = _validate_potcars(config, elements, diagnostics)
        workflow_cutoff = _prepare_workflow_cutoff(config, potcars)

    manifest_result = read_manifest(config.work_dir, "rlx")
    if workflow_cutoff is not None:
        _validate_workflow_cutoff_provenance(
            manifest_result,
            workflow_cutoff,
            diagnostics,
            warning_diagnostics,
        )
    stackings = _stage1_manifest_stackings(manifest_result, diagnostics)
    relaxation_records = []
    for stacking in stackings:
        atoms = _validate_relaxation(
            config.work_dir / "rlx" / f"{stacking[0]}_{stacking[1]}",
            diagnostics,
        )
        if atoms is not None:
            relaxation_records.append(
                PreparedRelaxation(stacking=stacking, atoms=atoms.copy())
            )
    relaxations = tuple(relaxation_records)
    relaxation_atoms = {
        record.stacking: record.atoms
        for record in relaxations
    }

    provenance = provenance_module.validate_stage1_provenance(
        config,
        manifest_result,
        stackings,
        structures,
        relaxation_atoms,
    )
    for issue in provenance.issues:
        target = warning_diagnostics if issue.severity == "warning" else diagnostics
        target.append(
            PreflightDiagnostic(
                domain="provenance",
                path=issue.path,
                reason=issue.reason,
            )
        )

    initial_seed = None
    if config.vasp_ml:
        initial_seed = _validate_initial_seed(
            config,
            diagnostics,
        )

    output_dirs = _stage1_output_dirs(config, stackings)
    _validate_output_dirs(
        config,
        tuple(path for _stage, path in stage_targets) + output_dirs,
        diagnostics,
    )
    _raise_if_failed(diagnostics)
    _emit_warnings(warning_diagnostics)
    return BuildPreflightResult(
        stage=config.stage,
        structures=structures,
        stackings=tuple(stackings),
        rcut=rcut,
        templates=templates,
        workflow_cutoff=workflow_cutoff,
        submit_script=submit_script,
        stage_targets=stage_targets,
        output_dirs=output_dirs,
        initial_seed=initial_seed,
        relaxations=relaxations,
        warnings=tuple(warning_diagnostics),
        provenance=provenance,
    )


def _stage1_manifest_stackings(
    manifest_result: ManifestReadResult,
    diagnostics: list[PreflightDiagnostic],
) -> tuple[tuple[int, int], ...]:
    manifest_display_path = Path("rlx") / "manifest.yaml"
    if manifest_result.kind == "missing":
        diagnostics.append(
            PreflightDiagnostic(
                domain="manifest",
                path=manifest_display_path,
                reason=(
                    "Missing relaxation manifest; Stage1 cannot determine its "
                    "stacking list. Rebuild the relaxation stage before Stage1."
                ),
            )
        )
        return ()

    if manifest_result.kind == "legacy":
        manifest_stackings = (manifest_result.raw_data or {}).get("stackings")
    elif manifest_result.manifest is not None:
        manifest_stackings = manifest_result.manifest.stackings
    else:
        manifest_stackings = None

    if not isinstance(manifest_stackings, list) or not manifest_stackings:
        diagnostics.append(
            PreflightDiagnostic(
                domain="manifest",
                path=manifest_display_path,
                reason="relaxation manifest stackings must be a non-empty list",
            )
        )
        return ()

    stackings = []
    for value in manifest_stackings:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="manifest",
                    path=manifest_display_path,
                    reason="each relaxation manifest stacking must contain two indices",
                )
            )
            return ()
        try:
            stackings.append((int(value[0]), int(value[1])))
        except (TypeError, ValueError):
            diagnostics.append(
                PreflightDiagnostic(
                    domain="manifest",
                    path=manifest_display_path,
                    reason="relaxation manifest stacking indices must be integers",
                )
            )
            return ()
    return tuple(stackings)


def _validate_workflow_cutoff_provenance(
    manifest_result: ManifestReadResult,
    workflow_cutoff: PreparedWorkflowCutoff,
    diagnostics: list[PreflightDiagnostic],
    warning_diagnostics: list[PreflightDiagnostic],
) -> None:
    if manifest_result.kind != "current" or manifest_result.manifest is None:
        return

    manifest_path = Path("rlx") / "manifest.yaml"
    recorded = manifest_result.manifest.config_summary.get("workflow_cutoff")
    if recorded is None:
        warning_diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=manifest_path,
                reason=(
                    "relaxation manifest predates workflow cutoff evidence; "
                    "Stage1 is using a newly resolved cutoff plan"
                ),
            )
        )
        return
    if recorded != workflow_cutoff.audit_record():
        diagnostics.append(
            PreflightDiagnostic(
                domain="provenance",
                path=manifest_path,
                reason=(
                    "workflow cutoff plan drift between the relaxation manifest "
                    "and current Stage1 inputs; restore the Stage0 POTCAR/config "
                    "selection before generating MD"
                ),
            )
        )


def _stage0_templates(config: DPmoireLiteConfig) -> list[tuple[str, Path]]:
    templates = []
    if config.init_mlff:
        if config.init_mlff_mode == "single-job":
            if config.init_bottom_incar is None or config.init_top_incar is None:
                raise RuntimeError(
                    "single-job init preflight requires prepared bottom and top INCAR paths"
                )
            templates.extend(
                (
                    ("init_bottom_INCAR", config.init_bottom_incar),
                    ("init_top_INCAR", config.init_top_incar),
                )
            )
        else:
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
) -> tuple[PreparedIncarTemplate, ...]:
    del config
    prepared_templates = []
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
            source = prepare_source(path)
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
        vdw_source = None
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
            else:
                try:
                    vdw_source = prepare_source(kernel)
                except Exception as exc:
                    diagnostics.append(
                        PreflightDiagnostic(
                            domain="vdw",
                            path=kernel,
                            reason=str(exc),
                        )
                    )
        prepared_templates.append(
            PreparedIncarTemplate(
                name=name,
                source=source,
                analysis=analysis,
                vdw_source=vdw_source,
            )
        )

    return tuple(prepared_templates)


def _validate_submit_script(
    config: DPmoireLiteConfig,
    diagnostics: list[PreflightDiagnostic],
) -> PreparedSource | None:
    if config.init_mlff_mode == "single-job" and not _is_safe_basename(
        config.dft_script
    ):
        diagnostics.append(
            PreflightDiagnostic(
                domain="script",
                path=config.script_dir,
                reason=(
                    "single-job init submit script must use a safe basename, "
                    "not a nested, drive-relative, reserved, or non-portable name"
                ),
            )
        )
        return None
    path = config.script_dir / config.dft_script
    if not path.is_file():
        diagnostics.append(
            PreflightDiagnostic(
                domain="script",
                path=path,
                reason="required submit script is missing",
            )
        )
        return None
    try:
        return prepare_source(path)
    except Exception as exc:
        diagnostics.append(
            PreflightDiagnostic(
                domain="script",
                path=path,
                reason=str(exc),
            )
        )
        return None


def _read_structures(
    config: DPmoireLiteConfig,
    diagnostics: list[PreflightDiagnostic],
) -> StructureHandler | None:
    start_diagnostics = len(diagnostics)
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

    if len(diagnostics) != start_diagnostics:
        return None

    try:
        return StructureHandler.from_atoms(
            config.input_dir,
            config.work_dir,
            config.n_sectors,
            config.d,
            input_atoms["top_layer"],
            input_atoms["bot_layer"],
            config.d_mode,
            config.d_reference,
            config.grid_shift_anchor,
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
) -> tuple[PreparedPotcar, ...]:
    potcars = []
    for element in elements:
        try:
            source_dir = resolve_potcar_dir(
                element,
                config.potcar_dir,
                potcar_policy=config.potcar_policy,
            )
            source = prepare_source(source_dir / "POTCAR")
            enmax = read_enmax(source.path)
            if not np.isfinite(enmax) or enmax <= 0:
                raise ValueError(
                    f"ENMAX must be finite and positive in {source.path}"
                )
            potcars.append(
                PreparedPotcar(element=element, source=source, enmax=enmax)
            )
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="potcar",
                    path=config.potcar_dir / element / "POTCAR",
                    reason=str(exc),
                )
            )
    return tuple(potcars)


def _prepare_workflow_cutoff(
    config: DPmoireLiteConfig,
    potcars: tuple[PreparedPotcar, ...],
) -> PreparedWorkflowCutoff | None:
    factor = config.encut_factor
    if not potcars:
        return None

    governing_potcar = min(
        potcars,
        key=lambda potcar: (
            -potcar.enmax,
            potcar.element,
            potcar.source.path.parent.name,
        ),
    )
    encut = governing_potcar.enmax * factor
    return PreparedWorkflowCutoff(
        selected_potcars=potcars,
        governing_potcar=governing_potcar,
        encut_factor=factor,
        encut=float(encut),
    )


def _validate_relaxation(
    directory: Path,
    diagnostics: list[PreflightDiagnostic],
) -> Atoms | None:
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
    contcar_atoms = None
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
            contcar_atoms = read_vasp(contcar)
        except Exception as exc:
            diagnostics.append(
                PreflightDiagnostic(
                    domain="relaxation",
                    path=display_directory,
                    reason=f"CONTCAR is not readable by ASE: {exc}",
                )
            )
    return contcar_atoms


def _validate_initial_seed(
    config: DPmoireLiteConfig,
    diagnostics: list[PreflightDiagnostic],
) -> PreparedInitialSeed | None:
    init_dir = config.work_dir / "init_mlff"
    try:
        published_seed = validate_published_init_mlff_seed(config.work_dir)
    except InitMlffWorkflowError as exc:
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=Path("init_mlff") / "manifest.yaml",
                reason=str(exc),
            )
        )
        return None
    if published_seed is not None:
        return PreparedInitialSeed(
            parsed=published_seed.parsed,
            ml_ab=published_seed.ml_ab,
            ml_ff=published_seed.ml_ff,
        )

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

    if (
        not seed_path.is_file()
        or seed_path.stat().st_size == 0
        or not force_field_path.is_file()
        or force_field_path.stat().st_size == 0
    ):
        return None
    try:
        result = parse_mlab(seed_path)
    except Exception as exc:
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=seed_display_path,
                reason=str(exc),
            )
        )
        return None
    if result.status != "complete" or result.declared_count != result.complete_count:
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=seed_display_path,
                reason=(
                    "initial ML_ABN seed must be complete: "
                    f"status={result.status!r}, declared={result.declared_count}, "
                    f"complete={result.complete_count}"
                ),
            )
        )
        return None
    try:
        prepared = PreparedInitialSeed(
            parsed=result,
            ml_ab=prepare_source(seed_path),
            ml_ff=prepare_source(force_field_path),
        )
    except (OSError, RuntimeError) as exc:
        diagnostics.append(
            PreflightDiagnostic(
                domain="mlff",
                path=Path("init_mlff"),
                reason=f"initial MLFF seed identity could not be prepared: {exc}",
            )
        )
        return None
    return prepared


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
