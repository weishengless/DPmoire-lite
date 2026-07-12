from __future__ import annotations

import shutil
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
from .build_preflight import preflight_stage0, preflight_stage1
from .config import ConfigError, DPmoireLiteConfig, load_config
from .inputs import (
    copy_submit_script,
    copy_vdw_if_needed,
    get_ordered_elements,
    render_incar,
    stage_mlff_files,
    write_kpoints,
    write_potcar,
    write_supercell_poscar,
)
from .manifest import Manifest, read_manifest, write_manifest
from .mlab import seed_prefix_identity
from .paths import backup_existing_directory, manifest_path, relative_to_workdir, stage_dir
from .provenance import structure_identity
from .slurm import SlurmJob, SlurmRunner
from .structures import StructureHandler, generate_stackings, supercell_matrix


VASP_RELAXATION_CONVERGED_PHRASE = "reached required accuracy - stopping structural energy minimisation"


def _stage0_target_stages(config: DPmoireLiteConfig) -> list[tuple[str, Path]]:
    targets = []
    if config.init_mlff:
        targets.append(("init_mlff", stage_dir(config.work_dir, "init_mlff")))
    if config.do_relaxation:
        targets.append(("rlx", stage_dir(config.work_dir, "rlx")))
    if config.twist_val:
        targets.append(("validation", stage_dir(config.work_dir, "validation")))
    return targets


def _stage1_target_stages(config: DPmoireLiteConfig) -> list[tuple[str, Path]]:
    return [("md", stage_dir(config.work_dir, "md"))]


def _check_target_stages_absent(config: DPmoireLiteConfig, targets: list[tuple[str, Path]]) -> None:
    conflicts = [(stage, path) for stage, path in targets if path.exists()]
    if not conflicts:
        return

    details = "\n".join(
        f"- {stage} ({relative_to_workdir(config.work_dir, path)}) already exists"
        for stage, path in conflicts
    )
    raise RuntimeError(
        "Build target conflict(s):\n"
        f"{details}\n"
        "DPmoire-lite does not rebuild stages in place. "
        "Delete the listed stage directories and rerun the build. "
        "No files were modified."
    )


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
    config.validate_build_mode(wait)
    _check_target_stages_absent(config, _stage0_target_stages(config))
    preflight_stage0(config)
    generated_at = datetime.now().isoformat(timespec="seconds")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    config.work_dir.mkdir(parents=True, exist_ok=True)
    structures = StructureHandler(
        config.input_dir,
        config.work_dir,
        config.n_sectors,
        config.d,
        config.d_mode,
        config.d_reference,
    )
    rcut = _resolve_rcut(config, structures.top_atoms, structures.bot_atoms)
    stackings = (
        structures.find_sym_reduced_stackings()
        if config.symm_reduce
        else generate_stackings(config.n_sectors)
    )
    if config.symm_reduce:
        structures.write_sym_reduced_stackings(stackings)
    runner = SlurmRunner(config.dft_script, config.n_nodes, config.auto_resub) if config.submit else None

    if config.init_mlff:
        _build_init_mlff(config, structures, rcut, generated_at, timestamp, runner, wait)
    if config.do_relaxation:
        _build_relaxations(config, structures, stackings, rcut, generated_at, timestamp, runner, wait)
    if config.twist_val:
        _build_validation(config, structures, rcut, generated_at, timestamp, runner, wait)


def build_stage1(config: DPmoireLiteConfig, wait: bool = False, runner: SlurmRunner | None = None) -> None:
    config.validate_build_mode(wait)
    _check_target_stages_absent(config, _stage1_target_stages(config))
    stackings = _stage1_stackings(config)
    preflight = preflight_stage1(config, stackings)
    mlff_seed = _stage1_mlff_seed_identity(config, preflight)
    generated_at = datetime.now().isoformat(timespec="seconds")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    config.work_dir.mkdir(parents=True, exist_ok=True)
    structures = StructureHandler(
        config.input_dir,
        config.work_dir,
        config.n_sectors,
        config.d,
        config.d_mode,
        config.d_reference,
    )
    rcut = _resolve_rcut(config, structures.top_atoms, structures.bot_atoms)
    md_dir = stage_dir(config.work_dir, "md")
    targets = [md_dir / f"{i}_{j}" for i, j in stackings]
    if config.include_monolayer_md:
        targets.extend([md_dir / "top_layer", md_dir / "bot_layer"])
    backups = []

    directories = []
    md_anchor_records: dict[str, list[dict[str, object]]] = {}
    init_mlff_dir = config.work_dir / "init_mlff"
    if preflight.trusted_sc_rlx is None:
        md_sc = None if config.sc_rlx else config.sc
    else:
        md_sc = None if preflight.trusted_sc_rlx else preflight.trusted_sc
    for i, j in stackings:
        source_dir = config.work_dir / "rlx" / f"{i}_{j}"
        target = md_dir / f"{i}_{j}"
        target.mkdir(parents=True, exist_ok=True)
        anchor_records = _write_md_poscar(
            source_dir / "CONTCAR",
            target / "POSCAR",
            md_sc,
            preserve_constraints=config.preserve_grid_shift_md,
        )
        if anchor_records is not None:
            md_anchor_records[relative_to_workdir(config.work_dir, target)] = anchor_records
        atoms = structures.read_atoms(target / "POSCAR")
        _write_vasp_inputs(config, target, atoms, config.input_dir / "MD_INCAR", rcut)
        if config.vasp_ml:
            stage_mlff_files(init_mlff_dir, target)
            _verify_staged_mlff_files(init_mlff_dir, target, mlff_seed)
        directories.append(target)

    monolayer_constraint_warning_emitted = False
    if config.include_monolayer_md:
        for layer_name in ("top_layer", "bot_layer"):
            target = md_dir / layer_name
            target.mkdir(parents=True, exist_ok=True)
            atoms = structures.read_atoms(config.input_dir / f"{layer_name}.poscar")
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
            _write_vasp_inputs(config, target, atoms_sc, config.input_dir / "MD_monolayer_INCAR", rcut)
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
            config_summary=_config_summary(config),
            directories=[relative_to_workdir(config.work_dir, path) for path in directories],
            backups=backups,
            jobs=[job.as_dict() for job in jobs],
            stackings=[[i, j] for i, j in stackings],
            structure_provenance=_stage1_structure_provenance(preflight),
            grid_shift_anchors=md_anchor_records,
            mlff_seed=mlff_seed,
        ),
    )


def _stage1_structure_provenance(preflight) -> dict[str, object]:
    if preflight.provenance_kind is None:
        return {}
    return {
        "mode": preflight.provenance_kind,
        "sc_rlx": preflight.trusted_sc_rlx,
        "sc": list(preflight.trusted_sc) if preflight.trusted_sc is not None else None,
        "evidence": preflight.provenance_evidence,
    }


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

    generated_at = datetime.now().isoformat(timespec="seconds")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    config.work_dir.mkdir(parents=True, exist_ok=True)
    structures = StructureHandler(
        config.input_dir,
        config.work_dir,
        config.n_sectors,
        config.d,
        config.d_mode,
        config.d_reference,
    )
    rcut = _resolve_rcut(config, structures.top_atoms, structures.bot_atoms)
    stackings = (
        structures.find_sym_reduced_stackings()
        if config.symm_reduce
        else generate_stackings(config.n_sectors)
    )
    runner = (
        runner_factory(config)
        if runner_factory is not None
        else SlurmRunner(config.dft_script, config.n_nodes, config.auto_resub)
    )

    if config.init_mlff:
        _build_init_mlff(config, structures, rcut, generated_at, timestamp, runner, wait=True)
    if config.do_relaxation:
        _build_relaxations(config, structures, stackings, rcut, generated_at, timestamp, runner, wait=True)
    if config.twist_val:
        _build_validation(config, structures, rcut, generated_at, timestamp, runner, wait=True)

    build_stage1(config, wait=True, runner=runner)


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
    rcut: float,
    generated_at: str,
    timestamp: str,
    runner: SlurmRunner | None,
    wait: bool,
) -> None:
    init_dir = stage_dir(config.work_dir, "init_mlff")
    backups = []
    init_dir.mkdir(parents=True, exist_ok=True)
    write_supercell_poscar(config.input_dir / "bot_layer.poscar", init_dir / "POSCAR", config.sc)
    atoms = structures.read_atoms(init_dir / "POSCAR")
    _write_vasp_inputs(config, init_dir, atoms, config.input_dir / "init_INCAR", rcut)
    jobs = _submit_dirs(config, runner, [init_dir], wait)
    if runner is not None and wait:
        prepare_init_mlff_step2(init_dir, config.input_dir, config.sc)
        jobs.extend(_submit_dirs(config, runner, [init_dir], wait=True))
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
    backups = []
    directories = []
    rlx_poscars = {}
    grid_shift_anchors = {}
    for i, j in stackings:
        target = rlx_dir / f"{i}_{j}"
        target.mkdir(parents=True, exist_ok=True)
        atoms = structures.shift_atoms(i, j, c_constrain=True, sc=config.sc) if config.sc_rlx else structures.shift_primitive_atoms(i, j)
        write_vasp(target / "POSCAR", atoms=atoms)
        relative_path = relative_to_workdir(config.work_dir, target / "POSCAR")
        identity = _structure_identity_record(target / "POSCAR", relative_path)
        rlx_poscars[relative_path] = identity
        top_indexes, bot_indexes = structures.find_layer_idx(atoms)
        anchor_relative_path = relative_to_workdir(config.work_dir, target)
        grid_shift_anchors[anchor_relative_path] = _grid_shift_anchor_record(
            target / "POSCAR",
            identity,
            top_indexes,
            bot_indexes,
        )
        _write_vasp_inputs(config, target, atoms, config.input_dir / "rlx_INCAR", rcut)
        directories.append(target)
    jobs = _submit_dirs(config, runner, directories, wait)
    provenance = _stage0_structure_provenance(
        config,
        structures,
        stackings,
        rlx_poscars,
        grid_shift_anchors,
    )
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
            structure_provenance=provenance,
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
    tmp_dir = _make_temp_work_dir(config.work_dir, "validation", timestamp)
    try:
        angles, atoms_list = structures.make_twist_struct(config.min_val_n, config.max_val_n, tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    directories = [validation_dir / angle for angle in angles]
    backups = []
    for angle, atoms in zip(angles, atoms_list, strict=True):
        target = validation_dir / angle
        target.mkdir(parents=True, exist_ok=True)
        write_vasp(target / "POSCAR", atoms=atoms)
        _write_vasp_inputs(config, target, atoms, config.input_dir / "val_INCAR", rcut)
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


def _stage1_stackings(config: DPmoireLiteConfig) -> list[tuple[int, int]]:
    path = manifest_path(config.work_dir, "rlx")
    result = read_manifest(config.work_dir, "rlx")
    if result.kind == "missing":
        raise RuntimeError(
            f"Missing relaxation manifest at {path}; Stage1 cannot determine its stacking list. "
            "The relaxation stage may be an incomplete build; rebuild it before Stage1."
        )
    if result.kind == "legacy":
        manifest_data = result.raw_data or {}
        manifest_stackings = manifest_data.get("stackings")
        if not manifest_stackings:
            raise ValueError(
                f"Invalid legacy relaxation manifest at {path}; stackings must not be empty."
            )
    else:
        manifest = result.manifest
        if manifest is None:
            raise RuntimeError(f"Invalid relaxation manifest at {path}; no manifest data was loaded.")
        manifest_stackings = manifest.stackings

    if not manifest_stackings:
        raise ValueError(f"Invalid relaxation manifest at {path}; stackings must not be empty.")

    stackings: list[tuple[int, int]] = []
    for stacking in manifest_stackings:
        if len(stacking) != 2:
            raise ValueError(
                f"Invalid relaxation manifest at {path}; each stacking must contain two indices."
            )
        stackings.append((int(stacking[0]), int(stacking[1])))
    return stackings


def _make_temp_work_dir(work_dir: Path, name: str, timestamp: str) -> Path:
    base = Path(work_dir) / f".{name}-{timestamp}"
    candidate = base
    counter = 1
    while candidate.exists():
        candidate = Path(f"{base}-{counter}")
        counter += 1
    candidate.mkdir(parents=True)
    return candidate


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
    contcar: Path,
    poscar: Path,
    sc: tuple[int, int] | None,
    *,
    preserve_constraints: bool = False,
) -> list[dict[str, object]] | None:
    atoms = read_vasp(contcar)
    source_anchor_indices = (
        _constraint_indices(atoms) if preserve_constraints else []
    )
    _normalize_stage1_structure(atoms, clear_constraints=not preserve_constraints)
    if sc is None:
        if not preserve_constraints:
            _assert_stage1_structure_cleared(atoms)
        poscar.parent.mkdir(parents=True, exist_ok=True)
        write_vasp(poscar, atoms=atoms, direct=True, sort=False)
        if not preserve_constraints:
            return None
        return [
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
        return None

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
    return anchor_records


def _constraint_indices(atoms: Atoms) -> list[int]:
    indices = []
    for constraint in atoms.constraints:
        values = np.asarray(getattr(constraint, "index", []), dtype=int).reshape(-1)
        indices.extend(int(value) for value in values)
    return sorted(set(indices))


def _write_vasp_inputs(
    config: DPmoireLiteConfig,
    output_dir: Path,
    atoms: Atoms,
    incar_template: Path,
    rcut: float,
) -> None:
    elements = _ordered_elements(atoms)
    max_enmax = write_potcar(elements, config.potcar_dir, output_dir / "POTCAR", potcar_policy=config.potcar_policy)
    render_incar(
        incar_template,
        output_dir / "INCAR",
        encut=max_enmax * config.encut_factor,
        rcut1=rcut,
        rcut2=rcut,
        elements=elements,
    )
    write_kpoints(output_dir, atoms.cell.array, config.k_mesh)
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
    items = [(path, relative_to_workdir(config.work_dir, path)) for path in directories]
    if hasattr(runner, "submit_many"):
        return runner.submit_many(items, wait=wait)
    jobs = [runner.submit(path, relative_to_workdir(config.work_dir, path)) for path in directories]
    return runner.wait(jobs) if wait else jobs


def _config_summary(config: DPmoireLiteConfig) -> dict[str, object]:
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
        "r_cut": config.r_cut,
    }


def _structure_identity_record(
    path: Path,
    relative_path: str,
    atoms: Atoms | None = None,
) -> dict[str, object]:
    identity = structure_identity(path, atoms)
    return {
        "path": relative_path,
        "sha256": identity.sha256,
        "atom_count": identity.atom_count,
        "ordered_elements": list(identity.ordered_elements),
        "composition": dict(identity.composition),
        "cell": [list(row) for row in identity.cell],
    }


def _grid_shift_anchor_record(
    path: Path,
    identity: dict[str, object],
    top_indexes: list[int],
    bot_indexes: list[int],
) -> dict[str, object]:
    atoms = read_vasp(path)
    entries: dict[int, list[bool]] = {}
    for constraint in atoms.constraints:
        indexes = np.asarray(getattr(constraint, "index", []), dtype=int).reshape(-1)
        mask_value = getattr(constraint, "mask", None)
        if indexes.size == 0 or mask_value is None:
            raise RuntimeError(
                f"Stage0 grid-shift constraints in {path} cannot be represented by fixed_masks"
            )
        mask = np.asarray(mask_value, dtype=bool).reshape(-1)
        if mask.shape != (3,) or mask.tolist() != [True, True, False]:
            raise RuntimeError(
                f"Stage0 grid-shift constraints in {path} must use fixed mask [true, true, false]"
            )
        for index in indexes:
            index = int(index)
            if index < 0 or index >= len(atoms) or index in entries:
                raise RuntimeError(
                    f"Stage0 grid-shift constraints in {path} contain invalid or duplicate anchor indices"
                )
            entries[index] = mask.tolist()

    top_anchors = sorted(set(top_indexes).intersection(entries))
    bot_anchors = sorted(set(bot_indexes).intersection(entries))
    if len(entries) != 2 or len(top_anchors) != 1 or len(bot_anchors) != 1:
        raise RuntimeError(
            f"Stage0 grid-shift constraints in {path} must contain exactly one top and one bottom anchor"
        )

    top_index = top_anchors[0]
    bottom_index = bot_anchors[0]
    return {
        "atom_count": int(identity["atom_count"]),
        "poscar_sha256": str(identity["sha256"]),
        "top_index": top_index,
        "bottom_index": bottom_index,
        "fixed_masks": {
            top_index: entries[top_index],
            bottom_index: entries[bottom_index],
        },
    }


def _stage0_structure_provenance(
    config: DPmoireLiteConfig,
    structures: StructureHandler,
    stackings: list[tuple[int, int]],
    rlx_poscars: dict[str, dict[str, object]],
    grid_shift_anchors: dict[str, dict[str, object]],
) -> dict[str, object]:
    if structures.top_atoms is None or structures.bot_atoms is None:
        raise RuntimeError("Stage0 structure provenance requires loaded top and bottom inputs")

    summary = _config_summary(config)
    input_paths = {
        "input/top_layer.poscar": (config.input_dir / "top_layer.poscar", structures.top_atoms),
        "input/bot_layer.poscar": (config.input_dir / "bot_layer.poscar", structures.bot_atoms),
    }
    inputs = {
        relative_path: _structure_identity_record(path, relative_path, atoms)
        for relative_path, (path, atoms) in input_paths.items()
    }
    stacking_values = [[int(i), int(j)] for i, j in stackings]
    return {
        "schema": "dpmoire-lite.structure-provenance.v1",
        "stage": 0,
        "sc_rlx": bool(config.sc_rlx),
        "sc": list(config.sc),
        "sc_semantics": (
            "stage0_relaxation_supercell"
            if config.sc_rlx
            else "stage0_relaxation_primitive"
        ),
        "n_sectors": list(config.n_sectors),
        "symm_reduce": bool(config.symm_reduce),
        "d": config.d,
        "d_mode": config.d_mode,
        "d_reference": summary["d_reference"],
        "stackings": stacking_values,
        "inputs": inputs,
        "rlx_poscars": rlx_poscars,
        "grid_shift_anchors": grid_shift_anchors,
    }
