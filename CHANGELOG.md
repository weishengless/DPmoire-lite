# Changelog

## Unreleased

### Added

- Added `d_mode` to control how `d` is interpreted when constructing bilayer and validation structures.
- Added `surface_gap` mode, where `d` sets `min_z(top) - max_z(bot)`.
- Added `reference_plane_gap` mode with optional `d_reference` selectors, where `d` sets the selected reference-plane mean-z distance.
- Added `grid_shift_anchor` to choose the two grid-shift anti-slide anchor atoms by element (one per layer; per-layer mapping or one symbol for both). Defaults keep the previous sorted first-atom anchors, and invalid elements fail preflight before any directory is created.
- Added `potcar_policy` with `recommend` and `minimal` modes for choosing VASP-recommended or lowest-`ZVAL` regular POTCAR variants.
- Added typed `--mlff-collect-mode seed-aware|full-dedup` collection. The
  seed-aware default validates immutable seed provenance, while explicit
  full-dedup is MLFF-MD-only, reads every accepted final `ML_ABN`, retains the
  first exact copy (including one shared seed), and uses more I/O.
- Added explicit `init_mlff_mode: single-job` workspace preparation while
  retaining `manual` as the default. The opt-in mode preflights separate bottom
  and top scientific INCAR templates, transactionally publishes independent
  static VASP input directories, validates and promotes both MLFF phases, and
  renders one init-specific submit adapter from an exact marked Bash template.
  Source/generated script hashes and lifecycle evidence are recorded in the init
  manifest. Users inspect the adapter, submit it from the derived script
  directory, and run both phases in one allocation; the workflow preserves the
  real launcher exit code. With fire-and-forget `submit: true`, DPmoire-lite now
  submits exactly that one root adapter, persists the returned job ID and script
  hash without claiming completion, and records bounded evidence on `sbatch`
  failure. Generation, submission request, workflow completion, and workflow
  failure have distinct CLI statuses. Scheduler polling, retry, resume, and
  cross-job dependencies remain deferred. Completed v1 workflow evidence remains
  readable for Stage1.

### Fixed

- Prevented automatic single-job submission from entering Slurm wait mode via
  template `#SBATCH`/`#SLURM -W` variants (including heterogeneous components)
  or inherited `SBATCH_WAIT`, and rejected translated `#PBS`/`#BSUB` scheduler
  directives on that automatic path. Also hardened the init workflow lock
  against symlink, reparse-point, hard-link, and replacement races before any
  lock-file write.
- Build status now reports `submission_requested` only after at least one real
  `sbatch` request succeeds; a stage with no enabled target reports `generated`.
- All generated stages now share one workflow-wide ENCUT derived from the maximum ENMAX across the selected top- and bottom-layer POTCAR variants, while each generated POTCAR remains local to its POSCAR species and order. Stage1 rejects drift from new relaxation-manifest cutoff evidence, and preflight rejects ambiguous ENMAX or lowest-`ZVAL` selections.
- Generated POTCAR files are now byte-concatenated from source potentials without inserting extra blank lines.
- Mo and W now resolve to the VASP-recommended `Mo_sv` and `W_sv` potentials when those directories are available.
- Generated KPOINTS now use each output POSCAR's actual in-plane cell lengths, so primitive and supercell stages get distinct meshes.
- `stage: all` and submitted `--wait` now fail closed before side effects while Slurm terminal-state validation and failure propagation remain deferred; use `submit: false` and submit generated stages manually.
- Preserved layer thickness when stacking thick interface structures.
- Applied the selected spacing mode consistently to twist validation structures.
- Rejected tilted slab cells whose spacing direction is not aligned with Cartesian z.
- Improved layer recovery when ASE tags are unavailable by falling back to the largest z-gap split.
- Collection now maps structured `complete`, `fatal`, `degraded`, and `no_data`
  results to exits 0, 1, 2, and 3 and prints one concise stderr summary; every
  nonzero code is a shell failure. `no_data` never creates, deletes, or replaces
  extxyz and preserves existing output bytes. Invalid full-dedup combinations
  fail before manifest access. Current Manifest v2 remains authoritative;
  legacy/missing compatibility writes `MD_data.collect.yaml` without rewriting
  build provenance, and missing-manifest scans are full-dedup-only and at best
  degraded when they produce frames.
- Seed-aware MLFF collection now accepts VASP-rewritten seed prefixes only when
  a trusted reference passes the fixed `vasp-seed-prefix-equivalence-v1`
  component rule. Exact `mlab-seed-v1`/`mlab-config-v1` identities and
  full-dedup remain unchanged. Result manifests publish aggregate verification
  counts and bounded per-source hashes, trust, deltas, and mismatch evidence
  without serializing complete seed configurations.
