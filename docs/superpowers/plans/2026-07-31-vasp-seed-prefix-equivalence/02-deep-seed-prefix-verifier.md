# Ticket 02: Deep Seed-prefix Verifier

Depends on: Ticket 01

Unlocks: Ticket 03

## Goal

Implement the pure exact/equivalent/mismatch comparison module behind a small
interface, without filesystem/manifest adapters or collection integration.

## Files

Read first:

- `src/dpmoire_lite/AGENTS.md`
- `tests/AGENTS.md`

Add:

- `src/dpmoire_lite/mlff_seed.py`
- `tests/test_mlff_seed.py`

Modify only if a shared immutable model is proven necessary:

- `src/dpmoire_lite/collect_models.py`

Do not modify `mlab.py`; consume its existing parser and exact identities.

## Interface

The external seam exposes an immutable result and one verifier operation. Exact
names may be refined, but callers must not learn numeric field loops or delta
calculation details.

Required vocabulary:

- outcome: `exact`, `vasp_equivalent`, `mismatch`;
- schema: `vasp-seed-prefix-equivalence-v1`;
- exact expected/actual identities;
- deterministic first mismatch;
- per-field maximum absolute and scaled deltas.

The pure verifier accepts already trusted reference configurations and parsed
final configurations. Ticket 03 supplies trusted-reference adapters.

## Tests

Add:

- `test_exact_prefix_returns_exact_and_zero_deltas()`;
- `test_vasp_641_rewrite_returns_vasp_equivalent()`;
- `test_vasp_651_rewrite_returns_vasp_equivalent()`;
- `test_vasp_equivalence_uses_fixed_v1_scaled_rule()`;
- `test_numeric_delta_at_threshold_is_accepted()`;
- `test_numeric_delta_above_threshold_is_mismatch()`;
- `test_element_count_atom_or_configuration_order_change_is_mismatch()`;
- `test_each_numeric_field_reports_deterministic_component_context()`;
- `test_short_prefix_is_mismatch()`;
- `test_equivalence_returns_no_approximate_digest()`;
- `test_exact_seed_and_config_identity_outputs_are_unchanged()`.

## RED

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
& $python -c "import sys; print(sys.executable)"
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_mlff_seed.py -q -p no:cacheprovider
```

Expected RED: the focused verifier module and structured result do not exist.

## GREEN

1. Compare the exact prefix identity first.
2. Return `exact` immediately when identities match.
3. For non-exact pairs, require exact structural fields and ordered shapes.
4. Apply only:

   ```text
   abs(a - b) <= 1e-12 * max(1.0, abs(a), abs(b))
   ```

5. Compare lattice, positions, energy, forces, and raw kbar stress in existing
   source units.
6. Return `vasp_equivalent` only when every scalar satisfies the fixed rule.
7. Return `mismatch` with stable first-field/component context otherwise.
8. Compute per-field max absolute/scaled deltas without dumping full arrays.
9. Do not mutate configurations or construct ASE objects.
10. Do not add an approximate digest, rounding, pairwise source comparison, or
    user-configurable policy.

Run the focused command to green.

## Affected and Full Verification

```powershell
& $python -m pytest tests/test_mlff_seed.py tests/test_mlab.py `
  tests/test_mlff_full_dedup.py -q -p no:cacheprovider
& $python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

Expected: exact identity and full-dedup regressions remain green.

## Stop Conditions

Stop for design review if either paired version exceeds the approved rule, the
pure interface needs filesystem/manifest knowledge, or existing exact identity
bytes change.

## Checkpoint

The pure deep verifier is green through its interface. Stop before adapters.
