# Plan 10: Collect Integration and CLI Outcomes

Authoritative specs:

- [P2-3 Collection output safety](../../../code-review-notes/P2-3-collection-output-safety.md)
- [P2-6 Collect result and exit codes](../../../code-review-notes/P2-6-collect-exit-codes.md)
- [P2-1 Seed/source integration](../../../code-review-notes/P2-1-partial-collection-and-mlff-seed.md)
- [Testing and tooling scope](../../../code-review-notes/testing-and-tooling-scope.md)

Consumes:

- [Plan 06 MD normalization and seed staging](06-md-normalization-seed-staging.md)
- [Plan 08 Per-source collection](08-source-collection.md)
- [Plan 09 Collection publication engine](09-collection-publication-engine.md)

Unlocks: final integration gate

## Goal and Done State

Wire the real build producer, source collectors, publication engine, manifest
diagnostics, and CLI boundary into one end-to-end collect contract.

Done means:

- `run_collect()` returns a structured `CollectResult`;
- complete, degraded, no-data, and fatal have the exact agreed meanings;
- nonzero candidates are published only through Plan 09;
- zero-frame runs never create/delete/replace extxyz and atomically record
  `no_data` only when a safe manifest boundary exists;
- old output is preserved and existing output replacement has a verified backup;
- current Stage1 seed identity is consumed successfully end to end;
- recoverable journals are completed before new collection begins;
- unrecoverable publication/provenance/manifest failures return fatal;
- CLI alone maps statuses to 0, 2, 3, and 1;
- final tests and package/git audits satisfy the full review scope.

## Non-goals

- Do not turn exit 2 or 3 into shell success.
- Do not create a fake fatal manifest when config/stage manifest cannot be safely
  read.
- Do not add a destructive clear-output command.
- Do not re-enable Slurm automation.
- Do not implement MD restart orchestration or fuzzy dataset deduplication.
- Do not retain the old direct-write or zero-frame unlink fallback.

## Files

Modify:

- `src/dpmoire_lite/collect.py`
- `src/dpmoire_lite/collect_models.py`
- `src/dpmoire_lite/collect_publish.py` only for integration defects proven by
  tests
- `src/dpmoire_lite/manifest.py` only for final field validation
- `src/dpmoire_lite/cli.py`
- `tests/test_collect.py`
- `tests/test_source_collection.py`
- `tests/test_collect_publish.py`
- `tests/test_cli.py`
- `tests/conftest.py`
- `README.md`
- `README_CH.md`
- `workflow.md`
- `workflow_CH.md`
- `CHANGELOG.md`

Potentially remove:

- private absolute-path `sample_dir` fixture/skip logic after every owning test is
  migrated to `tests/data/`.

## Aggregate Status Contract

The core result contains:

- `status: complete | degraded | no_data | fatal`;
- accepted frame count;
- source counts/results;
- output/manifest/backup/transaction identity when safely available;
- previous coverage comparison;
- warnings and fatal diagnostic.

Selection rules:

- `complete`: frames > 0, every expected source complete, no skipped/partial/
  failed source, no coverage decline, transaction committed;
- `degraded`: frames > 0 and transaction committed, but any source is partial,
  skipped, failed, or coverage declined;
- `no_data`: frames == 0, no new extxyz published, safe no-data manifest committed;
- `fatal`: global invariant or publication/recovery failure; no success is claimed.

Pre-safe-boundary fatal may have no writable manifest. Its structured result and
stderr/exit 1 are authoritative.

## Task 1: Implement Pure Aggregate-status Selection

Add failing tests:

- `test_complete_requires_frames_and_all_expected_sources_complete()`;
- `test_partial_with_frames_selects_degraded()`;
- `test_skipped_or_failed_with_other_frames_selects_degraded()`;
- `test_coverage_decline_with_frames_selects_degraded()`;
- `test_zero_frames_selects_no_data_before_publication()`;
- `test_global_invariant_failure_selects_fatal()`;
- `test_status_selection_does_not_parse_log_text()`.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_collect.py -q -p no:cacheprovider -k "status or complete or degraded or no_data or fatal"
```

Expected failure: current collect returns `None` and has no aggregate result
contract.

Implementation requirements:

1. Derive status from structured results and previous manifest coverage.
2. Keep publication success as a prerequisite for complete/degraded final status.
3. Do not represent no-data as an exception.
4. Represent global invariant/publication exceptions as typed fatal results at the
   orchestration boundary.

Run the focused command to green.

Checkpoint: pure outcome selection.

## Task 2: Wire Zero-frame Manifest-only Publication

Add failing tests:

- `test_no_data_preserves_existing_output_bytes()`;
- `test_no_data_without_previous_output_creates_no_extxyz()`;
- `test_no_data_records_preserved_previous_and_previous_frame_count()`;
- `test_no_data_manifest_only_publish_uses_stage_output_lock()`;
- `test_no_data_manifest_failure_returns_fatal_not_no_data()`;
- `test_no_data_failure_preserves_previous_manifest()`;
- `test_no_data_creates_no_data_candidate_or_two_file_journal()`.

Implementation requirements:

1. Acquire the same Plan 09 stage/output lock and recover any existing journal.
2. Do not open final extxyz for writing or deletion.
3. Build a no-data Manifest v2 candidate with full source diagnostics.
4. Record previous output identity/frame count when safely readable.
5. Atomically publish only the manifest through Plan 09.
6. Return no-data only after that manifest commit succeeds.
7. On failure, return fatal and leave old output/manifest unchanged.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_collect.py tests/test_collect_publish.py -q -p no:cacheprovider -k "no_data or manifest_only"
```

Checkpoint: safe no-data behavior.

## Task 3: Wire Nonzero Complete and Degraded Publication

Add failing tests:

- `test_complete_candidate_publishes_data_and_manifest_transaction()`;
- `test_partial_candidate_publishes_degraded_with_exact_source_counts()`;
- `test_failed_source_with_other_frames_publishes_degraded()`;
- `test_new_frames_less_than_previous_warns_with_backup()`;
- `test_source_or_directory_coverage_decline_warns_with_backup()`;
- `test_new_partial_failed_or_skipped_warns_with_backup()`;
- `test_published_manifest_frames_equal_reread_extxyz_frames()`;
- `test_published_manifest_and_output_share_transaction_and_hash()`.

Implementation requirements:

1. Pass the Plan 08 candidate and selected aggregate status to Plan 09.
2. Compare previous frame/source/directory coverage under the lock.
3. Publish nonzero output only through the data+manifest transaction.
4. Include new/old counts, backup path/hash, source results, selection order, and
   transaction identity in Manifest v2.
5. Emit warnings containing new/old coverage and backup path.
6. Do not reject a valid nonzero candidate solely because it is smaller.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_collect.py tests/test_collect_publish.py -q -p no:cacheprovider -k "publishes or coverage or backup or transaction"
```

Checkpoint: nonzero publication integration.

## Task 4: Close Fatal and Recovery Boundaries

Add failing tests:

- `test_missing_stage_manifest_returns_fatal_without_creating_manifest()`;
- `test_invalid_manifest_returns_fatal_without_overwrite()`;
- `test_config_error_returns_fatal_before_lock_or_manifest()`;
- `test_invalid_manifest_seed_schema_is_global_fatal()`;
- `test_unrecoverable_pending_journal_returns_fatal_and_preserves_evidence()`;
- `test_recovered_transaction_returns_its_committed_status()`;
- `test_recovery_completes_before_new_source_collection_starts()`;
- `test_lock_contention_returns_fatal()`.

Clarify source versus global failure:

- one source seed-prefix mismatch is a failed source; if other sources provide
  frames, aggregate is degraded, and if none provide frames, no-data;
- invalid/missing stage manifest, path escape, unsupported schema, or transaction
  inconsistency is global fatal.

Implementation requirements:

1. Catch only typed domain/config/publication errors at the orchestration boundary.
2. Do not catch programming errors as degraded source failures.
3. Perform journal recovery under lock before building a new candidate.
4. If recovery commits an old transaction, return its manifest status without
   starting a second transaction in the same invocation.
5. Preserve evidence and avoid fake manifest writes on pre-safe fatal.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_collect.py tests/test_collect_publish.py -q -p no:cacheprovider -k "fatal or recovery or contention or missing_stage_manifest"
```

Checkpoint: fatal/recovery integration.

## Task 5: Map Structured Results at the CLI Boundary

Add failing tests that directly assert integers:

- `test_collect_cli_complete_returns_zero()`;
- `test_collect_cli_fatal_returns_one()`;
- `test_collect_cli_degraded_returns_two()`;
- `test_collect_cli_no_data_returns_three()`;
- `test_collect_cli_prints_short_summary_to_stderr()`;
- `test_cli_does_not_infer_exit_code_from_manifest_or_log_text()`.

Implementation requirements:

1. Make `run_collect()` return `CollectResult`.
2. Make `collect_command()` map enum values to exact integers.
3. Print a concise status/frame/source/output summary.
4. Keep full per-source diagnostics in the manifest when safely writable.
5. Do not convert exit 2 or 3 to 0.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_cli.py tests/test_collect.py -q -p no:cacheprovider -k "collect_cli or exit or summary"
```

Update README/workflow/CHANGELOG with the exit-code table and non-destructive
no-data behavior.

Checkpoint: CLI/public documentation contract.

## Task 6: Run Real Producer/Consumer and Final Integration Tests

Add end-to-end tests:

- `test_stage1_seed_manifest_is_accepted_by_md_collect()`;
- `test_multiple_restart_mlab_collects_all_post_initial_seed_frames()`;
- `test_build_then_collect_uses_only_manifest_declared_directories()`;
- `test_rlx_multiple_outcar_segments_record_deterministic_order()`;
- `test_collect_fault_after_data_replace_recovers_manifest_next_run()`;
- `test_committed_journal_residual_is_cleaned_next_run()`;
- `test_complete_degraded_no_data_fatal_end_to_end_exit_codes()`.

Migrate core parser/collect tests away from the private `sample_dir` fixture. Once
no core test uses it, remove the absolute-path skip from `tests/conftest.py`.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_build.py tests/test_md_normalization.py tests/test_source_collection.py tests/test_collect_publish.py tests/test_collect.py tests/test_cli.py -q -p no:cacheprovider
```

Full and packaging checkpoint:

```powershell
& $python -m pytest -q -p no:cacheprovider
& $python -m pytest tests/test_cli.py -q -p no:cacheprovider -k "wheel or init_example"
git check-ignore -v example-test
git diff --check
git status --short
```

Repository/package audits:

```powershell
git ls-files | Select-String -Pattern '(^|[/\\])POTCAR$|example-test'
Get-ChildItem -Path 'tests','dist' -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq 'POTCAR' -or $_.FullName -match 'example-test' }
```

The audit must return no tracked/packaged POTCAR or raw private sample content.

ASE validation:

```powershell
& $python -c "import ase; print(ase.__version__)"
```

Run all version-agnostic tests in the available ASE 3.28 environment. If no
approved ASE 3.29 environment exists, record actual 3.29 execution as outstanding
external validation; do not install or claim it passed.

## Acceptance Traceability

| Requirement | Evidence |
| --- | --- |
| complete/degraded/no_data/fatal definitions | Task 1 |
| zero frames preserve/no-create extxyz | Task 2 |
| no-data manifest failure becomes fatal | Task 2 |
| nonzero data+manifest transaction | Task 3 |
| coverage-drop warnings with backup | Task 3 |
| pre-safe fatal creates no fake manifest | Task 4 |
| deterministic recovery before new work | Task 4 |
| CLI returns 0/1/2/3 exactly | Task 5 |
| Stage1 seed producer/consumer agreement | Task 6 |
| private fixture skips removed | Task 6 |
| wheel/git/POTCAR audits | Task 6 |
| Slurm gate remains active | full suite and Plan 00 regression |

## Final Definition of Done

Plan 10 and the full follow-up are complete only when:

- every predecessor checkpoint is green;
- all Plan 10 focused and full suites pass;
- every authoritative acceptance criterion maps to a passing test or audit;
- no core parser/collect test depends on the private absolute sample path;
- complete, degraded, no-data, fatal, pending recovery, and committed residual
  behavior pass end to end;
- wheel/examples/docs match the final config and CLI contracts;
- no POTCAR/private sample is tracked or packaged;
- the project leader reviews the final traceability and implementation diff.
