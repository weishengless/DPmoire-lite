# Plan 09: Collection Publication Engine

Authoritative specs:

- [P2-3 Collection output safety](../../../code-review-notes/P2-3-collection-output-safety.md)
- [P2-6 Legacy-compatible result manifest](../../../code-review-notes/P2-6-collect-exit-codes.md)
- [Approved full-dedup design](../../specs/2026-07-12-mlff-full-dedup-legacy-collection-design.md)

Consumes: [Plan 02 Manifest v2 and atomic I/O](02-manifest-v2.md)

Unlocks: Plan 10

## Goal and Done State

Implement and fault-test the persistence engine that safely publishes an already
classified collection candidate and matching manifest under a single-writer
transaction.

Done means:

- one OS-level lock serializes each `(stage, final_output)` across processes;
- existing output remains continuously readable while its verified backup is
  created;
- data and manifest candidates are written, fsynced, reread, and hashed before
  a pending journal exists;
- pending/committed journals make every interrupted state deterministic;
- first publication and existing-output publication follow explicit rules;
- the P/C/empty/X state table and committed-journal residual behavior are fully
  parameterized in tests;
- publication errors preserve the journal/backup evidence required for recovery;
- the request can target either the validated current stage manifest or the exact
  MLFF compatibility result path without accepting an arbitrary path;
- legacy/missing collection can pair `MD_data.extxyz` with
  `MD_data.collect.yaml` without rewriting source build provenance;
- a manifest-only atomic operation exists for Plan 10 no-data results.

## Non-goals

- Do not parse VASP sources or choose source status.
- Do not choose complete/degraded/no_data/fatal from diagnostics.
- Do not choose current versus compatibility result-manifest target; Plan 10 owns
  that policy.
- Do not map CLI exit codes.
- Do not delete older backups automatically.
- Do not create a generic database transaction framework.
- Do not merge this plan alone if project policy rejects an unwired internal API;
  keep separate commits but merge with Plan 10.

## Files

Create:

- `src/dpmoire_lite/file_lock.py`
- `src/dpmoire_lite/collect_publish.py`
- `tests/test_file_lock.py`
- `tests/test_collect_publish.py`

Modify:

- `src/dpmoire_lite/atomic_io.py` only for primitives proven missing by these
  tests
- `src/dpmoire_lite/manifest.py` only to serialize already defined transaction
  fields
- `tests/test_atomic_io.py`

## Publication Session and Request Contract

The engine first opens a publication session for validated stage/output and
result-manifest paths.
Entering the session acquires the OS lock, reads previous identity, and resolves
any existing pending/committed journal. If recovery completes an earlier
transaction, the caller returns that recovered result without collecting new
sources. Otherwise the session stays open while Plan 10 invokes Plan 08 and
classifies the new candidate.

The held session then receives an immutable request containing:

- stage, validated final output path, validated result-manifest target kind, and
  exact result-manifest path;
- transaction ID;
- candidate Dataset/frame count;
- already selected aggregate status and source diagnostics;
- expected previous output/manifest identity already read by this session under
  the lock;
- functions or serializers for data and manifest candidates.

The result-manifest target has two closed variants:

- current stage Manifest v2 at the standard stage manifest path;
- MLFF MD compatibility result at exact `work_dir/MD_data.collect.yaml`.

The second target never authorizes overwriting a legacy `md/manifest.yaml` and
is invalid for other output/stage combinations. The journal records target kind
and normalized path so recovery verifies the same artifact selected when the
transaction began. Caller-supplied arbitrary manifest paths are rejected before
candidate creation.

Plan 09 may use a test request with explicit status; Plan 10 owns real status
selection. The session returns committed paths/hashes/backup identity or raises a
typed fatal publication/recovery error. It never returns a partially committed
success and never releases/reacquires the lock between recovery, new collection,
and publication.

## Task 1: Implement the Cross-platform Single-writer Lock

Add failing tests:

- `test_collect_lock_allows_one_holder()`;
- `test_second_process_cannot_acquire_same_stage_output_lock()`;
- `test_different_stage_outputs_use_independent_locks()`;
- `test_lock_released_after_normal_exit()`;
- `test_lock_released_after_exception()`;
- `test_stale_metadata_does_not_override_os_lock_state()`;
- `test_only_lock_holder_may_update_diagnostic_metadata()`.

Use a real second process for at least one contention test. Launch it with the
explicit verified interpreter, not bare `python`.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_file_lock.py -q -p no:cacheprovider
```

Implementation requirements:

1. Use `fcntl` on Unix and an appropriate `msvcrt`/Windows locking primitive on
   Windows behind one context-manager API.
2. Lock by normalized `(stage, final_output)` identity.
3. Hold a live OS lock; metadata/sentinel existence alone is not ownership.
4. Store PID/hostname/time/stage/transaction only for diagnostics.
5. Fail immediately on contention with a typed fatal error.
6. Release on every context exit path.

Run the focused command to green.

Checkpoint: lock abstraction.

## Task 2: Write and Validate Data/Manifest Candidates

Add failing tests:

- `test_data_candidate_written_in_final_directory_and_fsynced()`;
- `test_data_candidate_reread_matches_expected_frame_count()`;
- `test_data_candidate_requires_energy_forces_and_stress()`;
- `test_data_candidate_rejects_shape_or_truncated_tail()`;
- `test_manifest_candidate_contains_transaction_and_data_hash()`;
- `test_current_target_writes_stage_manifest_candidate()`;
- `test_compatibility_target_writes_md_data_collect_candidate()`;
- `test_compatibility_target_rejects_wrong_stage_output_or_path()`;
- `test_compatibility_candidate_preserves_legacy_stage_manifest_bytes()`;
- `test_both_candidates_exist_and_validate_before_pending_journal()`;
- `test_candidate_failure_leaves_final_and_previous_manifest_unchanged()`.

Implementation requirements:

1. Write extxyz candidate in the final output directory.
2. Reread to EOF and validate frame count, atoms/composition/cell/positions,
   energy, forces, stress, and array shapes.
3. Compute SHA-256 only after candidate fsync/validation.
4. Build the selected current or compatibility result-manifest candidate with
   transaction/data/backup/source fields.
5. Fsync, reread, target-schema-validate, and hash the result-manifest candidate.
6. Do not create a journal until both candidates are finalized.
7. Never open a legacy source stage manifest for replacement when the
   compatibility target is selected.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_collect_publish.py -q -p no:cacheprovider -k "candidate"
```

Checkpoint: candidate creation/validation.

## Task 3: Back Up Existing Output Without Removing the Final Path

Add failing tests:

- `test_backup_prefers_hardlink_and_preserves_final_path()`;
- `test_backup_falls_back_to_copy_when_hardlink_unsupported()`;
- `test_backup_is_fsynced_and_hash_verified_before_publish()`;
- `test_backup_hash_mismatch_aborts_before_journal()`;
- `test_backup_name_published_atomically()`;
- `test_older_backups_are_not_deleted()`;
- `test_first_publish_has_null_previous_and_backup_fields()`.

Record path existence observations around every operation to prove no window
removes the formal output.

Implementation requirements:

1. Read previous hash/frame count under the lock.
2. Create backup temp on the same filesystem.
3. Use a hard link when possible, otherwise copy and fsync.
4. Verify backup hash equals previous output hash.
5. Atomically publish the backup name while final remains in place.
6. Preserve all older backups.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_collect_publish.py -q -p no:cacheprovider -k "backup or first_publish"
```

Checkpoint: backup behavior.

## Task 4: Implement the Normal Pending-to-committed Transaction

Add failing tests:

- `test_publish_sequence_matches_authoritative_order()`;
- `test_pending_journal_precedes_final_output_replace()`;
- `test_final_data_hash_verified_before_manifest_replace()`;
- `test_manifest_hash_and_internal_transaction_verified()`;
- `test_journal_marked_committed_before_unlink()`;
- `test_lock_held_from_recovery_through_journal_cleanup()`;
- `test_normal_publish_returns_matching_paths_and_hashes()`;
- `test_pending_journal_records_result_manifest_target_kind_and_path()`;
- `test_normal_publish_supports_compatibility_result_manifest()`.

The journal contains every field required by the P2-3 note, including nullable
previous/backup fields for first publication.

Implementation requirements:

1. Enter the publication session and acquire the lock before previous hashes and
   recovery.
2. Recover any existing journal before the caller starts a new source collection.
3. Create/validate data candidate, backup, and manifest candidate.
4. Atomically publish pending journal.
5. Replace and hash-verify data.
6. Replace and hash/transaction-verify manifest.
7. Atomically update journal to committed, then unlink it.
8. Release lock last.
9. Apply the same ordering to both result-manifest target kinds.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_collect_publish.py -q -p no:cacheprovider -k "publish_sequence or pending or committed or lock_held"
```

Checkpoint: happy-path transaction.

## Task 5: Implement the Deterministic Recovery State Table

Translate every authoritative P/C/empty/X row into one parameterized test ID:

- `previous_previous_candidates_complete`;
- `candidate_previous_manifest_candidate_complete`;
- `candidate_candidate_manifest_verify_commit`;
- `previous_candidate_manifest_impossible_fatal`;
- `external_data_x_fatal`;
- `external_manifest_x_fatal`;
- `missing_required_candidate_before_data_replace_fatal`;
- `missing_manifest_candidate_after_data_replace_fatal`;
- first-publication variants using empty final output and null backup.

Add tests:

- `test_pending_recovery_state_table()` parameterized by every row;
- `test_nonfirst_pending_requires_valid_backup()`;
- `test_recovery_never_guesses_rollback_or_continue()`;
- `test_fatal_recovery_preserves_journal_and_backup()`;
- `test_recovery_state_table_is_identical_for_both_manifest_targets()`;
- `test_recovery_rejects_changed_manifest_target_kind_or_path()`.

Implementation requirements:

1. Compare observed hashes to journal P/C values exactly.
2. Use file absence as empty only for journal-declared first publication.
3. Require valid backup for every nonfirst pending transaction.
4. Perform the single action specified by the matching row.
5. Treat unmatched/impossible/external-modification states as fatal.
6. Preserve evidence on fatal.
7. Verify the observed result-manifest path and target kind before applying a
   state-table row.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_collect_publish.py -q -p no:cacheprovider -k "recovery or state_table or nonfirst_pending"
```

Checkpoint: pending recovery state machine.

## Task 6: Cover Committed Residual, Manifest-only Publish, and Failures

Add tests:

- `test_committed_journal_with_matching_files_is_removed()`;
- `test_committed_journal_hash_or_transaction_mismatch_is_fatal()`;
- `test_manifest_only_publish_is_atomic_under_same_lock()`;
- `test_compatibility_manifest_only_publish_does_not_create_stage_manifest()`;
- `test_manifest_only_failure_preserves_old_manifest_and_data()`;
- `test_failure_before_pending_cleans_unneeded_candidates()`;
- `test_failure_after_pending_preserves_recovery_evidence()`;
- parameterized failures for candidate write/read, backup, journal write, data
  replace, data verify, manifest replace, manifest verify, committed update, and
  unlink boundary.

Implementation requirements:

1. Apply the exact committed-residual rule from P2-3.
2. Provide a manifest-only operation for no-data results under the same lock.
3. Permit compatibility-result manifest-only publication without creating or
   replacing a legacy stage manifest.
4. Distinguish safe pre-pending cleanup from post-pending evidence preservation.
5. Never report success while a fatal mismatch remains.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_collect_publish.py tests/test_file_lock.py -q -p no:cacheprovider
```

Full checkpoint:

```powershell
& $python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

## Acceptance Traceability

| P2-3 engine requirement | Evidence |
| --- | --- |
| OS-level single writer | Task 1 |
| double candidates before journal | Task 2 |
| extxyz semantic reread | Task 2 |
| closed current/compatibility targets | Task 2 |
| legacy stage manifest remains untouched | Tasks 2 and 6 |
| old output remains in place during backup | Task 3 |
| backup hash/atomic name | Task 3 |
| fixed publish sequence | Task 4 |
| journal binds target kind/path | Tasks 4 and 5 |
| first publication null fields | Tasks 3 through 5 |
| deterministic P/C/empty/X recovery | Task 5 |
| committed journal residual | Task 6 |
| manifest-only atomic operation | Task 6 |
| failure evidence/cleanup boundary | Task 6 |

## Plan Checkpoint

Plan 09 is complete when lock, candidate, backup, closed result-manifest targets,
transaction, recovery, committed residual, manifest-only, fault-injection, and
full suites pass. It must not be described as user-visible collect safety until
Plan 10 chooses the target from a validated input layout and wires the real
orchestration/CLI contract.
