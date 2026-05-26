from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
from ase import Atoms
from ase.io.vasp import write_vasp

from .config import DPmoireLiteConfig, load_config
from .inputs import (
    copy_submit_script,
    copy_vdw_if_needed,
    get_ordered_elements,
    render_incar,
    write_kpoints,
    write_potcar,
    write_supercell_poscar,
)
from .manifest import Manifest, write_manifest
from .paths import backup_existing_directory, relative_to_workdir, stage_dir
from .slurm import SlurmJob, SlurmRunner
from .structures import StructureHandler, generate_stackings


def run_build(config_path: Path, wait: bool = False) -> None:
    config = load_config(config_path)
    config.validate_build_mode(wait)
    if config.stage == 0:
        build_stage0(config, wait=wait)
    elif config.stage == 1:
        build_stage1(config, wait=wait)
    elif config.stage == "all":
        build_stage_all(config, wait=wait)


def build_stage0(config: DPmoireLiteConfig, wait: bool = False) -> None:
    generated_at = datetime.now().isoformat(timespec="seconds")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    config.work_dir.mkdir(parents=True, exist_ok=True)
    structures = StructureHandler(config.input_dir, config.work_dir, config.n_sectors, config.d)
    rcut = _resolve_rcut(config, structures.top_atoms, structures.bot_atoms)
    stackings = (
        structures.find_sym_reduced_stackings()
        if config.symm_reduce
        else generate_stackings(config.n_sectors)
    )
    runner = SlurmRunner(config.dft_script, config.n_nodes, config.auto_resub) if config.submit else None

    if config.init_mlff:
        _build_init_mlff(config, structures, rcut, generated_at, timestamp, runner, wait)
    if config.do_relaxation:
        _build_relaxations(config, structures, stackings, rcut, generated_at, timestamp, runner, wait)
    if config.twist_val:
        _build_validation(config, structures, rcut, generated_at, timestamp, runner, wait)


def build_stage1(config: DPmoireLiteConfig, wait: bool = False) -> None:
    _ = (config, wait)
    raise NotImplementedError("stage 1 build is added in a later task")


def build_stage_all(config: DPmoireLiteConfig, wait: bool = False) -> None:
    _ = (config, wait)
    raise NotImplementedError("stage all build is added in a later task")


def _build_init_mlff(
    config: DPmoireLiteConfig,
    structures: StructureHandler,
    rcut: float,
    generated_at: str,
    timestamp: str,
    runner: SlurmRunner | None,
    wait: bool,
) -> None:
    init_dir = stage_dir(config.work_dir, "init_mlff")
    backups = _backup_targets(config.work_dir, "init_mlff", [init_dir], timestamp)
    init_dir.mkdir(parents=True, exist_ok=True)
    write_supercell_poscar(config.input_dir / "bot_layer.poscar", init_dir / "POSCAR", config.sc)
    atoms = structures.read_atoms(init_dir / "POSCAR")
    _write_vasp_inputs(config, init_dir, atoms, config.input_dir / "init_INCAR", config.sc, rcut)
    jobs = _submit_dirs(config, runner, [init_dir], wait)
    write_manifest(
        config.work_dir,
        Manifest(
            stage="init_mlff",
            generated_at=generated_at,
            config_summary=_config_summary(config),
            directories=[relative_to_workdir(config.work_dir, init_dir)],
            backups=backups,
            jobs=[job.as_dict() for job in jobs],
        ),
    )


def _build_relaxations(
    config: DPmoireLiteConfig,
    structures: StructureHandler,
    stackings: list[tuple[int, int]],
    rcut: float,
    generated_at: str,
    timestamp: str,
    runner: SlurmRunner | None,
    wait: bool,
) -> None:
    rlx_dir = stage_dir(config.work_dir, "rlx")
    targets = [rlx_dir / f"{i}_{j}" for i, j in stackings]
    backups = _backup_targets(config.work_dir, "rlx", targets, timestamp)
    directories = []
    for i, j in stackings:
        target = rlx_dir / f"{i}_{j}"
        target.mkdir(parents=True, exist_ok=True)
        atoms = structures.shift_atoms(i, j, c_constrain=True, sc=config.sc) if config.sc_rlx else structures.shift_primitive_atoms(i, j)
        write_vasp(target / "POSCAR", atoms=atoms)
        _write_vasp_inputs(config, target, atoms, config.input_dir / "rlx_INCAR", config.sc if config.sc_rlx else (1, 1), rcut)
        directories.append(target)
    jobs = _submit_dirs(config, runner, directories, wait)
    write_manifest(
        config.work_dir,
        Manifest(
            stage="rlx",
            generated_at=generated_at,
            config_summary=_config_summary(config),
            directories=[relative_to_workdir(config.work_dir, path) for path in directories],
            backups=backups,
            jobs=[job.as_dict() for job in jobs],
            stackings=[[i, j] for i, j in stackings],
        ),
    )


def _build_validation(
    config: DPmoireLiteConfig,
    structures: StructureHandler,
    rcut: float,
    generated_at: str,
    timestamp: str,
    runner: SlurmRunner | None,
    wait: bool,
) -> None:
    validation_dir = stage_dir(config.work_dir, "validation")
    with tempfile.TemporaryDirectory(dir=config.work_dir, prefix=".validation-") as tmp_dir:
        angles, atoms_list = structures.make_twist_struct(config.min_val_n, config.max_val_n, tmp_dir)
    directories = [validation_dir / angle for angle in angles]
    backups = _backup_targets(config.work_dir, "validation", directories, timestamp)
    for angle, atoms in zip(angles, atoms_list, strict=True):
        target = validation_dir / angle
        target.mkdir(parents=True, exist_ok=True)
        write_vasp(target / "POSCAR", atoms=atoms)
        _write_vasp_inputs(config, target, atoms, config.input_dir / "val_INCAR", (1, 1), rcut)
    jobs = _submit_dirs(config, runner, directories, wait)
    write_manifest(
        config.work_dir,
        Manifest(
            stage="validation",
            generated_at=generated_at,
            config_summary=_config_summary(config),
            directories=[relative_to_workdir(config.work_dir, path) for path in directories],
            backups=backups,
            jobs=[job.as_dict() for job in jobs],
            angles=list(angles),
        ),
    )


def _write_vasp_inputs(
    config: DPmoireLiteConfig,
    output_dir: Path,
    atoms: Atoms,
    incar_template: Path,
    k_scale: tuple[int, int],
    rcut: float,
) -> None:
    elements = _ordered_elements(atoms)
    max_enmax = write_potcar(elements, config.potcar_dir, output_dir / "POTCAR")
    render_incar(
        incar_template,
        output_dir / "INCAR",
        encut=max_enmax * config.encut_factor,
        rcut1=rcut,
        rcut2=rcut,
        elements=elements,
    )
    write_kpoints(output_dir, atoms.cell.array, config.k_mesh, k_scale)
    copy_submit_script(config.script_dir, config.dft_script, output_dir)
    copy_vdw_if_needed(incar_template, config.input_dir, output_dir)


def _ordered_elements(atoms: Atoms) -> list[str]:
    return get_ordered_elements(atoms)


def _resolve_rcut(config: DPmoireLiteConfig, top_atoms: Atoms | None, bot_atoms: Atoms | None) -> float:
    if config.r_cut >= 0:
        return config.r_cut
    if top_atoms is None or bot_atoms is None:
        raise ValueError("Input layer cells are required to resolve default r_cut")
    max_layer_a = max(
        float(np.linalg.norm(atoms.cell.array[index]))
        for atoms in (top_atoms, bot_atoms)
        for index in (0, 1)
    )
    return float(np.sqrt(max_layer_a**2 + config.d**2) * 1.1)


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
    jobs = [runner.submit(path, relative_to_workdir(config.work_dir, path)) for path in directories]
    return runner.wait(jobs) if wait else jobs


def _config_summary(config: DPmoireLiteConfig) -> dict[str, object]:
    return {
        "stage": config.stage,
        "n_sectors": list(config.n_sectors),
        "sc": list(config.sc),
        "d": config.d,
        "k_mesh": config.k_mesh,
        "encut_factor": config.encut_factor,
        "r_cut": config.r_cut,
    }
