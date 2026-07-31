# Ticket 03: Current and Legacy Reference Adapters

Depends on: Ticket 02

Unlocks: Ticket 04

## Goal

Add the two real adapters at the seed-verification seam: strict Manifest v2
producer evidence and existing legacy seed recovery. Preserve the exact fast
path's independence from the original reference file while making non-exact
fallback lazy, cached, path-contained, and fail-closed.

## Files

Read first:

- `src/dpmoire_lite/AGENTS.md`
- `tests/AGENTS.md`

Modify:

- `src/dpmoire_lite/mlff_seed.py`
- `tests/test_mlff_seed.py`

Modify only if typed evidence extraction cannot remain local:

- `src/dpmoire_lite/manifest.py`
- `tests/test_manifest.py`

Do not integrate `collect.py` in this ticket.

## Tests

Add:

- `test_current_adapter_exact_match_does_not_read_reference()`;
- `test_current_adapter_mismatch_loads_reference_once_across_sources()`;
- `test_current_adapter_requires_contained_source_path()`;
- `test_current_adapter_requires_recorded_raw_sha256()`;
- `test_current_adapter_requires_complete_reference_count_and_exact_identity()`;
- `test_current_adapter_returns_structured_reference_failure()`;
- `test_legacy_adapter_uses_only_complete_init_mlff_mlabn()`;
- `test_legacy_adapter_records_legacy_rebuilt_trust()`;
- `test_legacy_adapter_missing_or_partial_reference_fails_closed()`.

## RED

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
& $python -c "import sys; print(sys.executable)"
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_mlff_seed.py -q -p no:cacheprovider `
  -k "current_adapter or legacy_adapter or reference"
```

Expected RED: the pure verifier has no Manifest v2 or legacy reference adapter.

## GREEN

1. Add `SeedPrefixVerifier.from_current_manifest(work_dir, evidence)`.
2. Validate count, exact schema/digest, source path, and raw hash shape at
   construction without reading reference bytes.
3. On exact final-prefix identity, return immediately without touching the
   reference.
4. On first non-exact prefix, resolve the manifest source inside `work_dir`,
   validate raw SHA-256 against `ml_ab_sha256`, parse it completely, verify count
   and exact canonical identity, and cache the structured result.
5. Reuse the same cached reference or cached failure for later sources.
6. Add a legacy adapter that trusts only complete `init_mlff/ML_ABN`, records
   `legacy-rebuilt`, and preserves the existing warning contract.
7. Return structured verification/reference failures; do not publish, mutate a
   manifest, or choose aggregate status.

Run the focused command to green.

## Affected and Full Verification

```powershell
& $python -m pytest tests/test_mlff_seed.py tests/test_manifest.py `
  tests/test_mlab.py -q -p no:cacheprovider
& $python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

## Non-goals

- Do not read current `md/ML_AB`.
- Do not infer from final-source agreement or VASP tags.
- Do not change Manifest v2 top-level schema or rewrite legacy manifests.
- Do not add collection/publication code.

## Checkpoint

Both adapters are green and share one verifier interface. Stop before Ticket 04.
