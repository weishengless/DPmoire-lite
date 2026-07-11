# Plan 03: ML_AB Parser and Canonical Seed Digest

Authoritative spec:
[P2-1 Partial collection and MLFF seed](../../../code-review-notes/P2-1-partial-collection-and-mlff-seed.md)

Depends on: Plan 00

Unlocks: Plans 04, 06, and 08

## Goal and Done State

Replace regex-splitting and incremental Dataset mutation with a pure structured
ML_AB/ML_ABN parser that can prove complete versus tail-partial input and compute
the exact `mlab-seed-v1` scientific-content digest.

Done means:

- complete files parse into immutable structured configurations;
- the only accepted partial form is one started, incomplete final configuration
  with `declared_count == complete_count + 1`;
- first-frame truncation and internal corruption fail without accepted frames;
- header, type counts, array shapes, finite floats, units, and stress order are
  validated;
- equivalent scientific content with harmless text formatting changes hashes the
  same;
- changed scientific content hashes differently;
- Dataset compatibility code does not mutate the shared dataset until a source
  parse result is known.

## Non-goals

- Do not decide aggregate collect status; Plan 08 owns source orchestration.
- Do not publish extxyz; Plans 09 and 10 own publication.
- Do not inspect current `md/ML_AB` to infer an initial seed.
- Do not implement fuzzy structure deduplication.
- Do not convert digest stress through ASE sign/unit conventions.

## Files

Create:

- `src/dpmoire_lite/mlab.py`
- `tests/test_mlab.py`
- `tests/data/mlab/README.md`
- minimal complete and truncated ML_ABN fixtures under `tests/data/mlab/`

Modify:

- `src/dpmoire_lite/dataset.py`
- `tests/test_collect.py` only for compatibility behavior that is directly owned
  by the parser
- `tests/data/README.md`

## Structured Configuration Contract

Each parsed configuration must expose, before ASE conversion:

- ordered element symbols and per-type counts;
- atom count;
- 3x3 lattice in angstrom;
- Cartesian positions in angstrom;
- total energy in eV;
- forces in eV/angstrom;
- raw VASP stress in kbar ordered `[xx, yy, zz, xy, yz, zx]`;
- source configuration number and source location diagnostics.

The parse result distinguishes `complete` and `partial`. Invalid formats raise a
structured error carrying the source path, configuration number, block, and
reason. It must not return an ambiguous list plus warning text.

## Task 1: Add Sanitized Fixture Corpus and Provenance Records

Create the smallest fixtures needed for:

- one complete multi-configuration ML_ABN;
- truncation in the final position block;
- truncation in the final force block;
- truncation in the final stress block;
- first configuration incomplete;
- internal corruption followed by a later recognizable configuration;
- identical scientific content with different whitespace/number formatting.

For every file, `tests/data/mlab/README.md` records:

- whether it was synthetic or cropped;
- which blocks are retained;
- which private paths/account/cluster data were removed;
- that it contains no POTCAR or licensed potential content;
- the expected parse status and accepted configuration count;
- redistribution confirmation.

Add failing fixture tests:

- `test_mlab_fixture_inventory_has_provenance_entry()`;
- `test_mlab_fixtures_contain_no_private_path_or_potcar_marker()`.

Do not add a fixture until its exact test exists.

## Task 2: Implement Complete Structured Parsing

Add failing tests:

- `test_parse_complete_mlab_returns_declared_configurations()`;
- `test_parse_mlab_preserves_element_and_atom_order()`;
- `test_parse_mlab_returns_cartesian_positions()`;
- `test_parse_mlab_preserves_raw_kbar_stress_order()`;
- `test_parse_mlab_rejects_type_count_sum_mismatch()`;
- `test_parse_mlab_rejects_position_or_force_shape_mismatch()`;
- `test_parse_mlab_rejects_nan_and_infinity()`;
- `test_initial_seed_requires_header_count_equal_complete_count()`.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_mlab.py -q -p no:cacheprovider -k "complete or order or count or shape or nan or infinity"
```

Expected failure: the structured parser does not exist and current Dataset code
parses unvalidated slices directly.

Implementation requirements:

1. Decode strict UTF-8.
2. Parse in file order with block-aware locations.
3. Validate header and every configuration before returning a complete result.
4. Keep raw scientific data independent of ASE calculator conversion.
5. Never mutate an external Dataset during parsing.

Run the red command to green.

Checkpoint: complete parser and validation.

## Task 3: Implement Exact Tail-partial Classification

Add failing tests:

- `test_tail_position_truncation_accepts_complete_prefix()`;
- `test_tail_force_truncation_accepts_complete_prefix()`;
- `test_tail_stress_truncation_accepts_complete_prefix()`;
- `test_partial_records_discarded_configuration_and_missing_block()`;
- `test_first_configuration_incomplete_is_failure()`;
- `test_internal_corruption_does_not_salvage_prefix()`;
- `test_declared_count_greater_than_complete_plus_one_fails()`;
- `test_declared_equals_complete_with_incomplete_tail_fails()`;
- `test_declared_less_than_complete_fails()`.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_mlab.py -q -p no:cacheprovider -k "partial or truncation or internal_corruption or declared"
```

Implementation requirements:

1. Accept partial only when the file ends inside exactly one final started
   configuration.
2. Require the header relation from the authoritative P2-1 note exactly.
3. Scan enough trailing structure to distinguish internal corruption followed by
   a later configuration marker.
4. Return complete configurations separately from the discarded tail metadata.
5. Never classify arbitrary parser exceptions as EOF salvage.

Run the focused command to green.

Checkpoint: partial/error classification.

## Task 4: Implement `mlab-seed-v1` Canonical Serialization

Add failing tests:

- `test_canonical_digest_is_stable_across_text_formatting()`;
- `test_canonical_digest_normalizes_negative_zero()`;
- `test_canonical_digest_uses_big_endian_float64_and_lengths()`;
- `test_canonical_digest_uses_raw_kbar_stress_without_ase_conversion()`;
- `test_canonical_digest_changes_for_lattice_position_energy_force_or_stress()`;
- `test_prefix_digest_hashes_exactly_first_n_configurations()`;
- `test_prefix_digest_rejects_short_source()`;
- `test_digest_schema_name_is_mlab_seed_v1()`.

Implementation requirements:

1. Serialize fields in the exact authoritative order.
2. Include string/array length and shape prefixes.
3. Use UTF-8 strings, fixed-width big-endian integers, and big-endian IEEE-754
   float64 bytes.
4. Normalize `-0.0` to `+0.0`; reject nonfinite values before serialization.
5. Perform no tolerance rounding.
6. Keep schema name and digest together in the returned identity object.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_mlab.py -q -p no:cacheprovider -k "canonical or digest or prefix"
```

Checkpoint: canonical identity implementation.

## Task 5: Replace Incremental Dataset Mutation with Parsed Results

Add/adjust tests:

- `test_dataset_adds_complete_parsed_configurations()`;
- `test_dataset_load_mlab_does_not_mutate_on_invalid_source()`;
- `test_dataset_ase_conversion_uses_expected_energy_forces_and_stress()`;
- `test_count_ml_ab_configs_does_not_replace_full_initial_seed_validation()`.

Implementation:

1. Remove raw regex parsing from `Dataset.load_ml_ab()`.
2. Convert a known structured result to ASE objects only after parse
   classification.
3. Keep a temporary compatibility wrapper fail-closed for partial input until
   Plan 08 supplies the source-status destination; it must add no frames before
   raising.
4. Preserve current ASE calculator sign/unit conversion only in the Dataset
   conversion layer, not in canonical digest input.
5. Mark simple header-only count reads as insufficient for Stage1 seed proof;
   Plan 04 must call the full parser.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_mlab.py tests/test_collect.py -q -p no:cacheprovider -k "ml_ab or mlab or dataset"
```

Full checkpoint:

```powershell
& $python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

## Acceptance Traceability

| P2-1 parser/digest requirement | Evidence |
| --- | --- |
| complete source parsing | Task 2 |
| exact EOF-tail salvage boundary | Task 3 |
| first-frame/internal corruption fail | Task 3 |
| header invariants | Tasks 2 and 3 |
| fixed units/stress order | Tasks 2 and 4 |
| negative zero and nonfinite handling | Tasks 2 and 4 |
| stable canonical digest | Task 4 |
| scientific changes alter digest | Task 4 |
| no incremental Dataset mutation | Task 5 |
| portable fixtures and provenance | Task 1 |

## Plan Checkpoint

Plan 03 is complete when the pure parser/digest and Dataset compatibility tests
plus the full suite pass. Accepting partial frames into a published collection and
verifying Stage1/MD seed provenance remain open for Plans 06 and 08.
