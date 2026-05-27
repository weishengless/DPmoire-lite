# Changelog

## Unreleased

### Added

- Added `d_mode` to control how `d` is interpreted when constructing bilayer and validation structures.
- Added `surface_gap` mode, where `d` sets `min_z(top) - max_z(bot)`.
- Added `reference_plane_gap` mode with optional `d_reference` selectors, where `d` sets the selected reference-plane mean-z distance.

### Fixed

- Preserved layer thickness when stacking thick interface structures.
- Applied the selected spacing mode consistently to twist validation structures.
- Rejected tilted slab cells whose spacing direction is not aligned with Cartesian z.
- Improved layer recovery when ASE tags are unavailable by falling back to the largest z-gap split.
