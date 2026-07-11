# Plan 08: Per-source Collection Semantics

Authoritative specs:

- [P2-1 Partial collection and MLFF seed](../../../code-review-notes/P2-1-partial-collection-and-mlff-seed.md)
- [P2-4 OUTCAR selection](../../../code-review-notes/P2-4-outcar-pattern-validation.md)
- [P2-5 OUTCAR streaming](../../../code-review-notes/P2-5-outcar-streaming.md)
- [P2-6 source-status mapping](../../../code-review-notes/P2-6-collect-exit-codes.md)

Consumes:

- [Plan 02 Manifest v2](02-manifest-v2.md)
- [Plan 03 ML_AB parser](03-mlab-parser-digest.md)
- [Plan 07 OUTCAR ingestion](07-outcar-ingestion.md)

Unlocks: Plan 10

## Goal and Done State

Turn every declared ML_ABN or OUTCAR input into a structured, internally
consistent source result before it contributes to a shared candidate dataset.

Done means:

- each expected source is `complete`, `partial`, `skipped`, or `failed` with
  stable diagnostics;
- complete and accepted tail-partial sources contribute exactly their accepted
  frames;
- failed sources contribute zero frames;
- one failed source cannot leave already-appended frames in a shared Dataset;
- new-manifest MLFF sources verify the immutable initial seed prefix before
  skipping it;
- restart growth in current `md/ML_AB` does not change the initial skip count;
- legacy seed evidence comes only from a complete `init_mlff/ML_ABN`;
- selected OUTCAR path/pattern/order and per-file sampling are preserved;
- an in-memory collection candidate reports counts exactly matching its frames.

## Non-goals

- Do not write, replace, delete, or back up final extxyz output.
- Do not choose the final aggregate CLI exit code.
- Do not create a stage manifest when it is missing.
- Do not perform fuzzy deduplication across sources.
- Do not collect multiple historical ML_ABN files by OUTCAR-style patterns.

## Files

Create:

- `src/dpmoire_lite/collect_models.py`
- `tests/test_source_collection.py`

Modify:

- `src/dpmoire_lite/collect.py`
- `src/dpmoire_lite/dataset.py`
- `src/dpmoire_lite/mlab.py` only for missing source-level diagnostics exposed by
  its existing parser contract
- `src/dpmoire_lite/outcar.py` only for missing error-position metadata required
  by source classification
- `tests/test_collect.py`
- `tests/data/mlab/README.md` and `tests/data/outcar/README.md` when a new
  corruption boundary fixture is required

## Result Contract

`SourceResult` must carry at least:

- declared relative source path and source kind;
- status enum;
- accepted frames/configurations owned by this result until aggregation;
- complete/accepted count;
- discarded configuration/frame and block/location for partial input;
- reason and error location for skipped/failed input;
- OUTCAR selection pattern index/order when applicable;
- seed verification identity when applicable.

`CollectionCandidate` carries:

- the candidate Dataset or immutable accepted-frame sequence;
- every expected source result, including missing directories/files;
- attempted/complete/partial/skipped/failed counts;
- expected directory coverage;
- selected source order;
- total frames equal to the sum of source accepted counts.

Aggregate `CollectStatus` names may be defined in this shared model, but final
selection and CLI mapping remain Plan 10 responsibilities.

## Task 1: Define Source-result Ownership with Failing Tests

Add tests:

- `test_source_status_values_are_stable()`;
- `test_source_result_owns_frames_until_accepted()`;
- `test_failed_source_result_cannot_expose_accepted_frames()`;
- `test_collection_candidate_frame_count_equals_source_sum()`;
- `test_collection_candidate_preserves_declared_source_order()`;
- `test_source_diagnostics_use_workdir_relative_paths()`.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_source_collection.py -q -p no:cacheprovider -k "source_result or collection_candidate or status"
```

Expected failure: no structured result layer exists; current loaders mutate one
shared Dataset inside their parse loops.

Implementation requirements:

1. Use enums, not free-form status strings inside core code.
2. Make invalid combinations such as failed-with-frames impossible or validated.
3. Keep diagnostics YAML-safe and deterministic.
4. Convert to the shared Dataset only when accepting a complete/partial result.

Run the focused command to green.

Checkpoint: result types and invariants.

## Task 2: Implement ML_ABN Complete/Partial/Failed Sources

Add failing tests:

- `test_complete_mlab_source_contributes_all_new_configurations()`;
- `test_tail_partial_mlab_contributes_complete_prefix_and_metadata()`;
- `test_first_frame_truncated_mlab_is_failed_with_zero_frames()`;
- `test_internal_corruption_mlab_is_failed_with_zero_frames()`;
- `test_mlab_source_failure_does_not_mutate_existing_candidate()`;
- `test_mlab_source_ase_properties_match_parsed_values()`.

Implementation:

1. Consume the Plan 03 parse result.
2. Hold parsed configurations locally until status is known.
3. Convert complete and permitted partial configurations to ASE frames.
4. Convert parser failures into structured failed results with zero frames.
5. Preserve partial block/configuration details.
6. Do not catch programming or manifest invariant errors as ordinary source
   failures.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_source_collection.py tests/test_mlab.py -q -p no:cacheprovider -k "mlab_source"
```

Checkpoint: raw ML_ABN source classification.

## Task 3: Verify Current-manifest Initial Seed Prefix

Add failing tests:

- `test_seed_prefix_match_skips_exact_initial_count()`;
- `test_seed_prefix_mismatch_fails_source_with_zero_new_frames()`;
- `test_seed_prefix_shorter_than_initial_count_fails()`;
- `test_seed_prefix_same_count_different_content_fails()`;
- `test_current_md_ml_ab_hash_change_is_ignored_for_restart()`;
- `test_multiple_restart_growth_still_skips_only_initial_seed()`;
- `test_seed_only_source_is_complete_with_zero_new_frames()`;
- `test_new_data_tail_partial_preserves_complete_new_frames()`.

Use synthetic Manifest v2 input matching the Plan 06 producer contract, so this
consumer can be tested before real Stage1 integration.

Implementation requirements:

1. Read initial count, schema, and prefix digest from the MD manifest.
2. Parse at least the first N complete configurations of final ML_ABN.
3. Recompute the exact Plan 03 canonical digest.
4. Reject mismatch/short prefix before exposing any new frames.
5. Ignore current `md/ML_AB` for initial identity.
6. After verification, expose all complete configurations after N, including data
   accumulated through earlier restarts.
7. Apply Plan 03 tail-partial semantics to the new-data tail.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_source_collection.py -q -p no:cacheprovider -k "seed_prefix or restart or seed_only"
```

Checkpoint: current-manifest seed consumer.

## Task 4: Implement Strict Legacy Seed Evidence

Add failing tests:

- `test_legacy_seed_identity_rebuilt_from_complete_init_mlff_mlabn()`;
- `test_legacy_seed_rebuild_warns_and_verifies_final_prefix()`;
- `test_legacy_missing_init_seed_fails_without_using_md_ml_ab()`;
- `test_legacy_partial_or_invalid_init_seed_fails()`;
- `test_legacy_final_prefix_mismatch_fails_source()`.

Implementation requirements:

1. Enter legacy seed recovery only for a manifest classified legacy by Plan 02.
2. Require `work/init_mlff/ML_ABN` to exist and parse as complete.
3. Compute count/schema/digest through Plan 03.
4. Verify the final ML_ABN prefix identically to current-manifest behavior.
5. Emit one explicit legacy evidence warning.
6. Never fall back to possibly grown `md/ML_AB`.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_source_collection.py -q -p no:cacheprovider -k "legacy_seed"
```

Checkpoint: legacy seed consumer.

## Task 5: Classify Streamed OUTCAR Sources

Add failing tests:

- `test_complete_outcar_source_contributes_sampled_frames()`;
- `test_tail_truncated_outcar_keeps_prior_sampled_frames_as_partial()`;
- `test_outcar_first_frame_failure_contributes_zero_frames()`;
- `test_outcar_internal_corruption_does_not_salvage_prefix()`;
- `test_outcar_invalid_utf8_is_failed_not_partial()`;
- `test_each_segment_resets_sampling_and_has_independent_status()`;
- `test_outcar_source_records_pattern_index_and_order()`.

Create the minimum truncated/corrupt variants needed and add their provenance
records. Prefer deterministic cropping from the Plan 07 fixture; do not introduce
a full calculation OUTCAR.

Implementation requirements:

1. Consume frames inside the Plan 07 context manager.
2. Buffer only sampled accepted frames for the current source.
3. Use parser location and project-side tail evidence to distinguish a final
   incomplete ionic step from internal corruption.
4. Treat strict-decode errors as failed corruption.
5. If the first frame fails, return zero accepted frames.
6. Do not skip past internal corruption.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_source_collection.py tests/test_outcar.py -q -p no:cacheprovider -k "outcar_source or segment"
```

Checkpoint: OUTCAR source classification.

## Task 6: Build a Deterministic In-memory Collection Candidate

Add failing tests:

- `test_candidate_uses_manifest_directories_only()`;
- `test_missing_directory_and_file_are_skipped_with_reason()`;
- `test_candidate_preserves_outcar_selection_order()`;
- `test_candidate_counts_all_source_statuses()`;
- `test_candidate_frames_equal_complete_plus_partial_contributions()`;
- `test_failed_sources_contribute_zero_frames()`;
- `test_candidate_does_not_fuzzy_deduplicate_similar_frames()`;
- `test_candidate_creation_does_not_write_output_or_manifest()`.

Implementation:

1. Enumerate expected directories only from a strict current/legacy manifest.
2. Produce a SourceResult for every expected directory/source condition.
3. Aggregate accepted frames after each result is final.
4. Preserve deterministic source order.
5. Compute all summary counts from results rather than independently incremented
   counters.
6. Return an in-memory candidate and diagnostics without publication.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_source_collection.py tests/test_collect.py -q -p no:cacheprovider
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
| structured complete/partial/skipped/failed | Tasks 1, 2, and 5 |
| no partial shared mutation | Tasks 1 and 2 |
| exact seed-prefix verification | Task 3 |
| restart skips immutable initial count | Task 3 |
| legacy evidence only from init seed | Task 4 |
| OUTCAR tail versus internal corruption | Task 5 |
| deterministic selected order | Tasks 5 and 6 |
| source counts equal candidate frames | Tasks 1 and 6 |
| no fuzzy deduplication | Task 6 |
| no output/manifest write | Task 6 |

## Plan Checkpoint

Plan 08 is complete when ML_ABN, seed, OUTCAR, candidate, affected collect, and
full suites pass. The resulting candidate is not safely published and has no CLI
status until Plans 09 and 10.
