# Plan 02: Manifest v2 and Atomic Persistence

Authoritative specs:

- [P1-5 Stage provenance](../../../code-review-notes/P1-5-stage-provenance.md)
- [P2-2 Stage rebuild contract](../../../code-review-notes/P2-2-stage-rebuild-contract.md)
- [P2-3 Collection output safety](../../../code-review-notes/P2-3-collection-output-safety.md)
- [P2-6 Collect exit codes](../../../code-review-notes/P2-6-collect-exit-codes.md)

Depends on: Plan 00

Unlocks: Plans 04, 05, 08, and 09

## Goal and Done State

Create one strict, versioned manifest contract and one reusable atomic file-write
convention before build provenance and collect transactions add their data.

Done means:

- every newly written stage manifest declares Manifest v2;
- reading distinguishes current, legacy, missing, and invalid manifests;
- a missing/invalid manifest is never silently converted into a new manifest;
- manifest paths and declared stage directories cannot escape `work_dir`;
- manifest publication uses a same-directory candidate and atomic replacement;
- failures before replacement leave the previous manifest byte-for-byte intact;
- the schema has explicit locations for provenance, anchors, seed identity, source
  diagnostics, and collect transaction metadata.

## Non-goals

- Do not populate full structure provenance; Plan 05 owns that producer/consumer.
- Do not implement seed parsing; Plan 03 owns the scientific identity.
- Do not implement the P2-3 lock/journal state machine; Plan 09 owns it.
- Do not choose collect aggregate status or CLI exit codes.
- Do not auto-migrate or overwrite a legacy relaxation manifest.

## Files

Create:

- `src/dpmoire_lite/atomic_io.py`
- `tests/test_atomic_io.py`
- `tests/test_manifest_v2.py`

Modify:

- `src/dpmoire_lite/manifest.py`
- `src/dpmoire_lite/paths.py` if a reusable work-dir containment helper is needed
- `tests/test_paths_manifest.py`
- current manifest-construction tests in `tests/test_build.py` and
  `tests/test_collect.py`

## Required Contract

New manifests use `schema_version: 2` and preserve current common fields while
adding optional, validated sections:

- `structure_provenance`;
- `grid_shift_anchors`;
- `mlff_seed`;
- `partial` source diagnostics;
- `collect.status` and `collect.transaction_id`;
- collect data/previous hashes and backup identity.

The schema may use focused dataclasses or validated nested mappings. It must reject
unknown top-level data that would change interpretation, while allowing explicit
forward-compatible diagnostic mappings where the plan defines them.

The reader must provide a stable distinction among:

- missing file;
- legacy manifest: file exists but has no Manifest v2 schema;
- invalid current manifest: declares v2 but violates its schema;
- valid current manifest.

Exception names or result-wrapper names may be refined during implementation,
but callers cannot receive `None` for all three failure categories.

## Amendment for the Pre-2026-07-12 Worktree

The earlier plan added seven Manifest v2 RED tests before an unrelated atomic-I/O
task. That made a per-task full-green checkpoint impossible. No Plan 02 files had
been committed, so this plan is reordered without rewriting history:

- former Task 2 becomes amended Task 1;
- the five strict-reader/schema tests belong to amended Task 2;
- the two path-escape tests belong to amended Task 3;
- former Task 5 becomes amended Task 4.

If the pre-amendment untracked `tests/test_manifest_v2.py` is present during
amended Task 1, it is test inventory only and must not be staged. Run the
committed-scope regression once with:

```powershell
& $python -m pytest --ignore=tests/test_manifest_v2.py -q -p no:cacheprovider
```

Before amended Task 2 starts, keep only its five tests in that file. Add the path
tests only when amended Task 3 starts. Do not mark tests xfail/skip to hide this
ordering issue.

## Task 1: Implement and Test Reusable Atomic I/O Primitives

Add failing tests:

- `test_atomic_text_publish_replaces_only_after_candidate_fsync()`;
- `test_atomic_publish_failure_preserves_previous_bytes()`;
- `test_atomic_publish_uses_same_directory_candidate()`;
- `test_sha256_file_matches_known_bytes()`;
- `test_atomic_publish_removes_its_candidate_on_pre_replace_failure()`;
- `test_atomic_publish_does_not_swallow_replace_failure()`.

Use monkeypatch/fake operations to record ordering. Do not assume POSIX-only file
semantics.

Implementation requirements for `atomic_io.py`:

1. Create unique candidates in the destination directory.
2. Write with explicit encoding or bytes mode.
3. Flush and fsync the candidate before replacement.
4. Compute hash from finalized candidate bytes when requested.
5. Use `os.replace()` for final publication.
6. Attempt directory durability only through a small platform-aware helper; lack
   of a supported directory-fsync operation on Windows must be explicit and must
   not pretend success for file fsync that did not occur.
7. Clean only candidates created by the current operation and only before they
   become evidence required by a later transaction.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_atomic_io.py -q -p no:cacheprovider
```

Checkpoint requirements:

1. Atomic focused tests pass.
2. The one-time committed-scope regression above passes.
3. Stage only `src/dpmoire_lite/atomic_io.py` and
   `tests/test_atomic_io.py`.
4. Confirm `tests/test_manifest_v2.py` and the local status file are not staged.

Checkpoint: atomic primitives only.

## Task 2: Implement Manifest v2 and Strict Classification

At the start of this task, add/retain only these five tests:

- `test_read_manifest_reports_missing_separately()`;
- `test_read_manifest_detects_legacy_without_mutating_it()`;
- `test_read_manifest_rejects_invalid_v2_schema()`;
- `test_manifest_v2_round_trip_preserves_extension_sections()`;
- `test_read_manifest_never_creates_a_file()`.

Use synthetic YAML under `tmp_path`; do not depend on existing work directories.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_manifest_v2.py -q -p no:cacheprovider
```

Expected failure: the current reader returns `None` only for missing and has no
schema/legacy classification or nested contract.

Implementation requirements:

1. Define the v2 common fields and optional extension sections.
2. Parse YAML with strict mapping/type validation and field-context errors.
3. Detect legacy only when a manifest file exists and lacks the v2 schema marker.
4. Reject an unsupported explicit schema version; do not treat it as legacy.
5. Keep legacy source data available to Plan 05 without synthesizing missing
   provenance.
6. Ensure `read_manifest()` or its replacement never writes.
7. Make new-manifest constructors set v2 explicitly.

Green command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_manifest_v2.py tests/test_paths_manifest.py -q -p no:cacheprovider
```

Run the full suite; it must be green before this task is committed.

Checkpoint: schema/reader implementation.

## Task 3: Enforce Work-dir Path Boundaries

Add the path tests only now:

- `test_manifest_v2_rejects_stage_path_escape()`;
- `test_manifest_v2_rejects_absolute_directory_outside_workdir()`;
- `test_manifest_directory_accepts_normalized_relative_path()`;
- `test_manifest_directory_rejects_parent_traversal()`;
- `test_manifest_directory_rejects_symlink_escape_when_resolvable()`;
- `test_manifest_output_path_must_match_stage_contract()`;
- `test_manifest_stage_must_match_manifest_location()`.

Implementation requirements:

1. Normalize persisted paths to POSIX relative strings.
2. Validate resolved paths stay inside `work_dir` before consumers use them.
3. Reject `..` escape and absolute external paths.
4. Keep path validation separate from checking whether a calculation directory
   currently exists.
5. Produce diagnostics containing the manifest path and offending field.

Verification:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_manifest_v2.py tests/test_paths_manifest.py -q -p no:cacheprovider -k "path or directory or stage"
```

Run the full suite; it must be green before this task is committed.

Checkpoint: strict path-boundary support.

## Task 4: Make Manifest Writes Atomic Without Changing Domain Semantics

Add/adjust tests:

- `test_write_manifest_uses_atomic_publisher()`;
- `test_write_manifest_failure_preserves_previous_manifest()`;
- existing build/collect manifest round-trip tests expect `schema_version: 2`;
- `test_legacy_read_does_not_rewrite_source_manifest()`.

Implementation:

1. Serialize to deterministic YAML bytes before publication.
2. Publish through `atomic_io.py`.
3. Do not add a fallback `or Manifest(...)` at any caller.
4. Keep existing domain values intact; later plans populate new sections.
5. Update tests/fixtures that construct manifests directly.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_atomic_io.py tests/test_manifest_v2.py tests/test_paths_manifest.py tests/test_build.py tests/test_collect.py -q -p no:cacheprovider
```

Full checkpoint:

```powershell
& $python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

## Acceptance Traceability

| Shared requirement | Evidence |
| --- | --- |
| explicit schema/version | Task 2 v2 round-trip and unsupported-version tests |
| missing is not legacy | Task 2 strict classification tests |
| read never creates manifest | read-only test |
| invalid v2 fails strictly | schema-context tests |
| paths stay in work_dir | Task 3 tests |
| manifest write is atomic | Tasks 1 and 4 atomic ordering/failure tests |
| old bytes survive failed write | failure-injection tests |
| extension points exist once | v2 round-trip test |
| legacy source not rewritten | legacy immutability test |

## Plan Checkpoint

Plan 02 is complete when strict manifest and atomic-I/O suites plus the full suite
pass. Full provenance, publication journal, and CLI status remain open and must
not be claimed complete here.
