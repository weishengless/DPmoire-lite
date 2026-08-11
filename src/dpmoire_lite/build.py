from __future__ import annotations

import warnings
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import numpy as np
from ase.build import make_supercell, sort
from ase import Atoms
from ase.constraints import FixedLine
from ase.io.vasp import read_vasp, write_vasp

from .atomic_io import sha256_file
from .build_preflight import (
    PreparedValidationStructure,
    PreparedWorkflowCutoff,
    preflight_stage0,
    preflight_stage1,
)
from .config import ConfigError, DPmoireLiteConfig, load_config
from .inputs import (
    PreparedIncarTemplate,
    PreparedSource,
    copy_prepared_source,
    get_ordered_elements,
    render_prepared_incar,
    stage_mlff_files,
    write_kpoints,
    write_prepared_potcar,
    write_supercell_poscar,
)
from .manifest import Manifest, write_manifest
from .mlab import seed_prefix_identity
from .paths import backup_existing_directory, relative_to_workdir
from . import provenance as provenance_module
from .slurm import SlurmJob, SlurmRunner
from .structures import StructureHandler, supercell_matrix


VASP_RELAXATION_CONVERGED_PHRASE = "reached required accuracy - stopping structural energy minimisation"


def run_build(config_path: Path, wait: bool = False) -> None:
    config = load_config(config_path)
    config.validate_build_mode(wait)
    if config.stage == 0:
        build_stage0(config, wait=wait)
    elif config.stage == 1:
        build_stage1(config, wait=wait)
    elif config.stage == "all":
        build_stage_all(config, wait=wait)


def _prepared_template(
    templates: dict[str, PreparedIncarTemplate],
    name: str,
) -> PreparedIncarTemplate:
    try:
        return templates[name]
    except KeyError as exc:
        raise RuntimeError(f"Preflight did not prepare required template {name}") from exc


def _take_prepared_output_dirs(
    output_dirs,
    count: int,
    label: str,
) -> tuple[Path, ...]:
    selected = []
    for _index in range(count):
        try:
            selected.append(next(output_dirs))
        except StopIteration as exc:
            raise RuntimeError(
                f"Preflight did not return every required {label} output directory"
            ) from exc
    return tuple(selected)


def _assert_no_prepared_output_dirs_remain(output_dirs) -> None:
    try:
        extra = next(output_dirs)
    except StopIteration:
        return
    raise RuntimeError(f"Preflight returned an unexpected output directory: {extra}")


def build_stage0(config: DPmoireLiteConfig, wait: bool = False) -> None:
    config.validate_build_mode(wait)
    preflight = preflight_stage0(config)
    if preflight.structures is None or preflight.rcut is None:
        raise RuntimeError("Stage0 preflight did not return prepared structures and rcut")
    if preflight.submit_script is None:
        raise RuntimeError("Stage0 preflight did not return a prepared submit script")
    if preflight.workflow_cutoff is None:
        raise RuntimeError("Stage0 preflight did not return a workflow cutoff plan")
    structures = preflight.structures
    rcut = preflight.rcut
    workflow_cutoff = preflight.workflow_cutoff
    stackings = preflight.stackings
    templates = {template.name: template for template in preflight.templates}
    output_dirs = iter(preflight.output_dirs)
    init_dir = (
        _take_prepared_output_dirs(output_dirs, 1, "init_mlff")[0]
        if config.init_mlff
        else None
    )
    relaxation_dirs = _take_prepared_output_dirs(
        output_dirs,
        len(stackings) if config.do_relaxation else 0,
        "rlx",
    )
    validation_dirs = _take_prepared_output_dirs(
        output_dirs,
        len(preflight.validation_structures) if config.twist_val else 0,
        "validation",
    )
    _assert_no_prepared_output_dirs_remain(output_dirs)
    generated_at = datetime.now().isoformat(timespec="seconds")
    config.work_dir.mkdir(parents=True, exist_ok=True)
    if config.symm_reduce:
        structures.write_sym_reduced_stackings(list(stackings))
    runner = SlurmRunner(config.dft_script, config.n_nodes, config.auto_resub) if config.submit else None

    if config.init_mlff:
        _build_init_mlff(
            config,
            structures,
            init_dir,
            _prepared_template(templates, "init_INCAR"),
            workflow_cutoff,
            preflight.submit_script,
            rcut,
            generated_at,
            runner,
            wait,
        )
    if config.do_relaxation:
        _build_relaxations(
            config,
            structures,
            stackings,
            relaxation_dirs,
            _prepared_template(templates, "rlx_INCAR"),
            workflow_cutoff,
            preflight.submit_script,
            rcut,
            generated_at,
            runner,
            wait,
        )
    if config.twist_val:
        _build_validation(
            config,
            preflight.validation_structures,
            validation_dirs,
            _prepared_template(templates, "val_INCAR"),
            workflow_cutoff,
            preflight.submit_script,
            rcut,
            generated_at,
            runner,
            wait,
        )


def build_stage1(config: DPmoireLiteConfig, wait: bool = False, runner: SlurmRunner | None = None) -> None:
    config.validate_build_mode(wait)
    preflight = preflight_stage1(config)
    if preflight.structures is None or preflight.rcut is None:
        raise RuntimeError("Stage1 preflight did not return prepared structures and rcut")
    if preflight.submit_script is None:
        raise RuntimeError("Stage1 preflight did not return a prepared submit script")
    if preflight.workflow_cutoff is None:
        raise RuntimeError("Stage1 preflight did not return a workflow cutoff plan")
    structures = preflight.structures
    rcut = preflight.rcut
    workflow_cutoff = preflight.workflow_cutoff
    stackings = preflight.stackings
    templates = {template.name: template for template in preflight.templates}
    output_dirs = iter(preflight.output_dirs)
    relaxation_output_dirs = _take_prepared_output_dirs(
        output_dirs,
        len(preflight.relaxations),
        "md relaxations",
    )
    monolayer_output_dirs = _take_prepared_output_dirs(
        output_dirs,
        2 if config.include_monolayer_md else 0,
        "md monolayers",
    )
    _assert_no_prepared_output_dirs_remain(output_dirs)
    mlff_seed = _stage1_mlff_seed_identity(config, preflight)
    generated_at = datetime.now().isoformat(timespec="seconds")
    config.work_dir.mkdir(parents=True, exist_ok=True)
    backups = []

    directories = []
    md_anchor_records: dict[str, list[dict[str, object]]] = {}
    init_mlff_dir = config.work_dir / "init_mlff"
    provenance = preflight.provenance
    if provenance is None or provenance.trusted_sc_rlx is None:
        md_sc = None if config.sc_rlx else config.sc
    else:
        md_sc = None if provenance.trusted_sc_rlx else provenance.trusted_sc
    validated_anchor_indices = (
        provenance.validated_anchor_indices
        if config.preserve_grid_shift_md and provenance is not None
        else ()
    )
    for relaxation_index, (relaxation, target) in enumerate(
        zip(
            preflight.relaxations,
            relaxation_output_dirs,
            strict=True,
        )
    ):
        i, j = relaxation.stacking
        target.mkdir(parents=True, exist_ok=True)
        atoms, anchor_records = _write_md_poscar(
            relaxation.atoms.copy(),
            target / "POSCAR",
            md_sc,
            preserve_constraints=config.preserve_grid_shift_md,
            validated_anchor_indices=(
                validated_anchor_indices[relaxation_index]
                if config.preserve_grid_shift_md
                else None
            ),
        )
        if anchor_records is not None:
            md_anchor_records[relative_to_workdir(config.work_dir, target)] = anchor_records
        _write_vasp_inputs(
            config,
            target,
            atoms,
            _prepared_template(templates, "MD_INCAR"),
            rcut,
            workflow_cutoff,
            preflight.submit_script,
        )
        if config.vasp_ml:
            stage_mlff_files(init_mlff_dir, target)
            _verify_staged_mlff_files(init_mlff_dir, target, mlff_seed)
        directories.append(target)

    monolayer_constraint_warning_emitted = False
    if config.include_monolayer_md:
        layer_sources = {
            "top_layer": structures.top_atoms,
            "bot_layer": structures.bot_atoms,
        }
        for (layer_name, source_atoms), target in zip(
            layer_sources.items(),
            monolayer_output_dirs,
            strict=True,
        ):
            if source_atoms is None:
                raise RuntimeError(f"Stage1 preflight did not return {layer_name} atoms")
            target.mkdir(parents=True, exist_ok=True)
            atoms = source_atoms.copy()
            had_constraints = _normalize_stage1_structure(
                atoms,
                clear_constraints=True,
                warn_on_constraints=not monolayer_constraint_warning_emitted,
            )
            if had_constraints:
                monolayer_constraint_warning_emitted = True
            atoms_sc = sort(make_supercell(prim=atoms, P=supercell_matrix(config.sc)))
            _assert_stage1_structure_cleared(atoms_sc)
            write_vasp(target / "POSCAR", atoms=atoms_sc)
            _write_vasp_inputs(
                config,
                target,
                atoms_sc,
                _prepared_template(templates, "MD_monolayer_INCAR"),
                rcut,
                workflow_cutoff,
                preflight.submit_script,
            )
            if config.vasp_ml:
                stage_mlff_files(init_mlff_dir, target)
                _verify_staged_mlff_files(init_mlff_dir, target, mlff_seed)
            directories.append(target)

    if not config.submit:
        runner = None
    elif runner is None:
        runner = SlurmRunner(config.dft_script, config.n_nodes, config.auto_resub)
    jobs = _submit_dirs(config, runner, directories, wait)
    write_manifest(
        config.work_dir,
        Manifest(
            stage="md",
            generated_at=generated_at,
            config_summary=_config_summary(config, workflow_cutoff),
            directories=[relative_to_workdir(config.work_dir, path) for path in directories],
            backups=backups,
            jobs=[job.as_dict() for job in jobs],
            stackings=[[i, j] for i, j in stackings],
            structure_provenance=provenance_module.stage1_structure_provenance(
                preflight.provenance
            ),
            grid_shift_anchors=md_anchor_records,
            mlff_seed=mlff_seed,
        ),
    )


def _stage1_mlff_seed_identity(
    config: DPmoireLiteConfig,
    preflight,
) -> dict[str, object]:
    if not config.vasp_ml:
        return {}
    if preflight.initial_seed is None:
        raise RuntimeError("Stage1 MLFF seed preflight did not return a complete seed")

    init_dir = config.work_dir / "init_mlff"
    seed_identity = seed_prefix_identity(preflight.initial_seed)
    return {
        "source": "init_mlff/ML_ABN",
        "configurations": preflight.initial_seed.complete_count,
        "digest_schema": seed_identity.schema,
        "seed_prefix_sha256": seed_identity.sha256,
        "ml_ab_sha256": sha256_file(init_dir / "ML_ABN"),
        "ml_ff_sha256": sha256_file(init_dir / "ML_FFN"),
    }


def _verify_staged_mlff_files(
    init_dir: Path,
    output_dir: Path,
    seed_identity: dict[str, object],
) -> None:
    for source_name, destination_name, digest_key in (
        ("ML_ABN", "ML_AB", "ml_ab_sha256"),
        ("ML_FFN", "ML_FF", "ml_ff_sha256"),
    ):
        source = Path(init_dir) / source_name
        destination = Path(output_dir) / destination_name
        if not destination.is_file():
            raise RuntimeError(
                f"Stage1 MLFF copy verification failed for {destination}: missing file"
            )
        source_size = source.stat().st_size
        destination_size = destination.stat().st_size
        if source_size != destination_size:
            raise RuntimeError(
                f"Stage1 MLFF copy verification failed for {destination}: "
                f"size {destination_size} != {source_size}"
            )
        destination_hash = sha256_file(destination)
        if destination_hash != seed_identity[digest_key]:
            raise RuntimeError(
                f"Stage1 MLFF copy verification failed for {destination}: "
                f"hash {destination_hash} != {seed_identity[digest_key]}"
            )


def build_stage_all(
    config: DPmoireLiteConfig,
    wait: bool = False,
    runner_factory: Callable[[DPmoireLiteConfig], SlurmRunner] | None = None,
) -> None:
    if config.stage != "all":
        raise ConfigError("build_stage_all requires stage: all")
    config.validate_build_mode(wait)
    del runner_factory
    raise ConfigError("stage: all is unavailable")


def check_relaxation_converged(directory: Path) -> None:
    directory = Path(directory)
    outcar = directory / "OUTCAR"
    if not outcar.exists():
        raise FileNotFoundError(f"Missing OUTCAR in {directory}")
    text = outcar.read_text(encoding="utf-8", errors="ignore")
    if VASP_RELAXATION_CONVERGED_PHRASE not in text:
        raise ValueError(f"Relaxation did not converge in {directory}")

    contcar = directory / "CONTCAR"
    if not contcar.exists():
        raise FileNotFoundError(f"Missing CONTCAR in {directory}")
    try:
        read_vasp(contcar)
    except Exception as exc:
        raise ValueError(f"CONTCAR is not readable by ASE in {directory}") from exc


def check_stage1_inputs(config: DPmoireLiteConfig, stackings: list[tuple[int, int]]) -> None:
    failures: list[tuple[Path, str]] = []
    init_mlff_dir = config.work_dir / "init_mlff"
    if config.vasp_ml:
        for name in ("ML_ABN", "ML_FFN"):
            path = init_mlff_dir / name
            if not path.exists():
                failures.append((path, "missing required MLFF file"))

    for i, j in stackings:
        source_dir = config.work_dir / "rlx" / f"{i}_{j}"
        try:
            check_relaxation_converged(source_dir)
        except (FileNotFoundError, ValueError) as exc:
            failures.append((source_dir, _stage1_failure_reason(exc, source_dir)))

    if failures:
        details = "\n".join(
            f"- {relative_to_workdir(config.work_dir, path)}: {reason}"
            for path, reason in failures
        )
        raise RuntimeError(f"Stage 1 input preflight failed:\n{details}")


def prepare_init_mlff_step2(init_dir: Path, input_dir: Path, sc: tuple[int, int]) -> None:
    init_dir = Path(init_dir)
    input_dir = Path(input_dir)
    (init_dir / "ML_ABN").replace(init_dir / "ML_AB")
    (init_dir / "ML_FFN").replace(init_dir / "ML_FF")
    write_supercell_poscar(input_dir / "top_layer.poscar", init_dir / "POSCAR", sc)


def _build_init_mlff(
    config: DPmoireLiteConfig,
    structures: StructureHandler,
    init_dir: Path,
    template: PreparedIncarTemplate,
    workflow_cutoff: PreparedWorkflowCutoff,
    submit_script: PreparedSource,
    rcut: float,
    generated_at: str,
    runner: SlurmRunner | None,
    wait: bool,
) -> None:
    backups = []
    init_dir.mkdir(parents=True, exist_ok=True)
    if structures.bot_atoms is None:
        raise RuntimeError("Stage0 preflight did not return bottom-layer atoms")
    atoms = sort(
        make_supercell(
            prim=structures.bot_atoms.copy(),
            P=supercell_matrix(config.sc),
        )
    )
    write_vasp(init_dir / "POSCAR", atoms=atoms)
    _write_vasp_inputs(
        config,
        init_dir,
        atoms,
        template,
        rcut,
        workflow_cutoff,
        submit_script,
    )
    jobs = _submit_dirs(config, runner, [init_dir], wait)
    if runner is not None and wait:
        prepare_init_mlff_step2(init_dir, config.input_dir, config.sc)
        jobs.extend(_submit_dirs(config, runner, [init_dir], wait=True))
    write_manifest(
        config.work_dir,
        Manifest(
            stage="init_mlff",
            generated_at=generated_at,
            config_summary=_config_summary(config, workflow_cutoff),
            directories=[relative_to_workdir(config.work_dir, init_dir)],
            backups=backups,
            jobs=[job.as_dict() for job in jobs],
        ),
    )


def _build_relaxations(
    config: DPmoireLiteConfig,
    structures: StructureHandler,
    stackings: tuple[tuple[int, int], ...],
    target_dirs: tuple[Path, ...],
    template: PreparedIncarTemplate,
    workflow_cutoff: PreparedWorkflowCutoff,
    submit_script: PreparedSource,
    rcut: float,
    generated_at: str,
    runner: SlurmRunner | None,
    wait: bool,
) -> None:
    backups = []
    directories = []
    rlx_poscars = {}
    grid_shift_anchors = {}
    for (i, j), target in zip(stackings, target_dirs, strict=True):
        target.mkdir(parents=True, exist_ok=True)
        atoms = structures.shift_atoms(i, j, c_constrain=True, sc=config.sc) if config.sc_rlx else structures.shift_primitive_atoms(i, j)
        write_vasp(target / "POSCAR", atoms=atoms)
        relative_path = relative_to_workdir(config.work_dir, target / "POSCAR")
        identity = provenance_module.structure_identity_record(target / "POSCAR", relative_path)
        rlx_poscars[relative_path] = identity
        top_indexes, bot_indexes = structures.find_layer_idx(atoms)
        anchor_relative_path = relative_to_workdir(config.work_dir, target)
        grid_shift_anchors[anchor_relative_path] = provenance_module.stage0_grid_shift_anchor_record(
            target / "POSCAR",
            identity,
            top_indexes,
            bot_indexes,
        )
        _write_vasp_inputs(
            config,
            target,
            atoms,
            template,
            rcut,
            workflow_cutoff,
            submit_script,
        )
        directories.append(target)
    jobs = _submit_dirs(config, runner, directories, wait)
    provenance = provenance_module.stage0_structure_provenance(
        config,
        structures,
        stackings,
        rlx_poscars,
    )
    write_manifest(
        config.work_dir,
        Manifest(
            stage="rlx",
            generated_at=generated_at,
            config_summary=_config_summary(config, workflow_cutoff),
            directories=[relative_to_workdir(config.work_dir, path) for path in directories],
            backups=backups,
            jobs=[job.as_dict() for job in jobs],
            stackings=[[i, j] for i, j in stackings],
            structure_provenance=provenance,
            grid_shift_anchors=grid_shift_anchors,
        ),
    )


def _build_validation(
    config: DPmoireLiteConfig,
    validation_structures: tuple[PreparedValidationStructure, ...],
    target_dirs: tuple[Path, ...],
    template: PreparedIncarTemplate,
    workflow_cutoff: PreparedWorkflowCutoff,
    submit_script: PreparedSource,
    rcut: float,
    generated_at: str,
    runner: SlurmRunner | None,
    wait: bool,
) -> None:
    directories = list(target_dirs)
    backups = []
    for record, target in zip(validation_structures, target_dirs, strict=True):
        target.mkdir(parents=True, exist_ok=True)
        atoms = record.atoms.copy()
        write_vasp(target / "POSCAR", atoms=atoms)
        _write_vasp_inputs(
            config,
            target,
            atoms,
            template,
            rcut,
            workflow_cutoff,
            submit_script,
        )
    jobs = _submit_dirs(config, runner, directories, wait)
    write_manifest(
        config.work_dir,
        Manifest(
            stage="validation",
            generated_at=generated_at,
            config_summary=_config_summary(config, workflow_cutoff),
            directories=[relative_to_workdir(config.work_dir, path) for path in directories],
            backups=backups,
            jobs=[job.as_dict() for job in jobs],
            angles=[record.angle for record in validation_structures],
        ),
    )


def _check_mlff_files(init_mlff_dir: Path) -> None:
    for name in ("ML_ABN", "ML_FFN"):
        path = Path(init_mlff_dir) / name
        if not path.exists():
            raise FileNotFoundError(f"vasp_ml requires {path}")


def _stage1_failure_reason(exc: Exception, source_dir: Path) -> str:
    text = str(exc)
    suffix = f" in {source_dir}"
    if text.endswith(suffix):
        return text[: -len(suffix)]
    return text


def _normalize_stage1_structure(
    atoms: Atoms,
    *,
    clear_constraints: bool,
    warn_on_constraints: bool = False,
) -> bool:
    had_constraints = bool(atoms.constraints)
    if clear_constraints:
        atoms.set_constraint([])
    if "momenta" in atoms.arrays:
        atoms.set_momenta(None)
    if had_constraints and warn_on_constraints:
        warnings.warn(
            "Clearing constraints from monolayer MD input; preserve_grid_shift_md does not apply.",
            UserWarning,
            stacklevel=2,
        )
    return had_constraints


def _assert_stage1_structure_cleared(atoms: Atoms) -> None:
    if atoms.constraints or "momenta" in atoms.arrays:
        raise RuntimeError("Stage1 normalization failed to clear constraints and momenta")


def _write_md_poscar(
    source_atoms: Atoms,
    poscar: Path,
    sc: tuple[int, int] | None,
    *,
    preserve_constraints: bool = False,
    validated_anchor_indices: tuple[int, int] | None = None,
) -> tuple[Atoms, list[dict[str, object]] | None]:
    atoms = source_atoms.copy()
    if preserve_constraints:
        if validated_anchor_indices is None:
            raise RuntimeError("Stage1 preservation requires validated provenance anchor indices")
        source_anchor_indices = [int(index) for index in validated_anchor_indices]
    else:
        source_anchor_indices = []
    _normalize_stage1_structure(atoms, clear_constraints=not preserve_constraints)
    if sc is None:
        if not preserve_constraints:
            _assert_stage1_structure_cleared(atoms)
        poscar.parent.mkdir(parents=True, exist_ok=True)
        write_vasp(poscar, atoms=atoms, direct=True, sort=False)
        if not preserve_constraints:
            return atoms, None
        return atoms, [
            {
                "index": index,
                "source_index": index,
                "image_translation": [0, 0, 0],
            }
            for index in source_anchor_indices
        ]

    source_atom_count = len(atoms)
    atoms.set_array("source_index", np.arange(source_atom_count, dtype=int))
    atoms_expanded = make_supercell(prim=atoms, P=supercell_matrix(sc))
    translations = np.asarray(
        [
            [translation_x, translation_y, 0]
            for translation_x in range(sc[0])
            for translation_y in range(sc[1])
        ],
        dtype=int,
    )
    atoms_expanded.set_array(
        "image_translation",
        np.repeat(translations, source_atom_count, axis=0),
    )
    atoms_sc = sort(atoms_expanded)
    if not preserve_constraints:
        _assert_stage1_structure_cleared(atoms_sc)
        del atoms_sc.arrays["source_index"]
        del atoms_sc.arrays["image_translation"]
        poscar.parent.mkdir(parents=True, exist_ok=True)
        write_vasp(poscar, atoms=atoms_sc, direct=True, sort=False)
        return atoms_sc, None

    source_indices = np.asarray(atoms_sc.arrays["source_index"], dtype=int)
    image_translations = np.asarray(atoms_sc.arrays["image_translation"], dtype=int)
    zero_translation = np.all(image_translations == [0, 0, 0], axis=1)
    anchor_indices = []
    anchor_records = []
    for source_index in source_anchor_indices:
        matches = np.flatnonzero((source_indices == source_index) & zero_translation)
        if len(matches) != 1:
            raise RuntimeError(
                "Stage1 expansion could not recover a unique zero-translation "
                f"anchor for source index {source_index}"
            )
        final_index = int(matches[0])
        anchor_indices.append(final_index)
        anchor_records.append(
            {
                "index": final_index,
                "source_index": int(source_index),
                "image_translation": image_translations[final_index].tolist(),
            }
        )

    atoms_sc.set_constraint(
        FixedLine(
            anchor_indices,
            direction=atoms_sc.cell.array[2] / atoms_sc.cell.lengths()[2],
        )
    )
    del atoms_sc.arrays["source_index"]
    del atoms_sc.arrays["image_translation"]
    poscar.parent.mkdir(parents=True, exist_ok=True)
    write_vasp(poscar, atoms=atoms_sc, direct=True, sort=False)
    anchor_records.sort(key=lambda record: int(record["index"]))
    return atoms_sc, anchor_records


def _write_vasp_inputs(
    config: DPmoireLiteConfig,
    output_dir: Path,
    atoms: Atoms,
    template: PreparedIncarTemplate,
    rcut: float,
    workflow_cutoff: PreparedWorkflowCutoff,
    submit_script: PreparedSource,
) -> None:
    elements = _ordered_elements(atoms)
    write_prepared_potcar(
        elements,
        workflow_cutoff.selected_potcars,
        output_dir / "POTCAR",
    )
    render_prepared_incar(
        template,
        output_dir / "INCAR",
        encut=workflow_cutoff.encut,
        rcut1=rcut,
        rcut2=rcut,
    )
    write_kpoints(output_dir, atoms.cell.array, config.k_mesh)
    copy_prepared_source(submit_script, output_dir / submit_script.path.name)
    if template.vdw_source is not None:
        copy_prepared_source(
            template.vdw_source,
            output_dir / template.vdw_source.path.name,
        )


def _ordered_elements(atoms: Atoms) -> list[str]:
    return get_ordered_elements(atoms)


def _backup_targets(work_dir: Path, stage: str, targets: list[Path], timestamp: str) -> list[str]:
    backups = []
    for target in targets:
        backup = backup_existing_directory(work_dir, stage, target, timestamp)
        if backup is not None:
            backups.append(relative_to_workdir(work_dir, backup))
    return backups


def _submit_dirs(config: DPmoireLiteConfig, runner: SlurmRunner | None, directories: list[Path], wait: bool) -> list[SlurmJob]:
    if runner is None:
        return []
    items = [(path, relative_to_workdir(config.work_dir, path)) for path in directories]
    if hasattr(runner, "submit_many"):
        return runner.submit_many(items, wait=wait)
    jobs = [runner.submit(path, relative_to_workdir(config.work_dir, path)) for path in directories]
    return runner.wait(jobs) if wait else jobs


def _config_summary(
    config: DPmoireLiteConfig,
    workflow_cutoff: PreparedWorkflowCutoff,
) -> dict[str, object]:
    d_reference = None
    if config.d_reference is not None:
        d_reference = {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in config.d_reference.items()
        }
    return {
        "stage": config.stage,
        "n_sectors": list(config.n_sectors),
        "sc": list(config.sc),
        "d": config.d,
        "d_mode": config.d_mode,
        "d_reference": d_reference,
        "potcar_policy": config.potcar_policy,
        "k_mesh": config.k_mesh,
        "encut_factor": config.encut_factor,
        "workflow_cutoff": workflow_cutoff.audit_record(),
        "r_cut": config.r_cut,
    }
