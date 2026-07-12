# Plan 06: MD Structure Normalization and Seed Staging

Authoritative specs:

- [P1-1 MD Selective Dynamics](../../../code-review-notes/P1-1-md-selective-dynamics.md)
- [P1-6 MD initial velocities](../../../code-review-notes/P1-6-md-initial-velocities.md)
- [P2-1 Stage1 seed producer](../../../code-review-notes/P2-1-partial-collection-and-mlff-seed.md)

Consumes:

- [Plan 03 ML_AB parser and digest](03-mlab-parser-digest.md)
- [Plan 05 Stage provenance](05-stage-provenance.md)

Unlocks: Plan 06R

## Goal and Done State

Give every Stage1 MD output an explicit, provenance-validated structure policy:
new trajectories never inherit velocities, constraints are cleared by default,
optional grid-shift preservation keeps only the two verified project anchors, and
the initial MLFF seed identity is recorded immutably at distribution time.

Done means:

- `preserve_grid_shift_md` defaults to `false` in config and bundled examples;
- all bilayer and monolayer MD POSCARs explicitly contain no constraints by
  default;
- all Stage1 MD POSCARs contain no velocity/momenta block regardless of source;
- preservation accepts only the two exact Stage0 `F F T` project anchors;
- expanded primitive structures keep exactly two anchors using stable
  `(source_index, image_translation)` identity;
- legacy manifests can proceed only with default constraint clearing;
- Stage1 records and verifies initial seed count, canonical digest, and copied
  file hashes in the MD manifest.

## Non-goals

- Do not preserve arbitrary user constraints.
- Do not add a Stage1 option to preserve velocities.
- Do not implement MD restart.
- Do not duplicate anchors into every periodic image.
- Do not infer initial seed identity from a later `md/ML_AB`.
- Do not implement collection-time seed-prefix verification; Plan 08 owns it.

## Files

Create:

- `tests/test_md_normalization.py`

Modify:

- `src/dpmoire_lite/config.py`
- `src/dpmoire_lite/build.py`
- `src/dpmoire_lite/build_preflight.py`
- `src/dpmoire_lite/structures.py`
- `src/dpmoire_lite/inputs.py`
- `src/dpmoire_lite/manifest.py` only to populate Plan 02 fields
- `tests/test_config.py`
- `tests/test_build.py`
- `tests/test_structures_inputs.py`
- `example/config.yaml`
- `src/dpmoire_lite/example/config.yaml`
- `README.md`
- `README_CH.md`
- `workflow.md`
- `workflow_CH.md`

## Task 1: Add the Explicit Configuration Contract

Add failing tests:

- `test_preserve_grid_shift_md_defaults_false_when_omitted()`;
- `test_preserve_grid_shift_md_accepts_explicit_boolean()`;
- `test_preserve_grid_shift_md_rejects_invalid_boolean()`;
- `test_source_and_bundled_examples_show_false_default()`;
- `test_wheel_example_contains_preserve_grid_shift_md_false()`.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_config.py tests/test_cli.py -q -p no:cacheprovider -k "preserve_grid_shift or wheel_example"
```

Expected failure: the config field does not exist.

Implementation:

1. Add a defaulted boolean field without making it a required legacy config key.
2. Add it to source and bundled examples with an explanation that `F F T` means
   fixed x/y and movable z.
3. Document that `true` preserves only project grid-shift anchors.

Run the focused command to green.

Checkpoint: config/docs only.

## Task 2: Record Exact Stage0 Grid-shift Anchor Provenance

Add failing tests:

- `test_stage0_manifest_records_two_anchor_indices_per_stacking()`;
- `test_anchor_fixed_masks_use_true_equals_fixed()`;
- `test_ff_t_serializes_as_true_true_false()`;
- `test_anchor_record_is_bound_to_poscar_hash_and_atom_count()`;
- `test_anchor_indices_follow_final_sorted_poscar_order()`.

Implementation requirements:

1. Identify anchors in the final Stage0 POSCAR atom order, not pre-sort indexes.
2. Record top and bottom index separately.
3. Record `fixed_masks` with ASE semantics: `true` is fixed.
4. Require exactly `[true, true, false]` for both anchors.
5. Bind the record to Plan 05 POSCAR hash and atom count.
6. Fail Stage0 completion if the generated constraint set cannot be represented
   by this exact contract.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_md_normalization.py tests/test_build.py -q -p no:cacheprovider -k "anchor and stage0"
```

Checkpoint: anchor provenance producer.

## Task 3: Normalize All New-MD Structures by Default

Add failing tests covering zero and nonzero source velocities:

- `test_sc_rlx_true_default_clears_all_constraints_and_momenta()`;
- `test_sc_rlx_false_default_clears_before_supercell_expansion()`;
- `test_monolayer_md_clears_constraints_and_momenta()`;
- `test_generated_md_poscar_has_no_selective_dynamics_by_default()`;
- `test_generated_md_poscar_has_no_velocity_block()`;
- `test_reread_md_poscar_has_no_constraints_or_momenta()`;
- `test_monolayer_constraint_clear_emits_one_warning()`.

Tests must inspect ASE semantics and parsed output, not a particular float string.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_md_normalization.py -q -p no:cacheprovider -k "default or momenta or velocity or monolayer"
```

Expected failure: direct Stage1 paths preserve constraints and both Stage1 paths
can preserve/copy `momenta`.

Implementation requirements:

1. Create one Stage1 structure-normalization helper used by direct, expanded, and
   monolayer paths.
2. Clear momenta through the public ASE API before expansion.
3. For default constraint behavior, clear constraints explicitly before any
   expansion and again assert the final structure has none.
4. Do not rely on ASE `make_supercell()` dropping constraints.
5. Keep this helper semantically limited to new MD; do not reuse it for restart.

Run the focused command to green.

Checkpoint: default scientific behavior and velocity fix.

## Task 4: Validate Preserved Anchors Before MD Mutation

Add failing tests for `preserve_grid_shift_md: true`:

- `test_preserve_true_accepts_exact_two_manifest_anchors()`;
- `test_preserve_true_rejects_missing_anchor()`;
- `test_preserve_true_rejects_extra_constraint()`;
- `test_preserve_true_rejects_changed_fixed_mask()`;
- `test_preserve_true_rejects_other_constraint_type()`;
- `test_preserve_true_rejects_atom_order_or_hash_mismatch()`;
- `test_preserve_true_rejects_legacy_manifest()`;
- `test_preserve_validation_failure_leaves_md_absent()`.

Implementation requirements:

1. Add anchor validation to Plan 04 preflight using Plan 05 trusted provenance.
2. Compare the entire source CONTCAR constraint set to the two manifest anchors.
3. Require exact indices and masks in the same atom order.
4. Reject extra, missing, or different constraint types instead of filtering them.
5. Permit legacy provenance only when preservation is false.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_md_normalization.py -q -p no:cacheprovider -k "preserve_true and not expansion"
```

Checkpoint: preserve-mode fail-closed validation.

## Task 5: Preserve Exactly Two Anchors Through Expansion

Add failing tests:

- `test_primitive_expansion_keeps_exactly_two_anchors()`;
- `test_anchor_count_is_independent_of_supercell_area()`;
- `test_rectangular_expansion_selects_zero_translation_image()`;
- `test_source_index_alone_is_not_used_as_unique_identity()`;
- `test_sort_recovers_anchor_by_source_and_translation()`;
- `test_md_manifest_records_final_source_translation_identity()`;
- `test_sc_rlx_true_and_false_paths_have_same_anchor_count()`.

Use at least `[2, 1]`, `[1, 2]`, and `[2, 3]` expansion cases.

Implementation requirements:

1. Attach temporary `source_index` before expansion.
2. Generate an integer `image_translation` for every periodic image.
3. Define identity as the pair, not source index alone.
4. Select both source anchors at translation `[0, 0, 0]`.
5. Sort, recover the final indices by complete identity, remove temporary arrays,
   and set only the two `FixedLine` anchors.
6. Record final index, source index, and translation in the MD manifest.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_md_normalization.py -q -p no:cacheprovider -k "expansion or translation or anchor_count"
```

Checkpoint: deterministic preserve-mode expansion.

## Task 6: Stage and Record the Immutable Initial MLFF Seed

Add failing tests:

- `test_stage1_seed_preflight_requires_complete_initial_mlab()`;
- `test_stage1_seed_manifest_records_count_schema_and_prefix_digest()`;
- `test_stage1_seed_manifest_records_raw_ml_ab_and_ml_ff_hashes()`;
- `test_stage1_verifies_copied_size_and_hash()`;
- `test_stage1_seed_copy_failure_prevents_complete_md_manifest()`;
- `test_each_md_directory_receives_identical_initial_seed_identity()`;
- `test_later_md_ml_ab_changes_do_not_modify_manifest_seed_identity()`.

Implementation requirements:

1. Reuse the complete parsed seed result from Plan 04 preflight; do not parse only
   a header count during generation.
2. Compute `mlab-seed-v1` count/schema/digest through Plan 03.
3. Compute raw SHA-256 for initial `ML_ABN` and `ML_FFN`.
4. Copy to each MD directory as `ML_AB` and `ML_FF`.
5. Verify destination size and raw hash.
6. Record the immutable identity in the final MD manifest.
7. Treat later user restart replacement of `md/ML_AB` as outside Stage1; never
   mutate the recorded identity in response.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_md_normalization.py tests/test_mlab.py tests/test_build.py -q -p no:cacheprovider -k "seed or mlff"
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
| false default and examples | Task 1 |
| exact Stage0 anchors / `true=fixed` | Task 2 |
| default clears all constraints | Task 3 |
| every new MD clears velocities | Task 3 |
| preserve rejects unproven constraints | Task 4 |
| legacy preserve true fails | Task 4 |
| expansion keeps only two anchors | Task 5 |
| stable source+translation identity | Task 5 |
| MD manifest records final anchors | Task 5 |
| seed count/digest/raw hashes recorded | Task 6 |
| copied seed verified | Task 6 |

## Plan Checkpoint

Plan 06 is complete when config/example, anchor, constraint, velocity, seed-staging,
affected build, wheel-content, and full suites pass. MD restart remains deferred,
and collection-time prefix verification remains open for Plan 08.
