# Plan 00: Containment Baseline

Authoritative specs:

- [P0 repository hygiene](../../../code-review-notes/P0-repository-hygiene.md)
- [P1-2 automation safety gate](../../../code-review-notes/P1-2-automation-safety-gate.md)
- [Testing and tooling scope](../../../code-review-notes/testing-and-tooling-scope.md)

Depends on: approved roadmap and design

Unlocks: Plans 01, 02, 03, and 07

## Goal and Done State

Freeze the approved review corpus, prevent private VASP data from entering the
repository, and close the known-unsafe automated Slurm entry points before any
larger behavior change begins.

Done means:

- `/example-test/` is root-ignored and absent from git/wheel inputs;
- no POTCAR is tracked or packaged;
- `stage: all` always fails before side effects;
- `submit: true` combined with `--wait` fails for Stage0 and Stage1 before runner
  creation, filesystem mutation, or `sbatch`;
- `submit: false` manual Stage0/Stage1 generation remains available;
- `submit: true` without `--wait` is documented only as fire-and-forget;
- direct symmetry-reduction behavior has a stable test independent of the private
  calculation sample;
- the approved notes and plan documents are intentionally versioned without
  unrelated working-tree content.

## Non-goals

- Do not fix Slurm state parsing, retries, or final-failure propagation.
- Do not re-enable `stage: all` through a hidden flag.
- Do not copy any complete file from `example-test/` into tests.
- Do not create ML_ABN or OUTCAR fixtures yet; Plans 03 and 07 own their minimum
  content and redistribution records.
- Do not refactor build generation beyond the safety gate.

## Files

Create:

- `tests/test_repository_hygiene.py`
- `tests/test_symmetry.py`
- `tests/data/README.md`

Modify:

- `.gitignore`
- `src/dpmoire_lite/config.py`
- `src/dpmoire_lite/cli.py`
- `src/dpmoire_lite/build.py` only if the direct-call gate cannot remain in the
  config boundary
- `tests/test_config.py`
- `tests/test_build.py`
- `tests/test_cli.py`
- `README.md`
- `README_CH.md`
- `workflow.md`
- `workflow_CH.md`

Review and version intentionally:

- `docs/2026-07-11-code-review.md`
- `docs/code-review-notes/`
- `docs/superpowers/specs/2026-07-11-code-review-followup-decomposition-design.md`
- `docs/superpowers/plans/2026-07-11-code-review-followup/`

## Task 1: Freeze Repository Hygiene and Approved Documentation

Tests/audits:

- add `test_tests_data_contains_no_potcar_or_example_test_payload()`;
- add `test_bundled_package_tree_contains_no_potcar()`;
- document fixture provenance fields in `tests/data/README.md`: source type,
  reason for cropping, removed private data, redistribution confirmation, and
  expected parser behavior.

Red/audit command:

```powershell
git check-ignore -v example-test
git status --short
git ls-files | Select-String -Pattern '(^|[/\\])POTCAR$|example-test'
```

Expected initial result: the root ignore check succeeds because the approved
working-tree rule already exists, but the approved review corpus is still
untracked and must be reviewed intentionally. No `example-test/` content may be
staged as part of resolving that status.

Implementation:

1. Keep the exact root-anchored `.gitignore` entry `/example-test/`.
2. Add only synthetic test-data metadata; do not crop parser fixtures in this
   plan.
3. Review the approved docs for accidental user paths, credentials, or POTCAR
   content.
4. Stage the explicit file list from this task only and inspect the cached diff.

Verification:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_repository_hygiene.py tests/test_cli.py -q -p no:cacheprovider
git diff --cached --name-status
```

Checkpoint: one documentation/hygiene commit containing no production behavior.

## Task 2: Add Failing Safety-gate Tests

Add these tests before changing the gate:

- `test_stage_all_is_temporarily_disabled_for_submit_false()`;
- `test_stage_all_is_temporarily_disabled_for_submit_true_wait()`;
- `test_submitted_wait_is_disabled_for_stage0()`;
- `test_submitted_wait_is_disabled_for_stage1()`;
- `test_safety_gate_runs_before_work_dir_creation()`;
- `test_safety_gate_runs_before_runner_construction()`;
- `test_fire_and_forget_stage0_remains_allowed()`;
- `test_manual_stage0_and_stage1_remain_allowed()`.

Use a nonexistent `work_dir` and a runner factory/monkeypatch that raises if
constructed. Assert the error contains the temporary reliability reason and the
manual `submit: false` recommendation.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_config.py tests/test_build.py -q -p no:cacheprovider -k "stage_all or submitted_wait or safety_gate or fire_and_forget"
```

Expected failure: current validation accepts `stage: all` when submit/wait are
enabled and accepts submitted wait for individual stages.

Checkpoint: failing tests only; do not commit them as a green implementation
checkpoint.

## Task 3: Implement the Fail-closed Build-mode Contract

Implementation requirements:

1. Make the config/build validation boundary reject `stage: all` unconditionally.
2. Reject `config.submit and wait` for Stage0 and Stage1.
3. Run the check immediately after config load and again in direct build-stage
   entry points that can be called without `run_build()`.
4. Do not instantiate `SlurmRunner`, create `work_dir`, write root artifacts, or
   call any build helper before validation succeeds.
5. Preserve `submit: true, wait: false` and `submit: false` behavior.
6. Do not expose an environment-variable bypass.

Green command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_config.py tests/test_build.py -q -p no:cacheprovider -k "stage_all or submitted_wait or safety_gate or fire_and_forget"
```

Then run all build/config tests:

```powershell
& $python -m pytest tests/test_config.py tests/test_build.py tests/test_slurm.py -q -p no:cacheprovider
```

Checkpoint: safety-gate production change and its tests.

## Task 4: Align CLI and Workflow Documentation

Add/adjust tests:

- `test_build_help_marks_wait_temporarily_disabled_for_submitted_workflows()`;
- `test_build_error_recommends_manual_submission()`.

Documentation requirements:

- `stage: all` is unavailable, not merely discouraged;
- `--wait` cannot be used with submitted jobs;
- fire-and-forget only guarantees successful `sbatch` invocation;
- `auto_resub` is not presented as production-ready while wait is disabled;
- the recommended workflow is `submit: false`, manual distribution/submission,
  output inspection, then the next stage.

Verification:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_cli.py tests/test_config.py tests/test_build.py -q -p no:cacheprovider
```

Checkpoint: user-facing safety contract synchronized with production behavior.

## Task 5: Add Direct Symmetry and Baseline Quality Tests

Add tests using small synthetic layer inputs and `tmp_path`:

- `test_find_sym_reduced_stackings_is_stable_and_unique()`;
- `test_find_sym_reduced_stackings_supports_rectangular_sectors()`;
- `test_find_sym_reduced_stackings_reports_missing_optional_dependency()`.

The first two tests must compare returned stackings and ensure no duplicates. Do
not rely on the private example directory. The optional-dependency test must
assert a clear project error rather than a deep import traceback; it may use
monkeypatching so the installed environment need not be modified.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_symmetry.py -q -p no:cacheprovider
```

Full checkpoint:

```powershell
& $python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

The test may continue to observe the current auxiliary stacking file; Plan 04
owns moving its publication behind the global no-side-effect preflight.

## Acceptance Traceability

| Requirement | Evidence |
| --- | --- |
| `/example-test/` root ignored | `git check-ignore -v example-test` |
| no POTCAR in git/wheel inputs | repository hygiene tests and `git ls-files` audit |
| `stage: all` always closed | Stage0/build direct and CLI tests |
| submitted `--wait` closed | Stage0 and Stage1 parameterized tests |
| gate before side effects | nonexistent-workdir and runner-construction tests |
| manual/fire-and-forget paths retained | focused positive-path tests |
| documentation states limited guarantee | CLI help and workflow review |
| symmetry reduction directly covered | `tests/test_symmetry.py` |

## Plan Checkpoint

Plan 00 is complete when its focused suites and full suite pass, repository audits
show no private VASP data, and the approved spec/plan corpus is versioned
intentionally. P1-2 remains active for every later plan.
