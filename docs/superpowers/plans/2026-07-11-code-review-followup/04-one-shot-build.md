# Plan 04: One-shot Build and Aggregate Preflight

Authoritative spec:
[P2-2 Stage rebuild contract](../../../code-review-notes/P2-2-stage-rebuild-contract.md)

Consumes:

- [Plan 01 INCAR engine](01-incar-engine.md)
- [Plan 02 Manifest v2](02-manifest-v2.md)
- [Plan 03 ML_AB parser](03-mlab-parser-digest.md)

Unlocks: Plan 05

## Goal and Done State

Make every calculation stage a one-shot generated artifact and establish one
aggregate no-side-effect preflight boundary for all build inputs.

Done means:

- the full target-stage set is computed before any filesystem modification;
- an existing empty or non-empty target stage fails the whole command;
- ordinary build never moves targets to backups or overwrites them;
- all discoverable template, POTCAR, structure, MLFF, provenance-independent
  Stage1, and path errors are aggregated before generation;
- stage manifests are written atomically only after complete generation;
- an interrupted first generation leaves a diagnosable partial stage without a
  completion manifest, and rerun requires explicit user deletion;
- Stage1 and collect require an actual stage manifest and have no config,
  auxiliary-file, or directory-scan fallback.

## Non-goals

- Do not implement automatic rollback, stage staging, `--force`, or resume.
- Do not infer legacy Stage0 provenance; Plan 05 owns it.
- Do not implement MD constraint or velocity behavior; Plan 06 owns it.
- Do not back up build stages. P2-3 dataset backup is a separate lifecycle.
- Do not add Slurm automation; the Plan 00 gate remains active.

## Files

Create:

- `src/dpmoire_lite/build_preflight.py`
- `tests/test_build_lifecycle.py`

Modify:

- `src/dpmoire_lite/build.py`
- `src/dpmoire_lite/collect.py`
- `src/dpmoire_lite/paths.py`
- `src/dpmoire_lite/structures.py` only to separate pure symmetry selection from
  auxiliary-file publication
- `tests/test_build.py`
- `tests/test_collect.py`
- `tests/test_symmetry.py`
- `tests/test_paths_manifest.py`

Remove from ordinary build call paths:

- `_backup_targets()` and `backup_existing_directory()` use for calculation
  stages; the helper may remain temporarily only if another supported caller is
  proven and documented.

## Preflight Ordering Contract

The build command follows this order:

1. load and validate config, including the Plan 00 safety gate;
2. compute the complete target-stage set without creating paths;
3. check all target-stage conflicts and stop if any exist;
4. run all applicable domain validators and aggregate failures;
5. only after all checks pass, create `work_dir` and the first target stage;
6. generate all enabled stage contents;
7. write auxiliary root artifacts from already validated in-memory data;
8. atomically publish each completion manifest only when its stage is complete.

Target conflicts take precedence over expensive input validation so a command
cannot obscure the explicit-delete requirement with unrelated parser errors.

## Task 1: Add Failing Full-target Conflict Tests

Add tests:

- `test_stage0_existing_empty_init_mlff_blocks_every_target()`;
- `test_stage0_existing_rlx_blocks_init_and_validation_generation()`;
- `test_stage0_existing_validation_blocks_init_and_rlx_generation()`;
- `test_stage0_reports_all_existing_target_stages()`;
- `test_stage1_existing_empty_md_fails_without_backup()`;
- `test_target_conflict_runs_before_input_preflight()`;
- `test_target_conflict_does_not_write_root_artifacts()`;
- `test_target_conflict_does_not_construct_runner()`.

For Stage0, enable `init_mlff`, `do_relaxation`, and `twist_val` together. Place a
sentinel in each non-conflicting target and assert it is unchanged.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_build_lifecycle.py -q -p no:cacheprovider -k "existing or conflict"
```

Expected failure: current build backs up per-target directories and may generate
one target before discovering another problem.

## Task 2: Implement Pure Target Discovery and the Existence Gate

Implementation requirements:

1. Represent target stages explicitly: `init_mlff`, `rlx`, `validation`, and `md`.
2. Compute Stage0 targets from config without creating `StructureHandler`,
   `work_dir`, runner, temp directories, or stacking files.
3. Check every target and aggregate all conflicts.
4. Treat an empty directory as existing.
5. Emit the explicit-delete/rerun instruction.
6. Remove ordinary build calls to per-target backup/move behavior.
7. Apply the same gate in direct `build_stage0()`/`build_stage1()` calls.

Green command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_build_lifecycle.py tests/test_paths_manifest.py -q -p no:cacheprovider -k "existing or conflict or backup"
```

Checkpoint: one-shot target gate.

## Task 3: Add Failing Aggregate-preflight Tests

Add parameterized tests that combine at least two failures and assert both are
reported before target creation:

- `test_stage0_preflight_aggregates_incar_potcar_and_script_errors()`;
- `test_stage0_preflight_aggregates_input_structure_and_vdw_errors()`;
- `test_stage1_preflight_aggregates_all_relaxation_failures()`;
- `test_stage1_preflight_fully_parses_initial_seed()`;
- `test_preflight_failure_does_not_create_workdir_when_absent()`;
- `test_preflight_failure_does_not_write_normalized_input_sibling()`;
- `test_preflight_warnings_are_deduplicated_per_template_and_tag()`.

Use Plan 01 diagnostics and Plan 03 full initial-seed parser. Include a malformed
seed whose header count looks valid so the test proves preflight is not a
fifth-line count check.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_build_lifecycle.py -q -p no:cacheprovider -k "preflight"
```

Expected failure: current validation is distributed through generation helpers
and several readers can write normalized files or create directories.

## Task 4: Implement the Aggregate Preflight Pipeline

Implementation requirements:

1. Define a stable diagnostic record containing domain, path, optional tag/block,
   and reason.
2. Domain validators return diagnostics and validated in-memory values rather
   than writing.
3. Validate every enabled INCAR template through Plan 01.
4. Resolve all ordered elements, POTCAR choices, ENMAX values, and required vdW
   kernel paths without writing POTCAR/output files.
5. Read structures without creating normalized sibling files.
6. Validate submit-script/template presence.
7. Fully parse initial MLFF seed/force-field inputs when needed.
8. Aggregate every relaxation OUTCAR/CONTCAR failure for Stage1.
9. Validate every intended output path remains in `work_dir`.
10. Emit repairable warnings once per stable diagnostic identity after the
    preflight succeeds.

The preflight result should carry reusable values such as stackings, parsed INCAR
documents, selected POTCAR sources/ENMAX, resolved rcut, and initial seed identity
so generation does not reparse and disagree.

Green command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_build_lifecycle.py tests/test_incar.py tests/test_mlab.py -q -p no:cacheprovider -k "preflight or warning"
```

Checkpoint: no-side-effect preflight framework.

## Task 5: Remove Missing-manifest and Stacking Fallbacks

Add failing tests:

- `test_stage1_missing_relaxation_manifest_fails_without_md_change()`;
- `test_stage1_does_not_use_sym_reduced_file_without_manifest()`;
- `test_stage1_does_not_regenerate_stackings_from_config()`;
- `test_collect_missing_stage_manifest_fails_without_output_or_manifest()`;
- `test_collect_does_not_derive_directories_from_config()`;
- `test_collect_does_not_scan_validation_directories_without_manifest()`.

Implementation:

1. Replace `_stage1_stackings()` fallbacks with strict manifest input.
2. Remove `_new_manifest()` and `_derived_directories()` from normal collect.
3. Let Plan 02 exceptions distinguish missing, legacy, and invalid manifests.
4. Keep fatal errors before a safe collect transaction read-only; Plan 10 maps
   them to exit 1.
5. Do not auto-create a v2 manifest for historical directories.

Verification:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_build_lifecycle.py tests/test_collect.py -q -p no:cacheprovider -k "manifest or fallback or derive"
```

Checkpoint: strict downstream stage boundary.

## Task 6: Define Completion and Interruption Semantics

Add tests:

- `test_stage_manifest_published_only_after_all_targets_in_that_stage_succeed()`;
- `test_generation_failure_leaves_partial_stage_without_manifest()`;
- `test_rerun_rejects_partial_stage_until_user_deletes_it()`;
- `test_delete_partial_stage_then_rerun_succeeds()`;
- `test_symmetry_auxiliary_file_written_only_after_preflight()`;
- `test_manifest_stackings_equal_generated_and_auxiliary_stackings()`.

Use failure injection during the second generated target. Assert the first target
may remain for diagnosis, no manifest claims completion, and the next run fails at
the existing-stage gate. Delete the whole target stage explicitly in the test,
then verify a clean rerun.

Implementation requirements:

1. Publish manifest through Plan 02 only after all stage outputs succeed.
2. Do not implement rollback or cleanup-on-exception.
3. Split symmetry selection into a pure return value and a later auxiliary-file
   write.
4. Record the exact in-memory stacking list in the manifest.
5. Keep a failed partial stage untouched for user diagnosis.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_build_lifecycle.py tests/test_symmetry.py -q -p no:cacheprovider -k "manifest or partial_stage or symmetry"
```

Full checkpoint:

```powershell
& $python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

## Acceptance Traceability

| P2-2 requirement | Evidence |
| --- | --- |
| existing empty/nonempty stage rejected | Tasks 1 and 2 |
| Stage0 checks complete target set first | Tasks 1 and 2 |
| no backups/implicit rebuild | Task 2 |
| all discoverable input errors before writes | Tasks 3 and 4 |
| missing manifest is fatal | Task 5 |
| no stacking/directory fallbacks | Task 5 |
| manifest means complete stage | Task 6 |
| interrupted stage requires explicit deletion | Task 6 |
| symmetry artifact after preflight | Task 6 |

## Plan Checkpoint

Plan 04 is complete when target-conflict, aggregate-preflight, strict-manifest,
interruption, affected build/collect, and full suites pass. Structure provenance
validators remain open for Plan 05 and plug into the same preflight boundary.
