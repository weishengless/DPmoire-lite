# Ticket 04: Seed-aware Source Integration

Depends on: Ticket 03

Unlocks: Ticket 05

## Goal

Replace the direct exact-digest branch in seed-aware source collection with one
verifier per collection candidate, while preserving source-local ownership,
partial handling, frame counts, and full-dedup independence.

## Files

Read first:

- `src/dpmoire_lite/AGENTS.md`
- `tests/AGENTS.md`

Modify:

- `src/dpmoire_lite/collect.py`
- `src/dpmoire_lite/collect_models.py`
- `src/dpmoire_lite/mlff_seed.py` only for integration defects proven by the RED
- `tests/test_source_collection.py`

Potentially modify:

- `tests/conftest.py` only for reusable sanitized-fixture helpers

Do not modify publication or CLI modules.

## Tests

Add:

- `test_current_vasp_rewritten_prefix_collects_post_seed_frames()`;
- `test_legacy_vasp_rewritten_prefix_collects_post_seed_frames()`;
- `test_collection_candidate_reuses_one_verifier_for_all_sources()`;
- `test_exact_and_vasp_equivalent_sources_skip_same_seed_count()`;
- `test_equivalent_tail_partial_keeps_complete_post_seed_frames()`;
- `test_seed_mismatch_source_contributes_zero_frames()`;
- `test_reference_failure_is_source_local_and_reused()`;
- `test_early_md_collection_keeps_missing_sources_skipped()`;
- `test_full_dedup_never_calls_seed_equivalence_verifier()`;
- `test_source_result_exposes_structured_seed_verification()`.

## RED

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
& $python -c "import sys; print(sys.executable)"
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_source_collection.py -q -p no:cacheprovider `
  -k "vasp_rewritten or vasp_equivalent or seed_verification or reuses_one_verifier"
```

Expected RED: current source collection rejects the sanitized VASP-rewritten
prefix as an exact digest mismatch.

## GREEN

1. Build one current or legacy verifier per candidate, not per source.
2. Pass each finalized parsed ML_ABN payload through that verifier.
3. Map `exact` and `vasp_equivalent` to accepted prefix removal of exactly the
   recorded count.
4. Preserve original source status: complete remains complete; accepted
   tail-partial remains partial.
5. Map `mismatch`/reference failure to failed with empty accepted and parsed
   payloads.
6. Add immutable seed-verification evidence to `SourceResult` and its diagnostic
   mapping.
7. Keep accepted-frame equality and source-count invariants enforced by
   `CollectionCandidate`.
8. Leave `collect_full_dedup_mlab_sources()` and exact fold behavior unchanged.

Run the focused command to green.

## Affected and Full Verification

```powershell
& $python -m pytest tests/test_mlff_seed.py tests/test_source_collection.py `
  tests/test_mlff_full_dedup.py tests/test_collect.py -q -p no:cacheprovider
& $python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

## Stop Conditions

Stop if integration requires changing the numeric rule, canonical identity,
full-dedup, aggregate status definitions, or publication behavior.

## Checkpoint

In-memory seed-aware candidates accept VASP-equivalent prefixes with exact frame
and source accounting. Stop before publication/CLI integration.
