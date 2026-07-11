# Plan 01: INCAR Engine and Preflight Adapter

Authoritative spec:
[P1-4 INCAR safe rendering](../../../code-review-notes/P1-4-incar-safe-rendering.md)

Depends on: Plan 00

Unlocks: Plan 04

## Goal and Done State

Replace line/whitespace-based INCAR rewriting with one conservative parser and
analysis result used by controlled-tag rendering, duplicate detection, and vdW
dependency queries.

Done means the parser correctly handles supported VASP syntax, rendering changes
only `ENCUT`, `ML_RCUT1`, and `ML_RCUT2`, user-owned values such as
`LANGEVIN_GAMMA` remain unchanged, and all automatic repairs/conflicts carry
source locations suitable for Plan 04 aggregate preflight.

## Non-goals

- Do not implement a complete VASP semantic validator.
- Do not decide whether user-selected physical values are reasonable.
- Do not modify source templates in place.
- Do not create calculation directories in this module.
- Do not wire the final no-side-effect build preflight; Plan 04 consumes the
  adapter created here.

## Files

Create:

- `src/dpmoire_lite/incar.py`
- `tests/test_incar.py`

Modify:

- `src/dpmoire_lite/inputs.py`
- `tests/test_structures_inputs.py`

Plan 04 later modifies:

- `src/dpmoire_lite/build_preflight.py`
- `src/dpmoire_lite/build.py`

## Required Interfaces

The exact private types may change, but Plan 01 must expose one parsed document
that supports:

- ordered statements with line/column or statement-position information;
- case-insensitive tag lookup;
- effective, unambiguous value queries such as `LUSE_VDW` and `ML_LMLFF`;
- duplicate classification;
- rendering controlled values without reparsing the original text;
- diagnostics separated into repairable warnings and blocking conflicts.

`inputs.render_incar()` and `inputs.copy_vdw_if_needed()` must consume that same
analysis behavior. They must not maintain separate syntax rules.

## Task 1: Specify the Parser with Failing Syntax Tests

Add parameterized tests:

- `test_parse_incar_accepts_assignment_spacing_and_case()`;
- `test_parse_incar_splits_semicolon_statements()`;
- `test_parse_incar_ignores_hash_and_bang_comments()`;
- `test_parse_incar_preserves_quoted_special_characters()`;
- `test_parse_incar_handles_backslash_continuation()`;
- `test_parse_incar_records_statement_locations()`;
- `test_parse_incar_reports_unsafe_unclosed_quote_with_location()`.

Fixture strings must include:

```text
ENCUT=400
encut = 450; ISMEAR=-1
SYSTEM = "a;#b!c"
LONG_VALUE = one two \
             three
```

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_incar.py -q -p no:cacheprovider -k "parse_incar"
```

Expected failure: `dpmoire_lite.incar` and its structured parser do not exist.

## Task 2: Implement the Minimal Lexical/Statement Model

Implementation requirements:

1. Scan characters while tracking quote, comment, continuation, and statement
   boundaries.
2. Treat `;`, `#`, and `!` as syntax only outside quotes.
3. Preserve original statement/comment text and ordering.
4. Normalize tag identity to uppercase only for lookup; preserve source spelling
   for diagnostics.
5. Reject an unsafe construct with its location instead of guessing.
6. Keep parsing independent of filesystem and VASP input generation.

Green command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_incar.py -q -p no:cacheprovider -k "parse_incar"
```

Checkpoint: parser model and syntax tests.

## Task 3: Add Duplicate and Effective-value Analysis

Add failing tests:

- `test_controlled_duplicates_are_repairable_even_when_values_differ()`;
- `test_user_duplicates_with_equal_normalized_text_are_repairable()`;
- `test_user_duplicates_with_different_text_are_blocking()`;
- `test_comment_text_is_not_a_duplicate_definition()`;
- `test_duplicate_detection_spans_semicolon_statements()`;
- `test_effective_luse_vdw_accepts_supported_true_spellings()`;
- `test_conflicting_luse_vdw_is_blocking()`;
- `test_ml_lmlff_effective_value_controls_missing_rcut_policy()`.

Normalization for equal user-owned values is limited to tag case, surrounding
whitespace, and whitespace around `=`. Do not treat `T` and `.TRUE.` or `0` and
`0.0` as equal.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_incar.py -q -p no:cacheprovider -k "duplicate or luse_vdw or ml_lmlff"
```

Implement analysis on the parsed document, then run the same command to green.

Checkpoint: structured diagnostics and effective-value queries.

## Task 4: Implement Conservative Controlled-tag Rendering

Add failing tests:

- `test_render_updates_no_space_and_lowercase_controlled_tags()`;
- `test_render_preserves_unrelated_semicolon_statements()`;
- `test_render_disables_all_controlled_duplicates_and_emits_one_value()`;
- `test_render_disables_later_equal_user_duplicates()`;
- `test_render_refuses_conflicting_user_duplicates()`;
- `test_render_adds_missing_encut_with_annotation()`;
- `test_render_adds_only_missing_rcut_when_mlff_is_enabled()`;
- `test_render_does_not_add_rcut_when_mlff_is_disabled()`;
- `test_render_never_rewrites_langevin_gamma()`;
- `test_render_preserves_source_comments_and_statement_order()`.

Implementation requirements:

1. Render from the parsed document, not a second line scanner.
2. Control only `ENCUT`, `ML_RCUT1`, and `ML_RCUT2`.
3. Preserve or explicitly comment disabled source statements.
4. Preserve unrelated statements on the same original line.
5. Attach a generated annotation for missing controlled values.
6. Return diagnostics with the rendered text so callers can deduplicate warnings
   per template/tag.

Verification:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_incar.py -q -p no:cacheprovider -k "render"
```

Checkpoint: renderer and tests.

## Task 5: Replace the Legacy `inputs.py` Syntax Paths

Add/adjust integration tests:

- `test_render_incar_uses_shared_document_analysis()`;
- `test_needs_vdw_kernel_detects_semicolon_and_lowercase_definition()`;
- `test_needs_vdw_kernel_rejects_conflicting_definitions()`;
- update `test_replace_incar_values_updates_encut_rcut_and_langevin()` so it
  expects the original `LANGEVIN_GAMMA` value to remain unchanged;
- `test_copy_vdw_if_needed_uses_the_same_effective_luse_vdw()`.

Implementation:

1. Make `replace_incar_values()` a compatibility wrapper around the new parser
   and renderer, or replace internal callers with a clearer document API.
2. Make `needs_vdw_kernel()` use the parsed effective-value query.
3. Ensure `render_incar()` writes only after analysis has no blocking conflict.
4. Expose a filesystem-free validation function that Plan 04 can call during
   aggregate preflight.
5. Do not emit per-directory warning spam; return stable diagnostic identities
   so Plan 04 can emit once per template/tag.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_incar.py tests/test_structures_inputs.py -q -p no:cacheprovider
```

Full checkpoint:

```powershell
& $python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

## Acceptance Traceability

| P1-4 requirement | Test/task |
| --- | --- |
| no-space/case-insensitive assignments | Tasks 1 and 4 |
| semicolon/comment/quote/continuation handling | Tasks 1 and 2 |
| controlled duplicate repair | Tasks 3 and 4 |
| equal user duplicate repair | Tasks 3 and 4 |
| conflicting user duplicate blocks | Tasks 3 and 5 |
| missing ENCUT/conditional RCUT | Task 4 |
| preserve `LANGEVIN_GAMMA` | Tasks 4 and 5 |
| shared vdW interpretation | Tasks 3 and 5 |
| source templates remain read-only | render integration tests |
| location-aware diagnostics | Tasks 1 through 3 |

## Plan Checkpoint

Plan 01 is complete when the new syntax engine and `inputs.py` integration pass
their focused and full suites. Build-wide aggregation and proof of no directory
side effects remain explicitly open until Plan 04.
