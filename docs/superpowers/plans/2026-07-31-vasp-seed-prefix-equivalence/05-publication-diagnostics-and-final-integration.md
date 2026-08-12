# Ticket 05: Publication Diagnostics and Final Integration

Depends on: Ticket 04

Unlocks: completion of this ticket set

## Goal

Publish structured exact/equivalent/mismatch evidence through the existing
transaction, close end-to-end CLI behavior and documentation, confirm the
motivating case without making it a test dependency, and run all safety audits.

## Files

Read first:

- `src/dpmoire_lite/AGENTS.md`
- `tests/AGENTS.md`

Modify as required by proven integration REDs:

- `src/dpmoire_lite/collect.py`
- `src/dpmoire_lite/collect_models.py`
- `src/dpmoire_lite/manifest.py`
- `tests/test_collect.py`
- `tests/test_collect_publish.py`
- `tests/test_cli.py`
- `README.md`
- `README_CH.md`
- `workflow.md`
- `workflow_CH.md`
- `CHANGELOG.md`

Do not modify CLI options or config schema.

## Tests

Add:

- `test_vasp_equivalent_source_publishes_complete_when_coverage_is_complete()`;
- `test_vasp_equivalent_source_publishes_degraded_only_for_missing_coverage()`;
- `test_collect_manifest_records_seed_verification_counts_and_deltas()`;
- `test_seed_verification_mismatch_publishes_source_failure_diagnostic()`;
- `test_default_seed_aware_cli_collects_vasp_rewritten_prefix()`;
- `test_vasp_rewrite_regression_publishes_only_post_seed_frames()`;
- `test_full_dedup_output_and_dedup_counts_are_unchanged()`;
- `test_no_data_and_previous_output_preservation_are_unchanged()`;
- `test_result_manifest_never_dumps_full_seed_configurations()`.

## RED

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
& $python -c "import sys; print(sys.executable)"
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_collect.py tests/test_collect_publish.py `
  tests/test_cli.py -q -p no:cacheprovider `
  -k "vasp_equivalent or vasp_rewrite or seed_verification"
```

Expected RED: the source candidate may now accept rewritten prefixes, but the
published result/CLI end-to-end contract lacks the required structured evidence
and regression behavior.

## GREEN

1. Serialize source verification evidence through the existing result-manifest
   transaction.
2. Add aggregate exact/equivalent/mismatch counts derived from structured source
   results.
3. Treat approved VASP equivalence alone as complete; incomplete declared source
   coverage still selects degraded.
4. Keep mismatch, no-data, backup, lock, journal, and recovery behavior
   unchanged.
5. Keep the existing short CLI summary and exit-code mapping; do not add flags.
6. Document exact identity versus VASP pairwise equivalence and retain
   `full-dedup` as the explicit workaround/legacy mode.
7. Ensure diagnostics contain hashes, counts, methods, and bounded deltas, never
   complete configurations.

Run the focused command to green.

## Original-case Manual Confirmation

Use the ignored motivating calculation only after all automated tests are green.
Prefer the in-memory candidate seam first. If running the CLI, record that it
updates only ignored runtime state and ensure no previous dataset is overwritten
without an intentional backup.

Expected current-state evidence:

- `frames=1655`;
- `complete=7`;
- `partial=0`;
- `skipped=14`;
- `failed=0`;
- seven `vasp_equivalent` seed verifications;
- aggregate `degraded` / exit 2 because declared coverage is incomplete;
- the 63-frame initial seed is excluded rather than retained once.

Do not record private frame content in tracked files.

## Affected and Full Verification

```powershell
& $python -m pytest tests/test_mlab.py tests/test_mlff_seed.py `
  tests/test_source_collection.py tests/test_mlff_full_dedup.py `
  tests/test_collect_publish.py tests/test_collect.py tests/test_cli.py `
  -q -p no:cacheprovider
& $python -m pytest -q -p no:cacheprovider
& $python -m pytest tests/test_cli.py -q -p no:cacheprovider -k "wheel or init_example"
git ls-files | Select-String -Pattern '(^|[/\\])POTCAR$|example-test'
Get-ChildItem -Path 'tests','dist' -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -eq 'POTCAR' -or $_.FullName -match 'example-test' }
git diff --check
git status --short
```

Expected: every suite and audit is green; no private source is tracked or
packaged; only ticket-owned production/tests/docs changes are present.

## Final Review

- Exact `mlab-seed-v1` and `mlab-config-v1` outputs are unchanged.
- Full-dedup never calls approximate verification.
- Exact path does not require a reference read.
- VASP fallback uses only a raw-hash-verified or explicitly legacy-rebuilt
  reference.
- Numeric policy is fixed and versioned, not configurable.
- Source and aggregate counts equal published frames and diagnostics.
- No future-ticket RED, debug instrumentation, temporary fixture, journal, or
  candidate remains.

## Checkpoint

All acceptance criteria are met. Stop; do not begin unrelated follow-up work.
