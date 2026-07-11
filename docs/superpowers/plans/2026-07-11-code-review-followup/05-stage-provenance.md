# Plan 05: Stage Structure Provenance

Authoritative spec:
[P1-5 Stage provenance](../../../code-review-notes/P1-5-stage-provenance.md)

Consumes:

- [Plan 02 Manifest v2](02-manifest-v2.md)
- [Plan 04 One-shot build](04-one-shot-build.md)

Unlocks: Plan 06

## Goal and Done State

Make the relaxation manifest the single source of truth for how Stage1 interprets
Stage0 structures, while preserving a strict evidence-based path for existing
legacy manifests.

Done means:

- new Stage0 manifests record complete structure provenance;
- Stage1 validates immutable config/input/POSCAR identity before any MD change;
- Stage1 uses manifest `sc_rlx`, not an independently trusted current config;
- `sc` changes are rejected when Stage0 used `sc_rlx: true` and allowed when it
  used `sc_rlx: false`;
- a legacy manifest is accepted only when atom composition and in-plane cell
  evidence uniquely agree across every stacking;
- conflict or insufficient evidence fails without modifying MD;
- Stage1 records strict versus legacy evidence in the MD manifest.

## Non-goals

- Do not preserve or clear MD constraints yet; Plan 06 owns that behavior.
- Do not clear velocities yet; Plan 06 owns it.
- Do not rewrite a legacy relaxation manifest with inferred data.
- Do not use `sym_reduced_stackings.txt`, config, or directory scanning as a
  provenance fallback.
- Do not require historical calculations to possess hashes that did not exist
  when they were generated.

## Files

Create:

- `src/dpmoire_lite/provenance.py`
- `tests/test_stage_provenance.py`

Modify:

- `src/dpmoire_lite/build.py`
- `src/dpmoire_lite/build_preflight.py`
- `src/dpmoire_lite/manifest.py` only to populate fields already defined by
  Plan 02
- `tests/test_build.py`
- `tests/test_build_lifecycle.py`

## Structure-provenance Contract

New relaxation manifests record:

- `sc_rlx`, Stage0 `sc`, `n_sectors`, `symm_reduce`, `d`, `d_mode`, and
  `d_reference`;
- the exact stacking list;
- SHA-256 for top and bottom input POSCAR bytes;
- for each generated `rlx/<stacking>/POSCAR`: relative path, SHA-256, atom count,
  ordered/aggregated element composition, and cell;
- enough normalized numeric structure data to produce a useful diagnostic when
  a byte hash differs.

The Plan 05 result tells Stage1 whether provenance is strict or inferred and
provides the trusted `sc_rlx`, trusted Stage0 `sc` when applicable, stackings, and
per-directory expected structure identity.

## Task 1: Implement Pure Structure Identity and Cell-relation Tests

Add failing tests:

- `test_structure_identity_records_hash_count_composition_and_cell()`;
- `test_structure_identity_reports_specific_changed_component()`;
- `test_inplane_supercell_relation_detects_diagonal_sc()`;
- `test_inplane_supercell_relation_distinguishes_2x1_from_1x2()`;
- `test_inplane_supercell_relation_rejects_noninteger_transform()`;
- `test_inplane_supercell_relation_checks_determinant_against_atom_multiplier()`.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_stage_provenance.py -q -p no:cacheprovider -k "identity or cell_relation or supercell"
```

Expected failure: no reusable provenance module exists.

Implementation requirements:

1. File hash is byte identity; parsed fields are diagnostic/scientific identity.
2. Compare atom count and composition exactly.
3. Recover only project-supported integer in-plane transforms.
4. Use explicit numerical tolerances for parsed cell comparison and include them
   in diagnostics/tests.
5. Keep the helpers free of filesystem mutation.

Run the focused command to green.

Checkpoint: pure structure identity/inference primitives.

## Task 2: Write Complete Provenance in New Stage0 Manifests

Add failing tests:

- `test_stage0_manifest_v2_records_structure_source_fields()`;
- `test_stage0_manifest_records_input_hashes()`;
- `test_stage0_manifest_records_every_generated_rlx_poscar_identity()`;
- `test_stage0_manifest_stackings_match_generated_directories()`;
- `test_stage0_manifest_records_sc_only_with_clear_stage0_semantics()`.

Implementation:

1. Build provenance from the same validated inputs and generated POSCARs used by
   Plan 04 generation.
2. Hash actual final bytes after POSCAR writing.
3. Bind every per-stacking identity to the manifest relative path.
4. Publish provenance only in the final atomic completion manifest.
5. Do not add anchors in this task; Plan 06 records the P1-1-specific constraint
   identity after its validation rules exist.

Verification:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_stage_provenance.py tests/test_build.py -q -p no:cacheprovider -k "stage0_manifest or structure_source"
```

Checkpoint: new Stage0 provenance producer.

## Task 3: Add Failing Strict Stage1 Validation Tests

Add tests:

- `test_stage1_uses_manifest_sc_rlx_instead_of_current_config()`;
- `test_stage1_rejects_sc_rlx_change_before_md_mutation()`;
- `test_stage1_rejects_sc_change_when_stage0_was_supercell()`;
- `test_stage1_allows_md_sc_change_when_stage0_was_primitive()`;
- `test_stage1_rejects_changed_top_or_bottom_input()`;
- `test_stage1_rejects_changed_rlx_poscar()`;
- `test_stage1_rejects_contcar_atom_or_composition_mismatch()`;
- `test_stage1_aggregates_provenance_failures_across_stackings()`.

Every failure test starts with an absent `md/` directory and asserts it remains
absent.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_stage_provenance.py -q -p no:cacheprovider -k "stage1 and not legacy"
```

## Task 4: Implement Strict Stage1 Provenance Consumption

Implementation requirements:

1. Add a provenance validator to the Plan 04 aggregate-preflight pipeline.
2. Compare all immutable fields and input hashes.
3. Validate actual Stage0 POSCAR against its manifest identity.
4. Validate CONTCAR topology/composition against the Stage0 POSCAR.
5. Use manifest `sc_rlx` to decide whether Stage1 later expands CONTCAR.
6. Reject conflicting current config with a field-by-field diagnostic.
7. Permit current `sc` change only for trusted `sc_rlx: false`.
8. Return trusted interpretation; do not rewrite config.

Green command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_stage_provenance.py tests/test_build_lifecycle.py -q -p no:cacheprovider -k "stage1 and not legacy"
```

Checkpoint: strict current-manifest validation.

## Task 5: Implement Deterministic Legacy Inference

Add failing tests:

- `test_legacy_manifest_infers_primitive_from_count_composition_and_cell()`;
- `test_legacy_manifest_infers_supercell_and_sc()`;
- `test_legacy_manifest_distinguishes_same_determinant_directions()`;
- `test_legacy_manifest_rejects_count_cell_conflict()`;
- `test_legacy_manifest_rejects_inconsistent_stackings()`;
- `test_legacy_manifest_rejects_missing_poscar_or_contcar()`;
- `test_legacy_manifest_rejects_changed_current_inputs()`;
- `test_legacy_inference_conflict_with_current_config_fails_before_md()`.

Implementation requirements:

1. Enter legacy inference only when a manifest file exists and Plan 02 classifies
   it as legacy.
2. Use the manifest stacking list as the directory source.
3. Rebuild the primitive bilayer from current top/bottom inputs without writing.
4. Require atom-count/composition and in-plane cell conclusions to agree.
5. Require every stacking to yield one consistent `sc_rlx` and compatible `sc`.
6. Cross-check CONTCAR topology; allow numeric cell relaxation only when it does
   not invalidate the inferred topology.
7. Fail on ambiguity rather than choosing the current config.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_stage_provenance.py -q -p no:cacheprovider -k "legacy"
```

Checkpoint: legacy inference behavior.

## Task 6: Record Provenance Evidence in the MD Manifest

Add tests:

- `test_md_manifest_records_strict_provenance_evidence()`;
- `test_md_manifest_records_legacy_inference_evidence()`;
- `test_legacy_inference_does_not_rewrite_relaxation_manifest()`;
- `test_md_manifest_uses_relaxation_manifest_stackings_only()`.

Implementation:

1. Carry the validated provenance result into generation.
2. Record `strict` or `legacy_inference`, trusted `sc_rlx`, applicable `sc`, and
   the evidence summary in the MD manifest.
3. Do not copy invented hashes into a legacy relaxation manifest.
4. Keep MD manifest publication behind complete Stage1 generation.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_stage_provenance.py tests/test_build.py -q -p no:cacheprovider
```

Full checkpoint:

```powershell
& $python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

Manual evidence check against the retained private sample may be run separately,
but it is not an automated acceptance dependency. It should infer the known
primitive relaxation and 72-atom MD expansion without copying sample data into
the repository.

## Acceptance Traceability

| P1-5 requirement | Evidence |
| --- | --- |
| new schema records full structure source | Task 2 |
| Stage1 trusts manifest `sc_rlx` | Tasks 3 and 4 |
| input/POSCAR changes diagnosed | Tasks 1, 3, and 4 |
| `sc` rule differs by `sc_rlx` | Tasks 3 and 4 |
| legacy count + cell dual evidence | Task 5 |
| same determinant/different direction | Tasks 1 and 5 |
| all stackings consistent | Task 5 |
| missing manifest has no fallback | Plan 04, retained here |
| MD manifest records strict/legacy evidence | Task 6 |
| relaxation legacy manifest not rewritten | Tasks 5 and 6 |

## Plan Checkpoint

Plan 05 is complete when strict and legacy provenance tests, affected build suites,
and full suite pass. MD constraints, velocities, and grid-shift anchor provenance
remain open for Plan 06.
