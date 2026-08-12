# Plan 08A: MLFF Full-dedup and Legacy Inventory

Authoritative specs:

- [P2-1 Partial collection, seed-aware default, and full-dedup](../../../code-review-notes/P2-1-partial-collection-and-mlff-seed.md)
- [P2-6 Collect result and legacy-scan status](../../../code-review-notes/P2-6-collect-exit-codes.md)
- [Approved full-dedup design](../../specs/2026-07-12-mlff-full-dedup-legacy-collection-design.md)

Consumes:

- [Plan 02 Manifest v2 classification](02-manifest-v2.md)
- [Plan 03 ML_AB parser and exact identity](03-mlab-parser-digest.md)
- [Plan 08 Per-source collection semantics](08-source-collection.md)

Unlocks: Plan 10

## Goal and Done State

Add one bounded optional MLFF candidate-building path that collects all complete
configurations from each selected final ML_ABN and removes only exact canonical
duplicates. Also provide the safe legacy/missing-manifest inventory needed to use
that path with older DPmoire-lite calculation trees.

Done means:

- the core exposes stable `seed-aware` and `full-dedup` mode values, with
  `seed-aware` as the default;
- full-dedup consumes Plan 08 accepted parsed ML_ABN payloads before ASE
  conversion;
- one `mlab-config-v1` identity is computed per accepted complete
  configuration;
- deterministic first occurrence wins within and across sources;
- duplicates never reach ASE conversion and do not change source status;
- failed sources cannot reserve global identities or contribute frames;
- current and legacy manifests use their validated declared directory order;
- a missing manifest is accepted only in explicit full-dedup and scans one safe
  MD directory level;
- invalid/unsupported/escaping manifests never fall back to scanning;
- the candidate reports exact seen/unique/duplicate counts and whether source
  coverage is known;
- candidate creation performs no output, result-manifest, or journal write.

## Non-goals

- Do not add the CLI option; Plan 10 owns the user boundary.
- Do not publish `MD_data.extxyz` or `MD_data.collect.yaml`; Plans 09 and 10 own
  publication.
- Do not modify Stage0/Stage1 generation or default seed distribution.
- Do not infer mode from `ML_ISTART`, `ML_MODE`, OUTCAR, or current `md/ML_AB`.
- Do not collect `ML_AB`, `ML_ABN0`, `ML_ABN1`, backups, or nested restart
  directories.
- Do not accept old config keys.
- Do not implement fuzzy, tolerance, RMSD, symmetry, or pairwise deduplication.
- Do not add a disk-backed identity index or streaming extxyz writer.

## Files

Create:

- `src/dpmoire_lite/mlff_collect.py`
- `tests/test_mlff_full_dedup.py`

Modify:

- `src/dpmoire_lite/collect_models.py`
- `src/dpmoire_lite/collect.py` only for a private candidate-builder seam; the
  public CLI/default call remains unchanged until Plan 10
- `tests/test_source_collection.py`
- `tests/test_collect.py` only for internal candidate behavior

Consume without modifying unless a contract defect is proven:

- `src/dpmoire_lite/mlab.py`
- `src/dpmoire_lite/manifest.py`

## Shared Contracts

`MLFFCollectMode` has exactly:

- `seed-aware`;
- `full-dedup`.

The internal default is `seed-aware`. Plan 10 validates whether a mode is legal
for the requested stage/source kind.

`SourceInventory` carries at least:

- input manifest kind: current, legacy, or missing;
- directory discovery method: declared or legacy-scan;
- normalized ordered relative source directories;
- `coverage_known`;
- deterministic warnings/diagnostics.

`DedupStats` carries at least:

- seen complete configurations;
- unique retained configurations;
- duplicates removed;
- per-source seen/retained/duplicate counts;
- schema name `mlab-config-v1`.

It validates:

```text
seen == unique + duplicates_removed
unique == candidate frame count
sum(per-source fields) == aggregate fields
```

## Task 1: Define Mode, Inventory, and Dedup-result Invariants

Add failing tests:

- `test_mlff_collect_mode_values_are_stable()`;
- `test_seed_aware_is_the_core_default()`;
- `test_source_inventory_requires_normalized_ordered_relative_paths()`;
- `test_source_inventory_marks_declared_coverage_known()`;
- `test_dedup_stats_require_seen_equal_unique_plus_duplicates()`;
- `test_dedup_stats_require_unique_equal_candidate_frames()`;
- `test_dedup_schema_is_mlab_config_v1()`.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_mlff_full_dedup.py -q -p no:cacheprovider -k "mode or inventory or stats or schema"
```

Expected failure: Plan 08 has source/aggregate results but no optional MLFF mode,
inventory evidence, or exact-dedup counters.

Implementation requirements:

1. Use enums/dataclasses or equally strict immutable validated models.
2. Keep mode values separate from VASP INCAR vocabulary.
3. Reject negative or internally inconsistent counts at construction.
4. Store only work-dir-relative inventory paths.
5. Do not add CLI parsing or filesystem scanning in this task.

Run the focused command to green.

Checkpoint: core contracts only.

## Task 2: Implement the Exact Canonical Identity Fold

Add failing tests:

- `test_full_dedup_removes_identical_configurations_within_one_source()`;
- `test_full_dedup_removes_identical_configurations_across_sources()`;
- `test_full_dedup_keeps_deterministic_first_occurrence_and_provenance()`;
- `test_full_dedup_equivalent_float_text_uses_same_identity()`;
- `test_full_dedup_keeps_changed_element_cell_position_energy_force_or_stress()`;
- `test_full_dedup_normalizes_negative_zero()`;
- `test_duplicate_is_not_converted_to_ase()`;
- `test_identity_called_once_per_accepted_complete_configuration()`.

Focused command:

```powershell
& $python -m pytest tests/test_mlff_full_dedup.py tests/test_mlab.py -q -p no:cacheprovider -k "dedup or identity or first_occurrence"
```

Implementation requirements:

1. Consume Plan 03 parsed configurations and `mlab-config-v1` identity; do not
   reserialize scientific fields in this module.
2. Fold sources in supplied order and configurations in file order.
3. Use one digest set/map lookup per configuration.
4. Convert only first-seen configurations to ASE through the Plan 08 adapter.
5. Retain the first source/configuration provenance.
6. Perform no tolerance rounding or geometry-only comparison.
7. Compute counts from fold decisions rather than independent increments.

Run the focused command to green.

Checkpoint: pure exact fold without source discovery.

## Task 3: Implement Bounded Current/Legacy/Missing Inventory

Add failing tests:

- `test_current_manifest_inventory_uses_declared_order()`;
- `test_legacy_manifest_inventory_uses_validated_declared_order()`;
- `test_missing_manifest_is_rejected_for_seed_aware()`;
- `test_missing_manifest_full_dedup_scans_direct_md_children()`;
- `test_legacy_scan_uses_natural_order()`;
- `test_legacy_scan_accepts_named_monolayer_directory()`;
- `test_legacy_scan_requires_exact_mlabn_filename()`;
- `test_legacy_scan_ignores_files_backups_and_nested_descendants()`;
- `test_legacy_scan_rejects_resolved_path_escape()`;
- `test_invalid_or_unsupported_manifest_never_falls_back_to_scan()`;
- `test_missing_scan_marks_coverage_unknown_and_records_warning()`.

Use only `tmp_path` directories and synthetic manifest YAML. Do not copy a real
calculation tree.

Focused command:

```powershell
& $python -m pytest tests/test_mlff_full_dedup.py tests/test_manifest_v2.py -q -p no:cacheprovider -k "inventory or legacy_scan or missing_manifest or fallback"
```

Implementation requirements:

1. Consume Plan 02's strict current/legacy/missing distinction.
2. Allow scan only when the result is exactly missing and mode is exactly
   full-dedup.
3. Reuse work-dir path-boundary validation for declared and discovered paths.
4. Inspect only direct children of `work_dir/md`.
5. Select a directory only when `child/ML_ABN` is a regular in-boundary file.
6. Natural-sort selected relative paths deterministically.
7. Mark declared inventory coverage known and scan coverage unknown.
8. Perform no write or migration.

Run the focused command to green.

Checkpoint: read-only inventory adapter.

## Task 4: Integrate Source Classification with Full-dedup

Add failing tests:

- `test_full_dedup_complete_source_commits_unique_configurations()`;
- `test_full_dedup_partial_source_commits_only_complete_prefix()`;
- `test_full_dedup_failed_source_commits_no_frames_or_identities()`;
- `test_failed_source_identity_does_not_hide_later_valid_frame()`;
- `test_full_dedup_common_seed_is_retained_once()`;
- `test_full_dedup_fresh_start_retains_every_unique_final_configuration()`;
- `test_restart_time_ml_ab_count_is_never_used_by_full_dedup()`;
- `test_full_dedup_reads_only_final_mlabn()`;
- `test_distinct_seed_configurations_are_retained()`.

Use synthetic parsed configurations and minimum portable fixtures. The restart
regression represents an initial external seed count of zero, a grown current
ML_AB count, and a larger final ML_ABN; the test must not require the local raw
sample.

Focused command:

```powershell
& $python -m pytest tests/test_mlff_full_dedup.py tests/test_source_collection.py -q -p no:cacheprovider -k "full_dedup and (source or partial or failed or seed or restart or final)"
```

Implementation requirements:

1. Finalize each Plan 08 source result before mutating the global fold.
2. Commit complete sources and the accepted complete prefix of partial sources.
3. Discard every local identity/configuration from a failed source.
4. Never read current ML_AB count or VASP mode tags.
5. Do not let duplicate removal change source complete/partial status.
6. Preserve Plan 08 source diagnostics and add fold counts separately.

Run the focused command to green.

Checkpoint: source-safe full-dedup candidate path.

## Task 5: Build the Deterministic Auditable Candidate

Add failing tests:

- `test_full_dedup_candidate_preserves_inventory_order()`;
- `test_full_dedup_candidate_counts_match_source_sums()`;
- `test_full_dedup_candidate_frames_equal_unique_count()`;
- `test_duplicates_alone_do_not_mark_source_or_candidate_degraded()`;
- `test_missing_scan_candidate_preserves_coverage_unknown()`;
- `test_candidate_records_input_layout_discovery_mode_and_schema()`;
- `test_candidate_creation_writes_no_output_manifest_or_journal()`;
- `test_candidate_cost_is_one_identity_per_complete_configuration()`.

Implementation requirements:

1. Return a Plan 08-compatible in-memory `CollectionCandidate` plus inventory and
   dedup diagnostics.
2. Derive every aggregate count from finalized source/fold results.
3. Preserve `coverage_known` for Plan 10 status selection.
4. Do not choose the final CLI integer or publish any artifact.
5. Do not add wall-clock benchmark assertions; test the linear operation count.

Focused command:

```powershell
& $python -m pytest tests/test_mlff_full_dedup.py tests/test_source_collection.py tests/test_collect.py -q -p no:cacheprovider
```

Full checkpoint:

```powershell
& $python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

## Acceptance Traceability

| Requirement | Evidence |
| --- | --- |
| stable mode/default | Task 1 |
| exact canonical first-seen dedup | Task 2 |
| no duplicate ASE conversion | Task 2 |
| no pairwise/fuzzy comparison | Tasks 2 and 5 |
| current/legacy declared inventory | Task 3 |
| bounded missing-manifest scan | Task 3 |
| invalid manifest never scans | Task 3 |
| failed source cannot pollute identities | Task 4 |
| fresh restart loses no earlier frames | Task 4 |
| repeated seed retained once | Task 4 |
| counts and provenance are auditable | Task 5 |
| coverage-known signal for exit status | Tasks 3 and 5 |
| no publication side effects | Task 5 |

## Plan Checkpoint

Plan 08A is complete when mode, exact fold, compatibility inventory,
source-safety, candidate, affected collection, and full suites pass. The feature
is not user-visible until Plan 10 wires CLI validation and Plan 09's alternate
result-manifest path is integrated. Fuzzy deduplication, old config aliases, and
historical restart-file collection remain out of scope.
