# Ticket 01: Portable Paired VASP Fixtures

Depends on: Ticket 00

Unlocks: Ticket 02

## Goal

Create the smallest sanitized, redistributable fixture pairs that preserve the
approved VASP 6.4.1 and 6.5.1 read/rewrite differences without adding verifier
behavior tests prematurely.

The ticket ends green on fixture inventory, parser compatibility, and private
data audits. It leaves no future-ticket RED in default discovery.

## Files

Read first:

- `tests/AGENTS.md`
- the ignored evidence records named by Ticket 00

Modify:

- `tests/data/mlab/README.md`
- `tests/test_mlab.py`

Add minimal files under `tests/data/mlab/`, tentatively:

- `seed_input_vasp_641.mlab`
- `seed_rewrite_vasp_641.mlab`
- `seed_input_vasp_651.mlab`
- `seed_rewrite_vasp_651.mlab`
- `seed_rewrite_postseed_vasp_651.mlab`

Names may be refined for existing fixture conventions, but every file must have
one documented purpose and an owning test.

## Tests

Add:

- `test_seed_rewrite_fixture_inventory_records_paired_provenance()`;
- `test_seed_rewrite_fixtures_parse_complete_canonical_fields()`;
- `test_seed_rewrite_pairs_have_exact_structure_and_bounded_numeric_deltas()`;
- `test_seed_rewrite_postseed_fixture_contains_one_distinct_new_configuration()`;
- `test_seed_rewrite_fixtures_contain_no_private_or_potcar_markers()`.

The delta test computes evidence only. It must not import a production
equivalence verifier that Ticket 02 has not implemented.

## RED

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
& $python -c "import sys; print(sys.executable)"
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_mlab.py -q -p no:cacheprovider `
  -k "seed_rewrite_fixture or paired_provenance"
```

Expected RED: the named sanitized paired fixtures and inventory records do not
exist. The failure must not be a private-path skip.

## GREEN

1. Crop only the header/type/count and minimum complete configuration blocks.
2. Preserve the numeric values required to reproduce the approved deltas.
3. Renumber/correct headers only when required for a valid standalone crop;
   document every constructed change.
4. Remove usernames, hostnames, jobs, accounts, absolute paths, unrelated
   configurations, and non-source artifacts.
5. Record source type, VASP version proof, crop relationship, expected count,
   units, max deltas, removed private fields, and redistribution confirmation.
6. Confirm no POTCAR, potential, ML_FF/ML_FFN, WAVECAR, CHGCAR, or complete
   private calculation output is present.

Run the RED command to green.

## Affected and Full Verification

```powershell
& $python -m pytest tests/test_mlab.py -q -p no:cacheprovider
& $python -m pytest -q -p no:cacheprovider
git ls-files | Select-String -Pattern '(^|[/\\])POTCAR$|example-test'
Get-ChildItem -Path 'tests','dist' -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -eq 'POTCAR' -or $_.FullName -match 'example-test' }
git diff --check
git status --short
```

Expected: tests pass; both privacy audits return no tracked/packaged private
source; only the named tests, inventory, and minimal fixtures change.

## Non-goals

- Do not add `SeedPrefixVerifier` or source-collection behavior.
- Do not modify canonical identity schemas.
- Do not copy raw databases or use ignored files at test runtime.

## Checkpoint

Portable paired fixtures and provenance tests are green. Stop before Ticket 02.
