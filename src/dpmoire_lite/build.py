from __future__ import annotations

import hashlib
import shutil
import tempfile
import warnings
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from ase.build import make_supercell, sort
from ase import Atoms
from ase.constraints import FixedLine
from ase.io.vasp import write_vasp

from .atomic_io import (
    PinnedDirectory,
    atomic_bytes_publish,
    atomic_directory_publish_no_replace,
    atomic_text_publish,
    claim_pinned_directory_no_replace,
    sha256_file,
)
from .build_preflight import (
    BuildPreflightResult,
    PreparedInitialSeed,
    PreparedInitMlffWorkflow,
    PreparedValidationStructure,
    PreparedWorkflowCutoff,
    check_target_stages_absent,
    preflight_stage0,
    preflight_stage1,
)
from .build_lock import build_execution_lock
from .config import ConfigError, DPmoireLiteConfig, load_config
from .inputs import (
    PreparedIncarTemplate,
    PreparedSource,
    copy_prepared_source,
    get_ordered_elements,
    render_prepared_incar,
    verify_prepared_source,
    write_kpoints,
    write_prepared_potcar,
    write_supercell_poscar,
)
from .init_mlff import init_mlff_manifest_lock
from .manifest import (
    INIT_WORKFLOW_SCHEMA,
    Manifest,
    write_manifest,
    write_manifest_to_directory,
)
from .mlab import seed_prefix_identity
from .paths import relative_to_workdir
from . import provenance as provenance_module
from .slurm import SlurmJob, SlurmRunner, render_array_submission_script
from .structures import StructureHandler, supercell_matrix


class InitMlffSubmissionError(RuntimeError):
    """The single-job init workspace exists, but its sbatch request failed."""


@dataclass(frozen=True)
class BuildOutcome:
    stage: int | str
    status: str


def run_build(config_path: Path, wait: bool = False) -> BuildOutcome:
    config = load_config(config_path)
    config.validate_build_mode(wait)
    submitted = False
    if config.stage == 0:
        submitted = build_stage0(config, wait=wait)
    elif config.stage == 1:
        submitted = build_stage1(config, wait=wait)
    elif config.stage == "all":
        build_stage_all(config, wait=wait)
    return BuildOutcome(
        stage=config.stage,
        status="submission_requested" if submitted else "generated",
    )


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


def _verify_work_directory(
    work_dir: Path,
    expected: PinnedDirectory,
) -> PinnedDirectory:
    if Path(work_dir) != expected.path:
        raise RuntimeError("Build work directory disagrees with its pinned path")
    try:
        expected.verify()
    except OSError as exc:
        raise RuntimeError(
            f"Build work directory is unsafe; refusing to write it: {work_dir}"
        ) from exc
    return expected


@contextmanager
def _claim_output_directory(
    target: Path,
    parent: PinnedDirectory,
) -> Iterator[PinnedDirectory]:
    with ExitStack() as stack:
        try:
            claimed = stack.enter_context(
                claim_pinned_directory_no_replace(target, parent=parent)
            )
        except FileExistsError as exc:
            raise RuntimeError(
                "Build target appeared after preflight; refusing to overwrite it: "
                f"{target}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"Build target is unsafe after preflight; refusing to write it: {target}"
            ) from exc
        yield claimed


@contextmanager
def _claim_stage_roots(
    config: DPmoireLiteConfig,
    stage_targets: tuple[tuple[str, Path], ...],
    work_directory: PinnedDirectory,
    *,
    deferred_stages: frozenset[str] = frozenset(),
) -> Iterator[dict[str, PinnedDirectory]]:
    pending = tuple(
        (stage, target)
        for stage, target in stage_targets
        if stage not in deferred_stages
    )
    check_target_stages_absent(config, pending)
    with ExitStack() as stack:
        claimed = {}
        for stage, target in pending:
            claimed[stage] = stack.enter_context(
                _claim_output_directory(target, work_directory)
            )
        yield claimed


@contextmanager
def _claim_output_directories(
    targets: tuple[Path, ...],
    stage_root: PinnedDirectory,
) -> Iterator[tuple[PinnedDirectory, ...]]:
    with ExitStack() as stack:
        yield tuple(
            stack.enter_context(_claim_output_directory(target, stage_root))
            for target in targets
        )


def _verify_claimed_directory(
    target: Path,
    claimed: PinnedDirectory,
) -> None:
    if target != claimed.path:
        raise RuntimeError("Build target disagrees with its claimed directory identity")
    try:
        claimed.verify()
    except OSError as exc:
        raise RuntimeError(
            f"Build target identity is unsafe; refusing to write it: {target}"
        ) from exc


@contextmanager
def _verified_build_directory(
    claimed: PinnedDirectory,
) -> Iterator[PinnedDirectory]:
    _verify_claimed_directory(claimed.path, claimed)
    try:
        yield claimed
    finally:
        _verify_claimed_directory(claimed.path, claimed)


def _write_claimed_manifest(
    work_dir: Path,
    stage_root: PinnedDirectory,
    manifest: Manifest,
) -> None:
    with _verified_build_directory(stage_root) as output_directory:
        write_manifest_to_directory(work_dir, output_directory.write_path, manifest)
        output_directory.verify()


def build_stage0(config: DPmoireLiteConfig, wait: bool = False) -> bool:
    config.validate_build_mode(wait)
    preflight = preflight_stage0(config)
    with build_execution_lock(config.work_dir) as work_directory:
        return _build_stage0_after_preflight(
            config,
            preflight,
            wait,
            work_directory,
        )


def _build_stage0_after_preflight(
    config: DPmoireLiteConfig,
    preflight: BuildPreflightResult,
    wait: bool,
    expected_work_directory: PinnedDirectory,
) -> bool:
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
    init_dir_count = (
        len(preflight.init_mlff_workflow.phases)
        if preflight.init_mlff_workflow is not None
        else int(config.init_mlff)
    )
    init_dirs = _take_prepared_output_dirs(
        output_dirs,
        init_dir_count,
        "init_mlff",
    )
    if preflight.init_mlff_workflow is not None:
        prepared_init_dirs = tuple(
            phase.target_dir for phase in preflight.init_mlff_workflow.phases
        )
        if init_dirs != prepared_init_dirs:
            raise RuntimeError(
                "Stage0 preflight returned inconsistent init MLFF output directories"
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
    work_directory = _verify_work_directory(
        config.work_dir,
        expected_work_directory,
    )
    with ExitStack() as claims:
        stage_roots = claims.enter_context(
            _claim_stage_roots(
                config,
                preflight.stage_targets,
                work_directory,
                deferred_stages=(
                    frozenset({"init_mlff"})
                    if preflight.init_mlff_workflow is not None
                    else frozenset()
                ),
            )
        )
        init_directory = (
            stage_roots["init_mlff"]
            if config.init_mlff and preflight.init_mlff_workflow is None
            else None
        )
        relaxation_directories = (
            claims.enter_context(
                _claim_output_directories(relaxation_dirs, stage_roots["rlx"])
            )
            if config.do_relaxation
            else ()
        )
        validation_directories = (
            claims.enter_context(
                _claim_output_directories(
                    validation_dirs,
                    stage_roots["validation"],
                )
            )
            if config.twist_val
            else ()
        )
        _verify_claimed_directory(config.work_dir, work_directory)
        if config.symm_reduce:
            structures.write_sym_reduced_stackings(
                list(stackings),
                output_dir=work_directory.write_path,
            )
            work_directory.verify()
        runner = (
            SlurmRunner(config.dft_script, config.n_nodes, config.auto_resub)
            if config.submit
            else None
        )
        submitted = False

        if preflight.init_mlff_workflow is not None:
            submitted = _build_two_phase_init_mlff(
                config,
                preflight.init_mlff_workflow,
                workflow_cutoff,
                preflight.submit_script,
                rcut,
                generated_at,
                runner,
                wait,
            ) or submitted
        elif config.init_mlff:
            if init_directory is None:
                raise RuntimeError("Stage0 did not claim the legacy init MLFF target")
            submitted = _build_init_mlff(
                config,
                structures,
                init_directory,
                _prepared_template(templates, "init_INCAR"),
                workflow_cutoff,
                preflight.submit_script,
                rcut,
                generated_at,
                runner,
                wait,
            ) or submitted
        if config.do_relaxation:
            submitted = _build_relaxations(
                config,
                structures,
                stackings,
                relaxation_directories,
                stage_roots["rlx"],
                _prepared_template(templates, "rlx_INCAR"),
                workflow_cutoff,
                preflight.submit_script,
                rcut,
                generated_at,
                runner,
                wait,
            ) or submitted
        if config.twist_val:
            submitted = _build_validation(
                config,
                preflight.validation_structures,
                validation_directories,
                stage_roots["validation"],
                _prepared_template(templates, "val_INCAR"),
                workflow_cutoff,
                preflight.submit_script,
                rcut,
                generated_at,
                runner,
                wait,
            ) or submitted
        return submitted


def build_stage1(
    config: DPmoireLiteConfig,
    wait: bool = False,
    runner: SlurmRunner | None = None,
) -> bool:
    config.validate_build_mode(wait)
    preflight = preflight_stage1(config)
    with build_execution_lock(config.work_dir) as work_directory:
        return _build_stage1_after_preflight(
            config,
            preflight,
            wait,
            runner,
            work_directory,
        )


def _build_stage1_after_preflight(
    config: DPmoireLiteConfig,
    preflight: BuildPreflightResult,
    wait: bool,
    runner: SlurmRunner | None,
    expected_work_directory: PinnedDirectory,
) -> bool:
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
    initial_seed = _stage1_prepared_seed(config, preflight)
    mlff_seed = _stage1_mlff_seed_identity(config, initial_seed)
    generated_at = datetime.now().isoformat(timespec="seconds")
    work_directory = _verify_work_directory(
        config.work_dir,
        expected_work_directory,
    )
    with ExitStack() as claims:
        stage_roots = claims.enter_context(
            _claim_stage_roots(
                config,
                preflight.stage_targets,
                work_directory,
            )
        )
        md_root = stage_roots["md"]
        relaxation_output_directories = claims.enter_context(
            _claim_output_directories(
                relaxation_output_dirs,
                md_root,
            )
        )
        monolayer_output_directories = claims.enter_context(
            _claim_output_directories(
                monolayer_output_dirs,
                md_root,
            )
        )
        backups = []

        directories = []
        md_anchor_records: dict[str, list[dict[str, object]]] = {}
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
        for relaxation_index, (relaxation, target_directory) in enumerate(
            zip(
                preflight.relaxations,
                relaxation_output_directories,
                strict=True,
            )
        ):
            target = target_directory.path
            with _verified_build_directory(target_directory) as output_directory:
                atoms, anchor_records = _write_md_poscar(
                    relaxation.atoms.copy(),
                    output_directory.write_path / "POSCAR",
                    md_sc,
                    preserve_constraints=config.preserve_grid_shift_md,
                    validated_anchor_indices=(
                        validated_anchor_indices[relaxation_index]
                        if config.preserve_grid_shift_md
                        else None
                    ),
                )
                output_directory.verify()
                if anchor_records is not None:
                    md_anchor_records[
                        relative_to_workdir(config.work_dir, target)
                    ] = anchor_records
                _write_vasp_inputs(
                    config,
                    output_directory.write_path,
                    atoms,
                    _prepared_template(templates, "MD_INCAR"),
                    rcut,
                    workflow_cutoff,
                    preflight.submit_script,
                )
                output_directory.verify()
                if config.vasp_ml:
                    _stage_prepared_mlff_files(
                        initial_seed,
                        output_directory.write_path,
                    )
                    _verify_staged_mlff_files(
                        initial_seed,
                        output_directory.write_path,
                        mlff_seed,
                    )
                    output_directory.verify()
            directories.append(target)

        monolayer_constraint_warning_emitted = False
        if config.include_monolayer_md:
            layer_sources = {
                "top_layer": structures.top_atoms,
                "bot_layer": structures.bot_atoms,
            }
            for (layer_name, source_atoms), target_directory in zip(
                layer_sources.items(),
                monolayer_output_directories,
                strict=True,
            ):
                if source_atoms is None:
                    raise RuntimeError(
                        f"Stage1 preflight did not return {layer_name} atoms"
                    )
                target = target_directory.path
                with _verified_build_directory(target_directory) as output_directory:
                    atoms = source_atoms.copy()
                    had_constraints = _normalize_stage1_structure(
                        atoms,
                        clear_constraints=True,
                        warn_on_constraints=not monolayer_constraint_warning_emitted,
                    )
                    if had_constraints:
                        monolayer_constraint_warning_emitted = True
                    atoms_sc = sort(
                        make_supercell(prim=atoms, P=supercell_matrix(config.sc))
                    )
                    _assert_stage1_structure_cleared(atoms_sc)
                    write_vasp(
                        output_directory.write_path / "POSCAR",
                        atoms=atoms_sc,
                    )
                    output_directory.verify()
                    _write_vasp_inputs(
                        config,
                        output_directory.write_path,
                        atoms_sc,
                        _prepared_template(templates, "MD_monolayer_INCAR"),
                        rcut,
                        workflow_cutoff,
                        preflight.submit_script,
                    )
                    output_directory.verify()
                    if config.vasp_ml:
                        _stage_prepared_mlff_files(
                            initial_seed,
                            output_directory.write_path,
                        )
                        _verify_staged_mlff_files(
                            initial_seed,
                            output_directory.write_path,
                            mlff_seed,
                        )
                        output_directory.verify()
                directories.append(target)

        array_scripts = _maybe_array_scripts(
            config,
            md_root,
            _md_array_folders(stackings, config.include_monolayer_md),
            preflight.submit_script,
        )
        if not config.submit:
            runner = None
        elif runner is None:
            runner = SlurmRunner(config.dft_script, config.n_nodes, config.auto_resub)
        for target_directory in (
            relaxation_output_directories + monolayer_output_directories
        ):
            _verify_claimed_directory(target_directory.path, target_directory)
        jobs = _submit_dirs(
            config,
            runner,
            list(relaxation_output_directories + monolayer_output_directories),
            wait,
        )
        _write_claimed_manifest(
            config.work_dir,
            md_root,
            Manifest(
                stage="md",
                generated_at=generated_at,
                config_summary=_config_summary(config, workflow_cutoff),
                directories=[
                    relative_to_workdir(config.work_dir, path) for path in directories
                ],
                backups=backups,
                jobs=[job.as_dict() for job in jobs],
                stackings=[[i, j] for i, j in stackings],
                structure_provenance=provenance_module.stage1_structure_provenance(
                    preflight.provenance
                ),
                grid_shift_anchors=md_anchor_records,
                mlff_seed=mlff_seed,
                array_scripts=array_scripts,
            ),
        )
        return bool(jobs)


def _stage1_mlff_seed_identity(
    config: DPmoireLiteConfig,
    prepared: PreparedInitialSeed | None,
) -> dict[str, object]:
    if not config.vasp_ml:
        return {}
    if prepared is None:
        raise RuntimeError("Stage1 MLFF seed preflight did not return a complete seed")

    seed_identity = seed_prefix_identity(prepared.parsed)
    return {
        "source": "init_mlff/ML_ABN",
        "configurations": prepared.parsed.complete_count,
        "digest_schema": seed_identity.schema,
        "seed_prefix_sha256": seed_identity.sha256,
        "ml_ab_sha256": prepared.ml_ab.sha256,
        "ml_ff_sha256": prepared.ml_ff.sha256,
    }


def _stage1_prepared_seed(
    config: DPmoireLiteConfig,
    preflight,
) -> PreparedInitialSeed | None:
    if not config.vasp_ml:
        return None
    prepared = preflight.initial_seed
    if prepared is None:
        raise RuntimeError("Stage1 MLFF seed preflight did not return a complete seed")
    expected_ml_ab = (config.work_dir / "init_mlff" / "ML_ABN").resolve(
        strict=False
    )
    expected_ml_ff = (config.work_dir / "init_mlff" / "ML_FFN").resolve(
        strict=False
    )
    if (
        prepared.ml_ab.path.resolve(strict=False) != expected_ml_ab
        or prepared.ml_ff.path.resolve(strict=False) != expected_ml_ff
    ):
        raise RuntimeError("Stage1 MLFF seed preflight did not return bounded sources")
    verify_prepared_source(prepared.ml_ab)
    verify_prepared_source(prepared.ml_ff)
    return prepared


def _stage_prepared_mlff_files(
    prepared: PreparedInitialSeed | None,
    output_dir: Path,
) -> None:
    if prepared is None:
        raise RuntimeError("Stage1 MLFF staging requires a prepared seed")
    verify_prepared_source(prepared.ml_ab)
    verify_prepared_source(prepared.ml_ff)
    copy_prepared_source(prepared.ml_ab, Path(output_dir) / "ML_AB")
    copy_prepared_source(prepared.ml_ff, Path(output_dir) / "ML_FF")


def _verify_staged_mlff_files(
    prepared: PreparedInitialSeed | None,
    output_dir: Path,
    seed_identity: dict[str, object],
) -> None:
    if prepared is None:
        raise RuntimeError("Stage1 MLFF verification requires a prepared seed")
    _verify_staged_mlff_file(
        prepared.ml_ab,
        Path(output_dir) / "ML_AB",
        seed_identity["ml_ab_sha256"],
    )
    _verify_staged_mlff_file(
        prepared.ml_ff,
        Path(output_dir) / "ML_FF",
        seed_identity["ml_ff_sha256"],
    )


def _verify_staged_mlff_file(
    source: PreparedSource,
    destination: Path,
    recorded_sha256: object,
) -> None:
    if not destination.is_file():
        raise RuntimeError(
            f"Stage1 MLFF copy verification failed for {destination}: missing file"
        )
    destination_size = destination.stat().st_size
    if source.size != destination_size:
        raise RuntimeError(
            f"Stage1 MLFF copy verification failed for {destination}: "
            f"size {destination_size} != {source.size}"
        )
    destination_hash = sha256_file(destination)
    if source.sha256 != recorded_sha256:
        raise RuntimeError("Stage1 MLFF seed identity disagrees with preflight")
    if destination_hash != source.sha256:
        raise RuntimeError(
            f"Stage1 MLFF copy verification failed for {destination}: "
            f"hash {destination_hash} != {source.sha256}"
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


def prepare_init_mlff_step2(init_dir: Path, input_dir: Path, sc: tuple[int, int]) -> None:
    init_dir = Path(init_dir)
    input_dir = Path(input_dir)
    (init_dir / "ML_ABN").replace(init_dir / "ML_AB")
    (init_dir / "ML_FFN").replace(init_dir / "ML_FF")
    write_supercell_poscar(input_dir / "top_layer.poscar", init_dir / "POSCAR", sc)


def _build_init_mlff(
    config: DPmoireLiteConfig,
    structures: StructureHandler,
    init_directory: PinnedDirectory,
    template: PreparedIncarTemplate,
    workflow_cutoff: PreparedWorkflowCutoff,
    submit_script: PreparedSource,
    rcut: float,
    generated_at: str,
    runner: SlurmRunner | None,
    wait: bool,
) -> bool:
    backups = []
    init_dir = init_directory.path
    with _verified_build_directory(init_directory) as output_directory:
        if structures.bot_atoms is None:
            raise RuntimeError("Stage0 preflight did not return bottom-layer atoms")
        atoms = sort(
            make_supercell(
                prim=structures.bot_atoms.copy(),
                P=supercell_matrix(config.sc),
            )
        )
        write_vasp(output_directory.write_path / "POSCAR", atoms=atoms)
        output_directory.verify()
        _write_vasp_inputs(
            config,
            output_directory.write_path,
            atoms,
            template,
            rcut,
            workflow_cutoff,
            submit_script,
        )
        output_directory.verify()
        jobs = _submit_dirs(config, runner, [init_directory], wait)
        if runner is not None and wait:
            output_directory.verify()
            prepare_init_mlff_step2(
                output_directory.write_path,
                config.input_dir,
                config.sc,
            )
            output_directory.verify()
            jobs.extend(_submit_dirs(config, runner, [init_directory], wait=True))
        output_directory.verify()
        manifest = Manifest(
            stage="init_mlff",
            generated_at=generated_at,
            config_summary=_config_summary(config, workflow_cutoff),
            directories=[relative_to_workdir(config.work_dir, init_dir)],
            backups=backups,
            jobs=[job.as_dict() for job in jobs],
        )
        write_manifest_to_directory(
            config.work_dir,
            output_directory.write_path,
            manifest,
        )
        output_directory.verify()
    return bool(jobs)


def _build_two_phase_init_mlff(
    config: DPmoireLiteConfig,
    workflow: PreparedInitMlffWorkflow,
    workflow_cutoff: PreparedWorkflowCutoff,
    submit_script: PreparedSource,
    rcut: float,
    generated_at: str,
    runner: SlurmRunner | None,
    wait: bool,
) -> bool:
    if runner is not None and wait:
        raise RuntimeError("single-job init submission cannot wait")
    if len(workflow.phases) != 2:
        raise RuntimeError("Preflight did not return both init MLFF phase directories")
    if workflow.submit_adapter.source != submit_script:
        raise RuntimeError(
            "Preflight returned inconsistent single-job submit source identities"
        )

    init_root = workflow.root_dir
    candidate = Path(
        tempfile.mkdtemp(
            prefix=".init_mlff-candidate-",
            dir=config.work_dir,
        )
    )
    try:
        phase_records = {}
        for phase in workflow.phases:
            atoms = phase.atoms.copy()
            candidate_phase = candidate / phase.name
            candidate_phase.mkdir(parents=True)
            write_vasp(candidate_phase / "POSCAR", atoms=atoms)
            _write_vasp_inputs(
                config,
                candidate_phase,
                atoms,
                phase.template,
                rcut,
                workflow_cutoff,
                submit_script,
            )
            static_names = [
                "POSCAR",
                "POTCAR",
                "INCAR",
                "KPOINTS",
                submit_script.path.name,
            ]
            if phase.template.vdw_source is not None:
                static_names.append(phase.template.vdw_source.path.name)
            phase_records[phase.name] = {
                "role": phase.role,
                "state": phase.initial_state,
                "directory": relative_to_workdir(
                    config.work_dir,
                    phase.target_dir,
                ),
                "incar_template": _prepared_source_evidence(
                    phase.template.source
                ),
                "static_inputs": {
                    name: _file_evidence(candidate_phase / name)
                    for name in static_names
                },
            }

        submit_adapter = workflow.submit_adapter
        verify_prepared_source(submit_adapter.source)
        derived_path = candidate / submit_adapter.generated_name
        atomic_bytes_publish(derived_path, submit_adapter.rendered_bytes)
        if _file_evidence(derived_path) != {
            "size": submit_adapter.size,
            "sha256": submit_adapter.sha256,
        }:
            raise RuntimeError(
                "Generated single-job submit adapter identity does not match preflight"
            )

        manifest = Manifest(
            stage="init_mlff",
            generated_at=generated_at,
            config_summary=_config_summary(config, workflow_cutoff),
            directories=[
                relative_to_workdir(config.work_dir, phase.target_dir)
                for phase in workflow.phases
            ],
            init_workflow={
                "schema": INIT_WORKFLOW_SCHEMA,
                "mode": workflow.mode,
                "state": workflow.initial_state,
                "submit_source": _prepared_source_evidence(submit_script),
                "submit_adapter": submit_adapter.audit_record(),
                "phases": phase_records,
            },
        )
        write_manifest_to_directory(config.work_dir, candidate, manifest)
        if runner is None:
            _publish_init_mlff_candidate(candidate, init_root)
        else:
            script_record = {
                "name": workflow.submit_adapter.generated_name,
                "size": workflow.submit_adapter.size,
                "sha256": workflow.submit_adapter.sha256,
            }
            with init_mlff_manifest_lock(config.work_dir):
                _publish_init_mlff_candidate(candidate, init_root)
                manifest.jobs = [
                    {
                        "path": "init_mlff",
                        "status": "SUBMITTING",
                        "script": script_record,
                    }
                ]
                write_manifest(config.work_dir, manifest)
                try:
                    job = runner.submit(init_root, "init_mlff")
                except Exception as exc:
                    failure = {
                        "kind": "sbatch-invocation",
                        "exception": type(exc).__name__,
                    }
                    returncode = getattr(exc, "returncode", None)
                    if isinstance(returncode, int) and not isinstance(returncode, bool):
                        failure["returncode"] = returncode
                    manifest.jobs = [
                        {
                            "path": "init_mlff",
                            "status": "SUBMIT_FAILED",
                            "script": script_record,
                            "failure": failure,
                        }
                    ]
                    write_manifest(config.work_dir, manifest)
                    raise InitMlffSubmissionError(
                        "single-job init sbatch request failed; inspect init_mlff/manifest.yaml"
                    ) from exc
                manifest.jobs = [
                    {
                        "job_id": job.job_id,
                        "path": "init_mlff",
                        "status": "SUBMITTED",
                        "script": script_record,
                    }
                ]
                write_manifest(config.work_dir, manifest)
    except BaseException:
        if candidate.exists():
            shutil.rmtree(candidate)
        raise
    return runner is not None


def _publish_init_mlff_candidate(candidate: Path, init_root: Path) -> None:
    try:
        atomic_directory_publish_no_replace(candidate, init_root)
    except FileExistsError as exc:
        raise RuntimeError(
            "init_mlff appeared after preflight; refusing to replace an existing target"
        ) from exc


def _prepared_source_evidence(source: PreparedSource) -> dict[str, object]:
    return {
        "name": source.path.name,
        "size": source.size,
        "sha256": source.sha256,
    }


def _file_evidence(path: Path) -> dict[str, object]:
    path = Path(path)
    return {
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _write_array_submission_script(
    config: DPmoireLiteConfig,
    stage_root: PinnedDirectory,
    folders: list[str],
    submit_script: PreparedSource,
) -> dict[str, str]:
    template_text = submit_script.path.read_text(encoding="utf-8")
    script_name = Path(config.dft_script).name
    array_name = f"array_{script_name}"
    text = render_array_submission_script(
        template_text,
        folders,
        max_concurrent=config.array_max_concurrent,
    )
    atomic_text_publish(stage_root.write_path / array_name, text, encoding="utf-8")
    stage_root.verify()
    return {array_name: hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _maybe_array_scripts(
    config: DPmoireLiteConfig,
    stage_root: PinnedDirectory,
    folders: list[str],
    submit_script: PreparedSource,
) -> dict[str, str]:
    if not config.array_submission:
        return {}
    return _write_array_submission_script(config, stage_root, folders, submit_script)


def _md_array_folders(
    stackings: tuple[tuple[int, int], ...],
    include_monolayer_md: bool,
) -> list[str]:
    folders = [f"{i}_{j}" for i, j in stackings]
    if include_monolayer_md:
        folders.extend(("top_layer", "bot_layer"))
    return folders


def _build_relaxations(
    config: DPmoireLiteConfig,
    structures: StructureHandler,
    stackings: tuple[tuple[int, int], ...],
    target_directories: tuple[PinnedDirectory, ...],
    stage_root: PinnedDirectory,
    template: PreparedIncarTemplate,
    workflow_cutoff: PreparedWorkflowCutoff,
    submit_script: PreparedSource,
    rcut: float,
    generated_at: str,
    runner: SlurmRunner | None,
    wait: bool,
) -> bool:
    backups = []
    directories = []
    rlx_poscars = {}
    grid_shift_anchors = {}
    for (i, j), target_directory in zip(
        stackings,
        target_directories,
        strict=True,
    ):
        target = target_directory.path
        with _verified_build_directory(target_directory) as output_directory:
            atoms = (
                structures.shift_atoms(i, j, c_constrain=True, sc=config.sc)
                if config.sc_rlx
                else structures.shift_primitive_atoms(i, j)
            )
            poscar = output_directory.write_path / "POSCAR"
            write_vasp(poscar, atoms=atoms)
            output_directory.verify()
            relative_path = relative_to_workdir(config.work_dir, target / "POSCAR")
            identity = provenance_module.structure_identity_record(
                poscar,
                relative_path,
            )
            rlx_poscars[relative_path] = identity
            top_indexes, bot_indexes = structures.find_layer_idx(atoms)
            anchor_relative_path = relative_to_workdir(config.work_dir, target)
            grid_shift_anchors[anchor_relative_path] = (
                provenance_module.stage0_grid_shift_anchor_record(
                    poscar,
                    identity,
                    top_indexes,
                    bot_indexes,
                )
            )
            output_directory.verify()
            _write_vasp_inputs(
                config,
                output_directory.write_path,
                atoms,
                template,
                rcut,
                workflow_cutoff,
                submit_script,
            )
            output_directory.verify()
        directories.append(target)
    array_scripts = _maybe_array_scripts(
        config,
        stage_root,
        [directory.path.name for directory in target_directories],
        submit_script,
    )
    for target_directory in target_directories:
        _verify_claimed_directory(target_directory.path, target_directory)
    jobs = _submit_dirs(config, runner, list(target_directories), wait)
    provenance = provenance_module.stage0_structure_provenance(
        config,
        structures,
        stackings,
        rlx_poscars,
    )
    _write_claimed_manifest(
        config.work_dir,
        stage_root,
        Manifest(
            stage="rlx",
            generated_at=generated_at,
            config_summary=_config_summary(config, workflow_cutoff),
            directories=[
                relative_to_workdir(config.work_dir, path) for path in directories
            ],
            backups=backups,
            jobs=[job.as_dict() for job in jobs],
            stackings=[[i, j] for i, j in stackings],
            structure_provenance=provenance,
            grid_shift_anchors=grid_shift_anchors,
            array_scripts=array_scripts,
        ),
    )
    return bool(jobs)


def _build_validation(
    config: DPmoireLiteConfig,
    validation_structures: tuple[PreparedValidationStructure, ...],
    target_directories: tuple[PinnedDirectory, ...],
    stage_root: PinnedDirectory,
    template: PreparedIncarTemplate,
    workflow_cutoff: PreparedWorkflowCutoff,
    submit_script: PreparedSource,
    rcut: float,
    generated_at: str,
    runner: SlurmRunner | None,
    wait: bool,
) -> bool:
    directories = [target_directory.path for target_directory in target_directories]
    backups = []
    with _verified_build_directory(stage_root) as stage_directory:
        for record, target_directory in zip(
            validation_structures,
            target_directories,
            strict=True,
        ):
            target = target_directory.path
            with _verified_build_directory(target_directory) as output_directory:
                atoms = record.atoms.copy()
                write_vasp(output_directory.write_path / "POSCAR", atoms=atoms)
                output_directory.verify()
                _write_vasp_inputs(
                    config,
                    output_directory.write_path,
                    atoms,
                    template,
                    rcut,
                    workflow_cutoff,
                    submit_script,
                )
                output_directory.verify()
        for target_directory in target_directories:
            _verify_claimed_directory(target_directory.path, target_directory)
        jobs = _submit_dirs(config, runner, list(target_directories), wait)
        stage_directory.verify()
        manifest = Manifest(
            stage="validation",
            generated_at=generated_at,
            config_summary=_config_summary(config, workflow_cutoff),
            directories=[
                relative_to_workdir(config.work_dir, path) for path in directories
            ],
            backups=backups,
            jobs=[job.as_dict() for job in jobs],
            angles=[record.angle for record in validation_structures],
        )
        write_manifest_to_directory(
            config.work_dir,
            stage_directory.write_path,
            manifest,
        )
        stage_directory.verify()
    return bool(jobs)


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


def _submit_dirs(
    config: DPmoireLiteConfig,
    runner: SlurmRunner | None,
    directories: list[Path | PinnedDirectory],
    wait: bool,
) -> list[SlurmJob]:
    if runner is None:
        return []
    items = []
    for directory in directories:
        if isinstance(directory, PinnedDirectory):
            directory.verify()
            public_path = directory.path
            execution_path = directory.write_path
        else:
            public_path = directory
            execution_path = directory
        items.append(
            (
                execution_path,
                relative_to_workdir(config.work_dir, public_path),
            )
        )
    if hasattr(runner, "submit_many"):
        return runner.submit_many(items, wait=wait)
    jobs = [
        runner.submit(execution_path, relative_path)
        for execution_path, relative_path in items
    ]
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
        "grid_shift_anchor": dict(config.grid_shift_anchor)
        if config.grid_shift_anchor
        else None,
        "array_submission": config.array_submission,
        "array_max_concurrent": config.array_max_concurrent,
        "potcar_policy": config.potcar_policy,
        "init_mlff_mode": config.init_mlff_mode,
        "k_mesh": config.k_mesh,
        "encut": config.encut,
        "workflow_cutoff": workflow_cutoff.audit_record(),
        "r_cut": config.r_cut,
    }
