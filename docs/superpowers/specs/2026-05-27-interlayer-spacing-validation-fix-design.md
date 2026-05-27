# Interlayer Spacing Modes And Validation Geometry Fix Design

## Problem

DPmoire-lite currently has one numeric field, `d`, documented as an interlayer distance. The implementation uses the average fractional `z` positions of the two input layers, shifts them by `+/- d / total_c`, and therefore makes `d` closer to a mean-plane separation than to a surface-to-surface interface gap.

That behavior is useful in some moire workflows, especially when users want the vertical distance between selected reference atoms such as metal planes. The bug is that DPmoire-lite exposes only one ambiguous `d` definition, while thick layers need an unambiguous interface-gap definition.

Confirmed reproduction with the supplied Pt/O/Pt inputs:

- With `d = 7.3`, current normal stacking gives `top_min_z - bot_max_z = 3.011199 A`.
- The same input through validation/twist gives an interface gap around `2.27-2.58 A`, depending on the diagnostic split after wrapping.
- `_find_homo_twist.adjust_atoms_d()` also mutates both monolayer cells to `top_cell @ diag(1, 1, 0.5)`.
- On the supplied bottom layer, this changes `cell_c` from `25 A` to `10 A`; after `make_supercell()`, the apparent bottom-layer thickness changes from `6.805790 A` to `7.731403 A`.

## New User Contract

Keep `d` as the numeric distance, and add a mode field:

```yaml
d: 7.3
d_mode: surface_gap          # surface_gap | reference_plane_gap
```

`surface_gap` means:

```text
d = min_z(top layer atoms) - max_z(bottom layer atoms)
```

`reference_plane_gap` means:

```text
d = mean_z(top reference atoms) - mean_z(bottom reference atoms)
```

Reference atoms default to all atoms in each layer. Users can optionally select reference elements per layer:

```yaml
d: 7.3
d_mode: reference_plane_gap
d_reference:
  top: [Pt]
  bot: [Pt]
```

Allowed `d_reference` values:

- omitted: use all atoms in both layers
- `top: all` or `bot: all`: use all atoms in that layer
- `top: [Pt, Mo]` or `bot: [Pt]`: use only matching element symbols in that layer

Default for new and existing configs:

```yaml
d_mode: surface_gap
```

This matches the least ambiguous reading of "interlayer distance" and fixes thick interface systems by default. Users who need the old mean-plane-like behavior can set `d_mode: reference_plane_gap`; users who need metal-plane distances can also set `d_reference`.

## Geometry Design

Introduce shared layer-spacing helpers in `src/dpmoire_lite/structures.py`.

The structure code will assign stable ASE tags while combining layers:

- `TOP_LAYER_TAG = 1`
- `BOT_LAYER_TAG = 2`

Layer membership will be derived from tags whenever available. The existing `z > 0.5` fallback remains only for legacy structures read from disk that do not carry tags.

Normal stacking construction:

1. Read top and bottom structures without modifying internal Cartesian geometry.
2. Build the combined in-plane cell using the existing averaged-cell policy for compatibility.
3. Convert both layers into that shared cell.
4. Assign layer tags before sorting.
5. Place the two layers according to `d_mode`.
6. Sort atoms while preserving layer tags.

For `surface_gap`, placement uses layer boundary atoms:

```text
target_top_min = center_z + d / 2
target_bot_max = center_z - d / 2
```

For `reference_plane_gap`, placement uses reference atom means:

```text
target_top_ref_mean = center_z + d / 2
target_bot_ref_mean = center_z - d / 2
```

In both modes, changing `d` rigidly translates each layer and must not change either layer's internal Cartesian geometry.

## Validation/Twist Design

Validation/twist must stop using the current pre-positioning logic in `_find_homo_twist.adjust_atoms_d()` because it changes monolayer cells before twist expansion.

New flow:

1. Copy pristine top and bottom layer inputs.
2. Build top and bottom twist supercells from those pristine layers.
3. Stack without reordering so bottom and top atom ranges are known.
4. Assign layer tags to the stacked output.
5. Apply the same `d_mode` placement helper to the final stacked structure.
6. Sort only after tags and spacing have been applied.

The legacy `_find_homo_twist.adjust_atoms_d()` helper will be rewritten as a cell-preserving compatibility function. It will rigidly translate two `Atoms` objects to a `surface_gap` of `d` and will never call `set_cell()`.

## Config Design

Modify `DPmoireLiteConfig` to include:

```python
d_mode: str = "surface_gap"
d_reference: dict[str, str | list[str]] | None = None
```

Validation rules:

- `d_mode` must be `surface_gap` or `reference_plane_gap`.
- `d_reference` is only meaningful with `reference_plane_gap`.
- `d_reference.top` and `d_reference.bot`, when present, must be `all`, a string element symbol, or a list of element symbols.
- If an element selector matches no atoms in its layer, fail early with a clear `ValueError`.

Generated manifests should include `d_mode` and a YAML-safe `d_reference` summary so calculation folders remain self-describing after generation.

## Cell Height Handling

If the combined cell's `c` length is too short to contain both layer thicknesses plus the requested spacing while preserving the existing vacuum budget, DPmoire-lite will expand only the `c` vector before placement.

For `surface_gap`:

```text
required_height = bot_thickness + d + top_thickness
vacuum_budget = max(current_c - bot_thickness - top_thickness, 0)
target_c = max(current_c, required_height + vacuum_budget)
```

For `reference_plane_gap`, the same conservative `required_height` is computed from the final layer bounds after reference-plane placement. If any atom would sit outside the cell along `z`, expand `c` enough to keep the full bilayer inside the cell with the previous vacuum budget.

## Testing

Add focused tests that fail on the current implementation:

- Config accepts missing `d_mode` and defaults to `surface_gap`.
- Config accepts explicit `surface_gap`.
- Config accepts `reference_plane_gap` with omitted references.
- Config accepts `reference_plane_gap` with top/bottom element references.
- Config rejects invalid `d_mode`.
- Normal stacking with `surface_gap` sets `top_min_z - bot_max_z` exactly to `d`.
- Normal stacking with `reference_plane_gap` sets selected reference-plane mean distance exactly to `d`.
- Changing `d` changes only rigid layer offsets, not layer thickness.
- Layer tags survive `sort()` and `make_supercell()` well enough for constraints to choose one top and one bottom atom.
- Validation/twist preserves top and bottom layer thickness after twist supercell construction.
- Validation/twist applies the selected `d_mode`.
- `_find_homo_twist.adjust_atoms_d()` preserves both input cells.

Run both focused tests and the full suite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py tests\test_interlayer_geometry.py -v
.\.venv\Scripts\python.exe -m pytest -v
```

## Documentation

Update the English and Chinese README config tables:

- `d`: numeric distance value
- `d_mode`: chooses how `d` is interpreted
- `d_reference`: optional reference atom selectors for `reference_plane_gap`

Update both example config files to show:

```yaml
d: 7.3
d_mode: surface_gap
# d_reference:
#   top: [Pt]
#   bot: [Pt]
```

## Non-Goals

- Do not change the twist-angle search formula.
- Do not redesign the full DPmoire-lite workflow.
- Do not change `r_cut` semantics beyond keeping its existing dependency on the numeric `d` value.
- Do not implement arbitrary atom-index reference selectors in this pass; element selectors are enough for metal-plane workflows.
- Do not attempt to infer chemically meaningful contact distances automatically; the selected mode remains an explicit user choice.

## Acceptance Criteria

- `surface_gap` with the supplied Pt/O/Pt reproducer yields `top_min_z - bot_max_z = d` for normal stacking and validation/twist.
- `reference_plane_gap` yields `mean_z(top reference atoms) - mean_z(bot reference atoms) = d` for normal stacking and validation/twist.
- Top and bottom layer Cartesian thicknesses are unchanged except for intended in-plane twist/supercell replication.
- Validation/twist no longer mutates monolayer cells before twist expansion.
- The full pytest suite passes.
- README and example comments clearly explain both `d` modes.
