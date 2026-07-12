# Plan 10: Collect Integration and CLI Outcomes

Authoritative specs:

- [P2-3 Collection output safety](../../../code-review-notes/P2-3-collection-output-safety.md)
- [P2-6 Collect result and exit codes](../../../code-review-notes/P2-6-collect-exit-codes.md)
- [P2-1 Seed/source integration](../../../code-review-notes/P2-1-partial-collection-and-mlff-seed.md)
- [Approved full-dedup design](../../specs/2026-07-12-mlff-full-dedup-legacy-collection-design.md)
- [Testing and tooling scope](../../../code-review-notes/testing-and-tooling-scope.md)

Consumes:

- [Plan 06 MD normalization and seed staging](06-md-normalization-seed-staging.md)
- [Plan 06R Route correction and execution governance](06r-route-correction-execution-governance.md)
- [Plan 08 Per-source collection](08-source-collection.md)
- [Plan 08A MLFF full-dedup and legacy inventory](08a-mlff-full-dedup-legacy-collection.md)
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
- `seed-aware` remains the default and explicit `full-dedup` preserves all unique
  final ML_ABN configurations without using current ML_AB counts;
- valid current v2 collection updates its stage manifest, while legacy/missing
  compatibility collection safely publishes `MD_data.collect.yaml`;
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
- Do not infer collection mode from `ML_ISTART`, `ML_MODE`, OUTCAR, or current
  `md/ML_AB`.
- Do not retain the old direct-write or zero-frame unlink fallback.

## Files

Modify:

- `src/dpmoire_lite/collect.py`
- `src/dpmoire_lite/collect_models.py`
- `src/dpmoire_lite/mlff_collect.py` only for integration defects proven by tests
- `src/dpmoire_lite/collect_publish.py` only for integration defects proven by
  tests
- `src/dpmoire_lite/manifest.py` only for final field validation
- `src/dpmoire_lite/cli.py`
- `tests/test_collect.py`
- `tests/test_source_collection.py`
- `tests/test_mlff_full_dedup.py`
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
  skipped, failed, coverage declined, or a missing-manifest legacy scan leaves
  expected coverage unknown;
- `no_data`: frames == 0, no new extxyz published, safe no-data manifest committed;
- `fatal`: global invariant or publication/recovery failure; no success is claimed.

Pre-safe-boundary fatal may have no writable manifest. Its structured result and
stderr/exit 1 are authoritative.

Exact duplicate removal alone is not degraded. `full-dedup` with current/legacy
declared coverage may be complete; missing-manifest scan with frames is at best
degraded. The result-manifest target is selected from the validated input layout,
not from output-file existence.

## Task 1: Implement Pure Aggregate-status Selection

Add failing tests:

- `test_complete_requires_frames_and_all_expected_sources_complete()`;
- `test_partial_with_frames_selects_degraded()`;
- `test_skipped_or_failed_with_other_frames_selects_degraded()`;
- `test_coverage_decline_with_frames_selects_degraded()`;
- `test_unknown_coverage_legacy_scan_with_frames_selects_degraded()`;
- `test_exact_duplicates_alone_do_not_select_degraded()`;
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
2. Treat `coverage_known: false` with frames as degraded regardless of discovered
   source success.
3. Keep publication success as a prerequisite for complete/degraded final status.
4. Do not represent no-data as an exception.
5. Represent global invariant/publication exceptions as typed fatal results at the
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
- `test_no_data_creates_no_data_candidate_or_two_file_journal()`;
- `test_legacy_no_data_writes_only_compatibility_result_manifest()`;
- `test_missing_scan_no_data_does_not_create_stage_manifest()`.

Implementation requirements:

1. Acquire the same Plan 09 stage/output lock and recover any existing journal.
2. Do not open final extxyz for writing or deletion.
3. Select current stage Manifest v2 or compatibility result target from the
   validated input layout.
4. Build a no-data result-manifest candidate with full source diagnostics.
5. Record previous output identity/frame count when safely readable.
6. Atomically publish only the selected result manifest through Plan 09.
7. Return no-data only after that manifest commit succeeds.
8. On failure, return fatal and leave old output/result/source manifests
   unchanged.

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
- `test_published_manifest_and_output_share_transaction_and_hash()`;
- `test_full_dedup_current_v2_publishes_counts_to_stage_manifest()`;
- `test_full_dedup_legacy_publishes_compatibility_result_manifest()`;
- `test_missing_scan_publication_is_degraded_and_records_inventory()`;
- `test_compatibility_publication_never_rewrites_legacy_stage_manifest()`.

Implementation requirements:

1. Pass the Plan 08 or 08A candidate and selected aggregate status to Plan 09.
2. Compare previous frame/source/directory coverage under the lock.
3. Publish nonzero output only through the data+manifest transaction.
4. Include new/old counts, backup path/hash, source results, selection order,
   collection mode, dedup/inventory evidence, and transaction identity in the
   selected result manifest.
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

- `test_seed_aware_missing_stage_manifest_returns_fatal_without_creating_manifest()`;
- `test_missing_manifest_full_dedup_uses_legacy_scan_instead_of_fatal()`;
- `test_invalid_manifest_returns_fatal_without_overwrite()`;
- `test_invalid_manifest_full_dedup_never_falls_back_to_scan()`;
- `test_config_error_returns_fatal_before_lock_or_manifest()`;
- `test_invalid_manifest_seed_schema_is_global_fatal()`;
- `test_unrecoverable_pending_journal_returns_fatal_and_preserves_evidence()`;
- `test_recovered_transaction_returns_its_committed_status()`;
- `test_recovery_completes_before_new_source_collection_starts()`;
- `test_lock_contention_returns_fatal()`;
- `test_current_v2_manifest_takes_precedence_over_old_compatibility_result()`;
- `test_compatibility_previous_result_requires_matching_output_hash()`.

Clarify source versus global failure:

- one source seed-prefix mismatch is a failed source; if other sources provide
  frames, aggregate is degraded, and if none provide frames, no-data;
- invalid/missing stage manifest, path escape, unsupported schema, or transaction
  inconsistency is global fatal, except the exact full-dedup missing-manifest
  scan defined by P2-6;
- an invalid/unsupported/escaping manifest is always fatal in both modes.

Implementation requirements:

1. Catch only typed domain/config/publication errors at the orchestration boundary.
2. Do not catch programming errors as degraded source failures.
3. Perform journal recovery under lock before building a new candidate.
4. If recovery commits an old transaction, return its manifest status without
   starting a second transaction in the same invocation.
5. Preserve evidence and avoid fake manifest writes on pre-safe fatal.
6. For compatibility collection, recover and validate the exact
   `MD_data.collect.yaml` target without treating a legacy stage manifest as a
   writable current manifest.
7. When a valid current v2 stage manifest exists, it is authoritative and an old
   compatibility result is historical only. Without current v2, a previous
   compatibility result/output hash mismatch is fatal, not guessed around.

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
- `test_cli_does_not_infer_exit_code_from_manifest_or_log_text()`;
- `test_collect_cli_defaults_mlff_mode_to_seed_aware()`;
- `test_collect_cli_accepts_full_dedup_only_for_mlff_md()`;
- `test_collect_cli_rejects_full_dedup_for_rlx_validation_or_non_ml_md()`.

Implementation requirements:

1. Make `run_collect()` return `CollectResult`.
2. Make `collect_command()` map enum values to exact integers.
3. Print a concise status/frame/source/output summary.
4. Keep full per-source diagnostics in the selected result manifest when safely
   writable.
5. Do not convert exit 2 or 3 to 0.
6. Parse `--mlff-collect-mode seed-aware|full-dedup` to the Plan 08A enum and
   validate the stage/source-kind combination before collection starts.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_cli.py tests/test_collect.py -q -p no:cacheprovider -k "collect_cli or exit or summary"
```

Update README/workflow/CHANGELOG with the exit-code table and non-destructive
no-data behavior, default seed-aware semantics, explicit full-dedup cost/meaning,
and legacy compatibility boundary.

Checkpoint: CLI/public documentation contract.

## Task 6: Run Real Producer/Consumer and Final Integration Tests

Add end-to-end tests:

- `test_stage1_seed_manifest_is_accepted_by_md_collect()`;
- `test_multiple_restart_mlab_collects_all_post_initial_seed_frames()`;
- `test_full_dedup_fresh_restart_keeps_pre_restart_configurations()`;
- `test_full_dedup_repeated_seed_is_published_once()`;
- `test_default_and_explicit_seed_aware_outputs_match()`;
- `test_vasp_641_and_651_mlab_fixtures_collect_end_to_end()`;
- `test_missing_manifest_full_dedup_writes_degraded_compatibility_result()`;
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
& $python -m pytest tests/test_build.py tests/test_md_normalization.py tests/test_source_collection.py tests/test_mlff_full_dedup.py tests/test_collect_publish.py tests/test_collect.py tests/test_cli.py -q -p no:cacheprovider
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
| full-dedup current/legacy publication | Task 3 |
| missing scan is bounded degraded exception | Tasks 1, 3, and 4 |
| compatibility result preserves legacy manifest | Tasks 2 through 4 |
| pre-safe fatal creates no fake manifest | Task 4 |
| deterministic recovery before new work | Task 4 |
| CLI returns 0/1/2/3 exactly | Task 5 |
| CLI mode default/validation | Task 5 |
| Stage1 seed producer/consumer agreement | Task 6 |
| fresh restart and exact dedup end to end | Task 6 |
| VASP 6.4.1/6.5.1 compatibility | Task 6 |
| private fixture skips removed | Task 6 |
| wheel/git/POTCAR audits | Task 6 |
| Slurm gate remains active | full suite and Plan 00 regression |

## Final Definition of Done

Plan 10 and the full follow-up are complete only when:

- every predecessor checkpoint, including Plan 08A, is green;
- all Plan 10 focused and full suites pass;
- every authoritative acceptance criterion maps to a passing test or audit;
- no core parser/collect test depends on the private absolute sample path;
- complete, degraded, no-data, fatal, pending recovery, and committed residual
  behavior pass end to end;
- default seed-aware and explicit full-dedup behavior pass end to end for current,
  legacy, and permitted missing-manifest layouts;
- wheel/examples/docs match the final config and CLI contracts;
- no POTCAR/private sample is tracked or packaged;
- the project leader reviews the final traceability and implementation diff.
