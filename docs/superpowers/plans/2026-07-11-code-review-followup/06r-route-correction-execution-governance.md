# Plan 06R: Route Correction and Execution Governance

Authoritative specifications:

- [Approved route-correction and dual-execution design](../../specs/2026-07-12-code-review-followup-route-correction-and-low-reasoning-execution-design.md)
- [P1-1 MD Selective Dynamics](../../../code-review-notes/P1-1-md-selective-dynamics.md)
- [P1-5 Stage provenance](../../../code-review-notes/P1-5-stage-provenance.md)
- [P2-2 Stage rebuild contract](../../../code-review-notes/P2-2-stage-rebuild-contract.md)

Consumes:

- [Plan 04 One-shot build and aggregate preflight](04-one-shot-build.md)
- [Plan 05 Stage provenance](05-stage-provenance.md)
- [Plan 06 MD normalization and seed staging](06-md-normalization-seed-staging.md)

Unlocks: Plans 07, 09, and 10

## Goal and Done State

Restore the completed build path to the approved manifest, provenance, and
no-side-effect contracts before any new collection work begins. At the same time,
replace the stale monolithic execution prompt with a progressively disclosed
orchestrator/worker workflow that a lower-cost model can follow without gaining
route, commit, or active-state authority.

Done means:

- exactly four scoped `AGENTS.md` files define stable repository, plan,
  production, and test rules;
- two persistent threads use a serial handoff in the shared worktree: Sol Max
  Standard owns planning/review/commit and Luna Max Standard owns bounded TDD;
- the orchestrator prompt is no more than 120 physical lines and the worker
  prompt is no more than 80; neither prompt claims it can select its own model;
- the worker grants no plan, staging, commit, push, or active-state authority;
- ignored runtime state is no more than 80 physical lines and names one exact
  execution unit and next action;
- new Stage0 anchor records exist only in top-level `grid_shift_anchors`;
- nested-only development anchor manifests fail closed for preservation instead
  of creating a dual-read schema;
- successful legacy Stage1 inference emits exactly one warning per build;
- successful and failed preflight leave both the input tree and absent work tree
  unchanged;
- Stage0 symmetry selection and Stage1 manifest stacking interpretation occur in
  preflight;
- generation consumes the one prepared structure, rcut, template, POTCAR,
  submit-script, vdW, source-identity, and target-path interpretation;
- `provenance.py`, `build_preflight.py`, and `build.py` again match their roadmap
  ownership;
- every Task checkpoint is full-suite green and Plan 07 remains closed until the
  final Plan 06R gate passes.

The historical audit at commit `698b8a0` recorded 302 passed and 6 skipped. That
is evidence for the route review, not an exact-count assertion for future test
runs.

## Non-goals

- Do not implement Plan 07 OUTCAR ingestion, Plan 08/08A collection semantics,
  Plan 09 publication, or Plan 10 integration.
- Do not re-enable `stage: all`, submitted `--wait`, MD restart, deferred Slurm
  automation, fuzzy deduplication, or in-place stage rebuild.
- Do not add a generic workflow engine, task database, or commit hook.
- Do not create a permanent compatibility reader for nested-only development
  anchors.
- Do not rewrite, squash, or hide historical RED-only commits.
- Do not install or modify Python/conda packages.
- Do not stage local runtime state, private `example-test/` data, or POTCAR
  content.

## Execution Authority

Only the high-reasoning orchestrator may select a unit, interpret authoritative
documents, issue a capsule, change specs/plans/runtime state, stage, commit, or
advance the plan. A worker may edit only capsule-named files, run capsule-named
tests, and return an unstaged diff plus evidence.

| Task | Execution profile | RED handoff | `standalone_eligible` |
| --- | --- | --- | --- |
| 1 Governance bootstrap | orchestrator-only | normal TDD inside root task | false |
| 2 Manifest/warning contracts | Luna worker thread under Sol orchestrator | mandatory split RED then GREEN | false |
| 3 Pure authoritative structure preflight | orchestrator-only | root observes RED | false |
| 4 Prepared non-structure build inputs | orchestrator-only | root observes RED | false |
| 5 Provenance ownership restoration | Luna worker thread under Sol orchestrator | split RED then GREEN | false |

The normal topology is two persistent threads using a serial handoff in the
shared worktree. One write-capable thread may work at a time. Model, reasoning,
and speed are explicit UI selections, not prompt-controlled properties. Read-only
review agents may run independently, but their output cannot authorize changes.
The no-write contract drill does not count toward direct-mode eligibility. Only
successful supervised Tasks 2 and 5 count; any scope, authority, or TDD-protocol
violation leaves direct lower-cost mode closed.

The orchestrator reads this leaf plan completely. A worker reads only the header
authorities, this Execution Authority section, the Standard Checkpoint Protocol,
its active Task section, and the matching acceptance row named by its capsule.
It must not load or act on later Task sections.

Every delegated capsule must contain:

1. exact Task and phase;
2. execution profile and RED handoff mode;
3. `standalone_eligible: false`;
4. directly applicable authorities;
5. exact allowed files and known pre-existing changes;
6. named RED tests, command, and expected failure;
7. minimal GREEN behavior and affected/full commands;
8. non-goals and stop conditions;
9. required evidence and report shape.

If any field is missing or disagrees with git/runtime state, the worker stops
without editing.

## Standard Checkpoint Protocol

Use the verified interpreter for every Task:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
& $python -c "import sys; print(sys.executable)"
$env:PIP_NO_CACHE_DIR = '1'
```

For every behavior or ownership change:

1. add only the named RED tests;
2. run the focused command and observe the expected behavioral failure;
3. implement only that Task;
4. run focused and affected suites;
5. run the complete suite;
6. run `git diff --check` and inspect `git status --short`;
7. the orchestrator stages an explicit file list and inspects
   `git diff --cached --name-status` and `git diff --cached --check`;
8. the orchestrator creates one green local commit, updates ignored runtime
   state, and stops.

Workers never perform steps 7 or 8. Never use `git add .`, `git add -A`, reset,
checkout, clean, automatic push, or a test skip/ignore to manufacture GREEN.

## Files Across Plan 06R

Create during Task 1:

- `AGENTS.md`
- `docs/superpowers/plans/2026-07-11-code-review-followup/AGENTS.md`
- `src/dpmoire_lite/AGENTS.md`
- `tests/AGENTS.md`
- `docs/superpowers/plans/2026-07-11-code-review-followup/ORCHESTRATOR-EXECUTION-PROMPT.md`
- `docs/superpowers/plans/2026-07-11-code-review-followup/WORKER-EXECUTION-PROMPT.md`
- `tests/test_execution_governance.py`
- ignored `IMPLEMENTATION-HISTORY.local.md`
- a replacement ignored `IMPLEMENTATION-STATUS.local.md`

Remove during Task 1:

- `docs/superpowers/plans/2026-07-11-code-review-followup/LOW-REASONING-EXECUTION-PROMPT.md`
  after its role-independent rules have been moved to scoped instructions.

Modify across product Tasks:

- `src/dpmoire_lite/build.py`
- `src/dpmoire_lite/build_preflight.py`
- `src/dpmoire_lite/structures.py`
- `src/dpmoire_lite/inputs.py`
- `src/dpmoire_lite/provenance.py`
- `tests/test_build.py`
- `tests/test_build_lifecycle.py`
- `tests/test_incar.py`
- `tests/test_manifest_v2.py`
- `tests/test_md_normalization.py`
- `tests/test_stage_provenance.py`
- `tests/test_structures_inputs.py`
- `tests/test_symmetry.py`

Task-specific allowed-file lists below are strict subsets. A need to edit a file
not listed for the active Task is a blocker requiring orchestrator review; it is
not permission to widen a worker capsule.

## Task 1: Bootstrap Progressive Execution Governance

Execution profile: orchestrator-only. Do not delegate writes.

Versioned allowed files:

- `.gitignore`
- the four `AGENTS.md` paths above
- `tests/test_execution_governance.py`
- `tests/test_repository_hygiene.py`
- this Plan 06R leaf plan, the roadmap README, the route-design governance body,
  and the other two approved design status headers
- remove `LOW-REASONING-EXECUTION-PROMPT.md` and create both
  `ORCHESTRATOR-EXECUTION-PROMPT.md` and `WORKER-EXECUTION-PROMPT.md`

Local-only allowed files, never staged:

- `IMPLEMENTATION-STATUS.local.md`
- `IMPLEMENTATION-HISTORY.local.md`

Add failing governance tests:

- `test_exactly_four_scoped_agents_files_exist()`;
- `test_agents_files_respect_line_caps_and_contain_no_concrete_runtime_state()`;
- `test_orchestrator_prompt_is_bounded_and_owns_capsule_review_and_commit()`;
- `test_worker_prompt_is_short_and_denies_commit_and_state_advance()`;
- `test_two_thread_prompts_define_a_serial_shared_worktree_handoff()`;
- `test_legacy_low_reasoning_prompt_is_absent()`;
- `test_native_custom_agent_config_is_absent_until_runtime_support_is_proven()`;
- `test_governance_authorities_define_two_persistent_threads()`;
- `test_local_implementation_state_pattern_is_repository_ignored()`;
- `test_versioned_status_headers_do_not_name_a_dynamic_active_plan()`.

RED command:

```powershell
& $python -m pytest tests/test_execution_governance.py tests/test_repository_hygiene.py -q -p no:cacheprovider
```

Expected failure: the scoped instructions, two reusable prompts, serial-handoff
contract, portable local-state ignore rule, and normalized status text do not
exist; the unverified native custom-agent configuration is still present.

Implementation requirements:

1. Root `AGENTS.md` contains only stable repository authority, verified Python,
   safety, private-data, exact-staging, one-green-unit, and narrower-file rules.
2. Plan-directory `AGENTS.md` defines active-state recovery, capsule fields,
   legal phases, RED/GREEN pairing, role authority, contradiction handling, and
   stop-after-checkpoint behavior.
3. Production `AGENTS.md` records module ownership and cross-module invariants by
   link, without copying scientific algorithms.
4. Test `AGENTS.md` records TDD, focused/affected/full verification, fixture
   provenance, private-data, platform skip, and external ASE rules.
5. Each `AGENTS.md` is at most 120 physical lines. None contains a concrete HEAD,
   timestamp, active plan, or current test count.
6. Create an orchestrator prompt of at most 120 physical lines. It is reusable in
   a persistent Sol thread and owns planning, copy-ready capsule issuance,
   complete-diff review, final verification, exact staging, commit, and state
   advance.
7. Rewrite the worker prompt to at most 80 physical lines. It is reusable in a
   persistent Luna thread, requires a capsule, follows RED-GREEN-REFACTOR, reads
   active state only for agreement, leaves changes unstaged, and denies
   plan/spec/state edits, staging, commit, push, and nested delegation.
8. State the launch profiles as external UI choices: Sol / Max / Standard and
   Luna / Max / Standard, with explicit Terra fallback if Luna is unavailable.
   Prompt text must not claim to switch or prove a model. Keep native named-agent
   loading out of the gate until a separate runtime-proven amendment is approved.
9. Add a repository `.gitignore` rule for `IMPLEMENTATION-*.local.md`; do not rely
   on the current user's global ignore file.
10. Preserve the existing 360-line status as `IMPLEMENTATION-HISTORY.local.md`.
    Create a new status of at most 80 lines with one unit, one phase, exact
    allowed files, current evidence, blockers, one next action, and first resume
    commands.
11. Normalize versioned status text: roadmap runtime progress lives only in the
    ignored snapshot; the decomposition design is approved and under
    implementation; the full-dedup design is approved and incorporated; this
    route design is approved. Mark `93 passed, 5 skipped` as historical wherever
    retained.
12. Run a no-write worker-contract drill that reads the new bootstrap path,
    reports Task 2 and its role limits, and changes no file. Compare status,
    tracked/cached diffs, and governed-file hashes before and after. This drill
    validates the prompt contract, not the model identity. The first actual Luna
    UI-selected execution is Task 2's split RED/GREEN calibration.

Local audit commands:

```powershell
git check-ignore -v docs/superpowers/plans/2026-07-11-code-review-followup/IMPLEMENTATION-STATUS.local.md
git check-ignore -v docs/superpowers/plans/2026-07-11-code-review-followup/IMPLEMENTATION-HISTORY.local.md
git ls-files "*IMPLEMENTATION-*.local.md"
git diff --check
git status --short
```

The `git ls-files` command must produce no local-state path.

Affected suite:

```powershell
& $python -m pytest tests/test_execution_governance.py tests/test_repository_hygiene.py tests/test_cli.py -q -p no:cacheprovider
```

Then run the standard full checkpoint. Suggested orchestrator commit:
`docs: bootstrap plan execution governance`.

After the commit, set ignored status to Task 2 `inspect`, name the exact Task 2
allowed files, and stop.

## Task 2: Restore Top-level Anchors and the Legacy Warning

Execution profile: persistent Luna worker thread under Sol orchestrator.
RED handoff: mandatory split. This is the first worker calibration unit.
`standalone_eligible: false` because it changes schema and compatibility behavior.

Direct authorities:

- route-correction design, Unit 1;
- P1-1 Stage0 anchor provenance;
- P1-5 successful legacy-inference behavior;
- Plan 02's existing top-level Manifest v2 field.

Allowed files:

- `src/dpmoire_lite/build.py`
- `src/dpmoire_lite/build_preflight.py`
- `tests/test_manifest_v2.py`
- `tests/test_md_normalization.py`
- `tests/test_stage_provenance.py`

Do not edit `manifest.py`; its current schema already owns the top-level field.
Do not move provenance helpers yet; Task 5 owns that refactor.

Add failing tests:

- `test_stage0_manifest_writes_grid_shift_anchors_at_top_level_only()`;
- `test_stage1_preservation_reads_top_level_anchor_records()`;
- `test_nested_only_development_anchors_fail_with_regeneration_diagnostic()`;
- `test_nested_only_development_anchors_do_not_block_default_clearing()`;
- `test_successful_legacy_inference_warns_exactly_once_per_build()`;
- `test_failed_legacy_inference_emits_no_success_warning()`;
- `test_strict_stage1_emits_no_legacy_inference_warning()`.

RED command:

```powershell
& $python -m pytest tests/test_manifest_v2.py tests/test_md_normalization.py tests/test_stage_provenance.py -q -p no:cacheprovider -k "top_level or nested_only or legacy_inference_warn"
```

Expected RED: Stage0 writes anchors under `structure_provenance`, preservation
reads that nested location, and successful legacy inference is silent.

The worker returns after RED with the command, exit code, expected assertion, and
changed test files. The orchestrator validates the failure and sends GREEN
authorization to the same worker.

GREEN requirements:

1. Pass Stage0 anchor records through `Manifest(grid_shift_anchors=...)`.
2. Remove anchor collection data from the Stage0 `structure_provenance` payload.
3. Preserve current MD-manifest top-level anchor output.
4. Read preservation anchors only from `Manifest.grid_shift_anchors`.
5. For `preserve_grid_shift_md: true`, a current manifest containing only the
   erroneous nested representation fails before `md/` mutation and tells the
   user to regenerate Stage0. Do not copy or migrate it automatically.
6. For preservation false, nested anchor data is irrelevant; unconditional
   constraint clearing may continue if every other provenance check passes.
7. After deterministic legacy inference succeeds, append one stable provenance
   warning diagnostic and emit it once through the existing preflight warning
   boundary. Failed inference and strict provenance emit no legacy-success
   warning.
8. Keep MD manifest `mode: legacy_inference` evidence unchanged.

Focused GREEN: rerun the RED command.

Affected suite:

```powershell
& $python -m pytest tests/test_manifest_v2.py tests/test_md_normalization.py tests/test_stage_provenance.py tests/test_build.py tests/test_build_lifecycle.py -q -p no:cacheprovider
```

The worker returns an unstaged diff. The orchestrator reviews the public schema
location and warning count, runs the standard full checkpoint, and creates the
suggested commit `fix: restore stage anchor and legacy warning contracts`.

Update ignored status to Task 3 `inspect` and stop.

## Task 3: Make Structure and Symmetry Preflight Pure and Authoritative

Execution profile: orchestrator-only because this unit changes the prepared
structure boundary across Stage0 and Stage1. Read-only review subagents are
allowed; no worker may write.

Direct authorities:

- route-correction design, Unit 2 and preflight data flow;
- Plan 04 preflight ordering and one-shot failure semantics;
- Plan 05 manifest-only stacking and trusted structure interpretation;
- Plan 06 normalization and anchor behavior.

Allowed files:

- `src/dpmoire_lite/structures.py`
- `src/dpmoire_lite/build_preflight.py`
- `src/dpmoire_lite/build.py`
- `tests/test_build.py`
- `tests/test_build_lifecycle.py`
- `tests/test_md_normalization.py`
- `tests/test_stage_provenance.py`
- `tests/test_structures_inputs.py`
- `tests/test_symmetry.py`

Add failing tests:

- `test_structure_label_compatibility_read_uses_no_temporary_file()`;
- `test_successful_stage0_preflight_leaves_input_and_absent_work_tree_unchanged()`;
- `test_successful_stage1_preflight_leaves_existing_work_tree_byte_identical()`;
- `test_symmetry_selection_failure_precedes_workdir_creation()`;
- `test_stage0_generation_consumes_preflight_structure_rcut_and_stackings()`;
- `test_stage1_preflight_owns_relaxation_manifest_stackings()`;
- `test_stage1_generation_consumes_preflight_structures_relaxations_and_rcut()`;
- `test_generation_does_not_reparse_written_or_validated_poscars()`;
- `test_twist_validation_selection_is_prepared_without_a_temporary_workdir()`.

The temporary-file test monkeypatches `tempfile.NamedTemporaryFile` globally to
raise if called, so it passes on Python 3.10 without relying on platform file
permissions.

RED command:

```powershell
& $python -m pytest tests/test_build_lifecycle.py tests/test_structures_inputs.py tests/test_symmetry.py -q -p no:cacheprovider -k "temporary_file or successful_stage0_preflight or successful_stage1_preflight or symmetry_selection_failure or consumes_preflight or owns_relaxation_manifest or does_not_reparse or twist_validation"
```

Expected RED: compatibility parsing writes a temporary sibling, Stage0 ignores
the preflight result and selects symmetry after `work_dir` creation, Stage1 reads
stackings before preflight and reconstructs structures/rcut, and validation
structure discovery uses a temporary work directory during generation.

Implementation requirements:

1. Replace normalized-sibling parsing with an in-memory text/file-object path.
   No successful or failed `read_atoms()` call may create a sibling file.
2. Parse each input layer once, validate it, and construct the returned
   `StructureHandler` from those already parsed atoms. Do not perform the current
   reader pass followed by a second constructor pass.
3. Stage0 preflight selects either the full or symmetry-reduced stacking tuple.
   Missing optional symmetry dependencies and matcher failures become preflight
   diagnostics before `work_dir` exists.
4. Separate validation twist discovery from publication: preflight returns the
   prepared angle/Atoms pairs without creating a temporary work path; generation
   only writes those prepared values.
5. Change Stage1 to `preflight_stage1(config)`. The preflight reads the required
   relaxation manifest, validates its stacking list, and returns that tuple.
   Remove build-layer stacking interpretation.
6. Have relaxation validation return the already parsed CONTCAR atoms and retain
   them in copy-isolated prepared records keyed by stacking. Preflight anchor and
   topology validation and later generation use the same parsed value.
7. `build_stage0()` consumes `preflight.structures`, `preflight.rcut`, prepared
   stackings, and prepared validation values. It does not construct another
   handler, recompute rcut, or reselect symmetry.
8. `build_stage1()` consumes preflight stackings, structures, rcut, trusted
   provenance, seed, and prepared relaxation atoms. Its MD writer accepts atoms
   rather than reopening CONTCAR, returns the output atoms for input generation,
   and never rereads the just-written POSCAR.
9. Monolayer generation uses copies of the preflight layer atoms. Preserve the
   existing one-warning constraint-clear contract.
10. Keep prepared collections tuple-based and avoid exposing mutable dictionaries
    as the authority. Copy mutable ASE values at the ownership boundary.
11. Do not change INCAR/POTCAR/script/vdW selection in this Task; Task 4 owns the
    remaining prepared-input boundary.
12. Leave `stage: all` fail-closed and preserve the one-shot partial-generation
    contract after preflight succeeds.

Focused GREEN: rerun the RED command.

Affected suite:

```powershell
& $python -m pytest tests/test_build.py tests/test_build_lifecycle.py tests/test_interlayer_geometry.py tests/test_md_normalization.py tests/test_stage_provenance.py tests/test_structures_inputs.py tests/test_symmetry.py -q -p no:cacheprovider
```

Run the standard full checkpoint. Suggested orchestrator commit:
`refactor: consume prepared build structures`. Update ignored status to Task 4
`inspect` and stop.

## Task 4: Prepare and Consume Every Remaining Build Input Once

Execution profile: orchestrator-only because this unit fixes target discovery and
the cross-module prepared-input API. Read-only review subagents are allowed.

Direct authorities:

- route-correction design, Unit 3;
- Plan 01 parsed INCAR document/render contract;
- Plan 04 target-conflict precedence and aggregate diagnostics;
- current `inputs.py` ownership of VASP input orchestration.

Allowed files:

- `src/dpmoire_lite/inputs.py`
- `src/dpmoire_lite/build_preflight.py`
- `src/dpmoire_lite/build.py`
- `tests/test_build.py`
- `tests/test_build_lifecycle.py`
- `tests/test_incar.py`
- `tests/test_structures_inputs.py`

Add failing tests:

- `test_preflight_returns_parsed_incar_documents_and_required_vdw_sources()`;
- `test_generation_does_not_reparse_preflight_incar_templates()`;
- `test_generation_does_not_reresolve_potcars_or_reread_enmax()`;
- `test_preflight_records_potcar_script_and_vdw_source_identities()`;
- `test_generation_copies_only_the_prepared_source_paths()`;
- `test_changed_prepared_source_fails_identity_check_before_copy()`;
- `test_preflight_returns_the_exact_validated_output_paths()`;
- `test_target_conflict_still_precedes_all_domain_preparation()`.

RED command:

```powershell
& $python -m pytest tests/test_build_lifecycle.py tests/test_incar.py tests/test_structures_inputs.py -q -p no:cacheprovider -k "parsed_incar_documents or does_not_reparse or does_not_reresolve or source_identities or prepared_source_paths or changed_prepared_source or exact_validated_output_paths or conflict_still_precedes"
```

Expected RED: generation reopens INCAR, resolves/reads POTCAR again, and reselects
the submit script and vdW kernel; target discovery/conflict remains outside the
aggregate result.

Implementation requirements:

1. Define frozen input-domain values in `inputs.py`: a prepared source identity
   containing exact path, byte size, and SHA-256; a prepared INCAR template
   containing its parsed analysis/document and optional prepared vdW source; and
   a prepared POTCAR record containing element, exact POTCAR source, and ENMAX.
2. Preflight returns tuple-based prepared templates and POTCAR records plus one
   prepared submit-script source. It records source identity without retaining
   POTCAR/kernel contents in memory.
3. Move complete target-stage discovery and conflict checking into
   `build_preflight.py`. Conflict checking remains the first operation after the
   config safety gate and short-circuits other domain validation.
4. Return exact enabled stage and target directory paths after stackings and
   validation angles are known. Validate that every path remains inside
   `work_dir` without creating it.
5. Render INCAR from the returned document. The build path must not call
   `parse_incar()` or reread the template.
6. Concatenate POTCAR bytes only from prepared source paths and calculate ENCUT
   from the prepared ENMAX values. Do not call `resolve_potcar_dir()` or
   `read_enmax()` during generation.
7. Copy only the prepared submit script and per-template vdW kernel. Do not
   rediscover whether a kernel is required during generation.
8. Immediately before reading/copying each prepared external source, verify its
   size and SHA-256. A changed source is a runtime generation failure: it may
   leave a partial target but cannot publish a completion manifest.
9. Keep existing standalone `inputs.py` convenience wrappers if tests or public
   callers use them, but the Stage0/Stage1 build path must use only prepared
   variants.
10. Remove build-layer rcut/input reselection helpers once no supported caller
    remains. Do not alter KPOINTS scientific semantics.
11. Use only synthetic tiny POTCAR-like text in tests; never read or stage a real
    POTCAR.

Focused GREEN: rerun the RED command.

Affected suite:

```powershell
& $python -m pytest tests/test_build.py tests/test_build_lifecycle.py tests/test_incar.py tests/test_md_normalization.py tests/test_stage_provenance.py tests/test_structures_inputs.py tests/test_symmetry.py -q -p no:cacheprovider
```

Run the standard full checkpoint. Suggested orchestrator commit:
`refactor: consume prepared build inputs`. Update ignored status to Task 5 `inspect`
and stop.

## Task 5: Restore Provenance Module Ownership and Close Plan 06R

Execution profile: persistent Luna worker thread under Sol orchestrator.
RED handoff: split RED then GREEN.
`standalone_eligible: false` because this changes a cross-module ownership API.

Direct authorities:

- route-correction design, Unit 4 and final gate;
- roadmap ownership for `provenance.py` and `build_preflight.py`;
- P1-5 strict and legacy evidence rules;
- P1-1 Stage0 anchor identity.

Allowed files:

- `src/dpmoire_lite/provenance.py`
- `src/dpmoire_lite/build_preflight.py`
- `src/dpmoire_lite/build.py`
- `tests/test_md_normalization.py`
- `tests/test_stage_provenance.py`

Before RED, the orchestrator fixes the exact public provenance result/issue shape
in the capsule. The worker then adds failing ownership/API tests against that
shape:

- `test_provenance_module_builds_stage0_structure_and_anchor_records()`;
- `test_provenance_module_returns_strict_stage1_result_and_issues()`;
- `test_provenance_module_returns_legacy_stage1_result_and_issues()`;
- `test_build_preflight_delegates_stage1_provenance_validation()`;
- `test_build_uses_provenance_module_for_stage0_manifest_evidence()`.

Tests exercise public domain results and monkeypatched delegation. Do not assert
source line counts, inspect private source text, or merely rename a private
function.

RED command:

```powershell
& $python -m pytest tests/test_stage_provenance.py tests/test_md_normalization.py -q -p no:cacheprovider -k "provenance_module or delegates_stage1_provenance or stage0_manifest_evidence"
```

Expected RED: strict/legacy/anchor rules and Stage0 evidence builders still live
in `build_preflight.py` or `build.py`, and no provenance-domain result API exists.

The worker returns after RED. The orchestrator confirms that the failure matches
the already fixed API and that no behavior change is authorized, then sends the
same worker back without changing the tests or interface.

GREEN requirements:

1. Implement the capsule-fixed immutable `ProvenanceIssue` and
   `Stage1ProvenanceResult` records. The result carries strict/legacy kind,
   trusted `sc_rlx`, applicable `sc`, evidence, validated anchor information,
   and stable issues without importing `PreflightDiagnostic`.
2. Move structure-identity record serialization, Stage0 structure-provenance
   production, and Stage0 grid-shift anchor record validation to
   `provenance.py`.
3. Move strict current-manifest validation, deterministic legacy inference,
   topology/cell comparison, and preservation-anchor validation with their
   private helpers to `provenance.py`.
4. `build_preflight.py` imports the provenance module as a domain boundary,
   invokes it in the approved order, maps domain issues to
   `PreflightDiagnostic(domain="provenance", ...)`, appends warnings, and builds
   the prepared aggregate result. It must not retain a second copy of any rule.
5. `build.py` asks `provenance.py` to construct Stage0 manifest evidence and
   consumes the already validated Stage1 result. It does not validate provenance
   itself.
6. Preserve all Task 2 top-level-anchor, exactly-once warning, Task 3 prepared
   structure, and Task 4 prepared-input behavior byte-for-byte or semantically as
   asserted by existing tests.
7. Remove now-unused `Counter`, numpy/constraint, manifest, and structure
   validation imports from orchestration modules only after callers are proven
   absent.
8. Do not introduce a new framework or split provenance into additional modules.

Focused GREEN: rerun the RED command.

Affected suite and final Plan 06R product gate:

```powershell
& $python -m pytest tests/test_build.py tests/test_build_lifecycle.py tests/test_manifest_v2.py tests/test_md_normalization.py tests/test_stage_provenance.py tests/test_structures_inputs.py tests/test_symmetry.py -q -p no:cacheprovider
& $python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

Before committing, the orchestrator also runs every audit in the Plan Checkpoint
below. Suggested commit: `refactor: restore provenance module ownership`.

After the commit, record whether Tasks 2 and 5 both completed without worker
authority/scope/TDD violations. If yes, record that the two-success calibration
threshold is met; this does not make any Plan 06R unit standalone. Set ignored
status to Plan 07 Task 1 `inspect`, then stop.

## Acceptance Traceability

| Plan 06R requirement | Evidence |
| --- | --- |
| four progressive `AGENTS.md` layers | Task 1 governance tests and line audit |
| bounded Sol/Luna prompts and local state | Task 1 tests and ignore audit |
| two-thread serial authority boundary | Task 1 tests and no-write contract drill |
| top-level-only Stage0 anchors | Task 2 manifest/MD tests |
| nested-only preservation fails closed | Task 2 MD tests |
| legacy success warns exactly once | Task 2 provenance tests |
| no temporary input normalization file | Task 3 structure test |
| symmetry and manifest stackings chosen in preflight | Task 3 lifecycle/symmetry tests |
| generation consumes prepared structures and rcut | Task 3 integration tests |
| INCAR/POTCAR/script/vdW prepared once | Task 4 input/lifecycle tests |
| prepared external source identity verified | Task 4 mutation tests |
| preflight owns exact targets and conflict precedence | Task 4 lifecycle tests |
| provenance rules live in `provenance.py` | Task 5 domain/delegation tests |
| full regression and deferred gates remain green | every checkpoint and final gate |

## Plan Checkpoint

Plan 06R is complete only after all five green commits exist and the following
audits pass. Exact pytest totals may grow; do not compare against a hard-coded
count.

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'

& $python -m pytest tests/test_execution_governance.py tests/test_repository_hygiene.py -q -p no:cacheprovider
& $python -m pytest tests/test_build.py tests/test_build_lifecycle.py tests/test_incar.py tests/test_manifest_v2.py tests/test_md_normalization.py tests/test_stage_provenance.py tests/test_structures_inputs.py tests/test_symmetry.py -q -p no:cacheprovider
& $python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
git check-ignore -v docs/superpowers/plans/2026-07-11-code-review-followup/IMPLEMENTATION-STATUS.local.md
git check-ignore -v docs/superpowers/plans/2026-07-11-code-review-followup/IMPLEMENTATION-HISTORY.local.md
git ls-files "*IMPLEMENTATION-*.local.md"
```

Manual structural audit:

1. Exactly the four named `AGENTS.md` files are tracked, each at most 120 lines.
2. Orchestrator prompt is at most 120 lines; worker prompt and ignored active
   state are each at most 80 lines.
3. No ignored status/history file is tracked or staged.
4. Both prompts define two persistent threads, external UI-selected Sol/Luna Max
   Standard profiles, serial handoff, and one write-capable thread at a time.
5. New relaxation manifests contain top-level anchors and no nested anchor
   collection.
6. Both successful and failed preflight leave input/work trees unchanged until
   generation begins.
7. `build_stage0()` and `build_stage1()` contain no second supported structure,
   stacking, rcut, template, POTCAR, script, or vdW interpretation path.
8. `build_preflight.py` contains orchestration/diagnostic aggregation but no
   duplicate strict, legacy, topology, or anchor provenance implementation.
9. `stage: all`, submitted `--wait`, Slurm automation, restart, and fuzzy-dedup
   gates remain fail-closed or deferred exactly as before.
10. Plans 07 and 09 were not started before this gate.

The final read-only status reconstruction/resume drill must select Plan 07 Task 1
and no other unit. Plan 10 remains blocked by Plans 07, 08, 08A, and 09 even
though its Plan 06R predecessor is now green.
