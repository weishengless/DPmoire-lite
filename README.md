# DPmoire-lite

Chinese documentation is available in [README_CH.md](README_CH.md). A detailed
workflow guide is available in [workflow.md](workflow.md), with the Chinese
version in [workflow_CH.md](workflow_CH.md).

Newcomers using a coding agent can start with the repository-scoped
`$dpmoire-guide` skill and the Chinese
[agent guide](docs/dpmoire-agent-guide.zh-CN.md).

DPmoire-lite is a clean VASP dataset-generation framework extracted from the
original DPmoire workflow. It generates calculation folders for bilayer or moire
force-field dataset construction, submits optional Slurm jobs, and collects
completed calculations into separate `extxyz` datasets.

The project focuses only on dataset preparation. It does not train a model and
does not merge relaxation, MD, and validation datasets automatically.

## Quick Start

```bash
DPmoireLite init-example my_case
cd my_case
# Edit config.yaml, input files, scripts, and potcar_dir first.
DPmoireLite build config.yaml
DPmoireLite collect config.yaml --stage rlx
DPmoireLite collect config.yaml --stage md
DPmoireLite collect config.yaml --stage validation
```

`init-example` copies a self-contained template with `config.yaml`, `input/`,
and `scripts/`. The generated `config.yaml` contains inline comments for every
supported tag.

## Installation In A Conda Environment

Clone the private repository, activate your own conda environment, and install
the package from the repository root:

```bash
git clone https://github.com/weishengless/DPmoire-lite.git
cd DPmoire-lite
conda activate your_env_name
python -m pip install -e .
DPmoireLite --help
```

Use `python -m pip install .` instead of `-e .` if you want a normal installed
copy rather than an editable development install.

## Required Input Files

`input_dir` must contain these structure and INCAR template files:

- `top_layer.poscar`: top monolayer primitive or pre-matched cell.
- `bot_layer.poscar`: bottom monolayer primitive or pre-matched cell.
- `init_INCAR`: INCAR template for the backward-compatible manual init MLFF
  path.
- `init_bottom_INCAR` and `init_top_INCAR`: independent scientific templates
  for `init_mlff_mode: single-job`. Keep species-dependent tags such as
  `MAGMOM` and DFT+U settings explicit in the appropriate file; DPmoire-lite
  does not infer or patch them.
- `rlx_INCAR`: INCAR template for stacking relaxation calculations.
- `MD_INCAR`: INCAR template for bilayer MD calculations.
- `MD_monolayer_INCAR`: INCAR template for optional monolayer MD calculations.
- `val_INCAR`: INCAR template for optional twist validation calculations.

Layer POSCAR files must use slab cells whose in-plane vectors lie in the
Cartesian xy plane and whose c vector is aligned with Cartesian z. DPmoire-lite
rejects tilted slab cells because the spacing modes operate along z.

If an INCAR template contains `LUSE_VDW = T`, `input_dir` must also contain
`vdw_kernel.bindat`; it will be copied into generated calculation folders.

`script_dir` must contain the Slurm script named by `dft_script`. In
`init_mlff_mode: single-job`, that file is an explicit Bash template: Slurm
directives stay before executable content, the synchronous launch function is
declared exactly as `dpmoire_run_vasp() {`, and the launch site is exactly:

```bash
dpmoire_run_vasp # DPMOIRE-LITE:RUN
```

The function must wait for VASP and return the real launcher exit code; do not
background it. Its closing `}` must be on a line by itself, and the marked call
must be the final executable line (only comments or blank lines may follow).
DPmoire-lite replaces only that marked line in a derived copy and never searches
for or rewrites `srun`, `mpirun`, containers, pipelines, or redirections in the
function body.

For automatic `single-job` submission, the template must not request Slurm's
wait mode through `#SBATCH --wait`, `-W`, or a clustered short option containing
`W`; the deprecated `#SLURM -W` spelling and every `hetjob`/`packjob` component
are checked as well. Preflight rejects those directives before writing the work
tree. Because `sbatch` may translate foreign scheduler syntax, automatic mode
also rejects any `#PBS` or `#BSUB` prefix anywhere in the template; use native
`#SBATCH` directives or `submit: false`. These restrictions do not apply to
`submit: false`, where the user owns the later manual `sbatch` call.

`potcar_dir` should point to a VASP POTCAR root. With the default
`potcar_policy: recommend`, DPmoire-lite tries VASP's recommended mapped folder
first, for example `Li_sv` for `Li`, then falls back to the plain element
folder. With `potcar_policy: minimal`, it selects the regular POTCAR candidate
with the smallest `ZVAL` in `potcar_dir`.

## Workflow

`stage: 0` creates the first calculation set:

- `init_mlff/` if `init_mlff: true`; its layout is selected by
  `init_mlff_mode`
- `rlx/<i>_<j>/` folders if `do_relaxation: true`
- `validation/<angle>/` folders if `twist_val: true`

`stage: 1` creates MD folders under `md/`. It checks the relaxation outputs
before writing any MD folder. When `vasp_ml: true`, it also requires
`init_mlff/ML_ABN` and `init_mlff/ML_FFN`, then distributes them as `ML_AB` and
`ML_FF` into each MD folder.

`stage: all` is temporarily unavailable because Slurm terminal-state validation
and failure propagation are not yet reliable. Generate Stage0 and Stage1
separately with `submit: false`, submit them manually, and inspect their outputs
before generating the next stage.

Validation outputs are independent of the MD timeline: stage1 does not consume
validation results, and validation data is collected only when the user
explicitly runs:

```bash
DPmoireLite collect config.yaml --stage validation
```

## Submission Semantics

`submit: false` only generates folders and is the recommended workflow. Submit
the generated folders manually, inspect their outputs, and only then generate
the next stage.

`submit: true` without `--wait` generates folders, submits all jobs requested by
the current stage, and exits. This fire-and-forget mode guarantees only that the
`sbatch` invocation succeeded; it does not guarantee final job success or advance
dependent stages. `n_nodes` throttling and `auto_resub` are unavailable because
the process does not keep polling Slurm.

`submit: true` with `--wait` is temporarily disabled for Stage0 and Stage1, and
`stage: all` is unavailable in every mode. Consequently, `auto_resub` is not a
production-ready feature while submitted wait remains disabled.

The initial MLFF workflow is intentionally explicit:

- `manual` is the default. Stage0 creates the historical single-folder
  `init_mlff` job, and if `submit: true`
  without `--wait`, submits that first init job only. Stage0 still continues to
  generate and optionally submit any enabled relaxation or validation folders.
  After the user finishes preparing `ML_ABN` and `ML_FFN`, stage1 can use those
  files.
- `single-job` is an explicit opt-in. Stage0 transactionally publishes complete
  static inputs in
  `init_mlff/bottom/` and `init_mlff/top/`, using `init_bottom_incar` and
  `init_top_incar`. Each phase receives its own POSCAR, local POTCAR, own-cell
  KPOINTS, rendered INCAR, and submit-script copy, while both INCAR files use the
  workflow-wide cutoff. It also renders `init_mlff/<dft_script>` from the marked
  source template without changing that source. The manifest records both
  script hashes and bounded workflow evidence without POTCAR payloads.
- With `submit: false`, build stops after generation. Inspect the derived script,
  change into `<work_dir>/init_mlff`, and submit it once with
  `sbatch <dft_script>`. With `submit: true` and no `--wait`, DPmoire-lite makes
  exactly one `sbatch` request for that root script, records the returned job ID
  and generated-script hash in `init_mlff/manifest.yaml`, and returns without
  claiming the workflow completed. The generated adapter resolves
  the workflow from Slurm's submit directory, so submitting from that directory
  keeps a relocated workspace portable. In one allocation it runs bottom,
  validates and copies the continuation seed, runs top, validates again, and
  atomically publishes final `init_mlff/ML_ABN` and `ML_FFN`. `DPMOIRE_PHASE` is
  `step1` or `step2` for phase-specific logging. The allocation resources must
  suit both calculations and its walltime must cover their combined runtime.
  Scheduler polling, retries, resume, and cross-job dependencies are not enabled.
  DPmoire-lite removes an inherited `SBATCH_WAIT` from every programmatic
  `sbatch` environment so the documented fire-and-forget path cannot silently
  become a waiting submission.

The build CLI prints `build status=generated` whenever it made no `sbatch`
request, including generation-only work or a stage with no enabled targets. It
prints `build status=submission_requested` only after successful fire-and-forget
`sbatch`; that means only that Slurm accepted the request. A failed request
returns a nonzero exit and leaves bounded `SUBMIT_FAILED` evidence in the init
manifest.
The generated job later prints `init_mlff status=complete` only after both phases
and final seed publication succeed, or `init_mlff status=failed` on a workflow
failure. No scheduler polling is used to bridge these states.

## Collection Semantics

Collection returns a structured aggregate status. The CLI prints one concise
summary line to stderr; the selected result manifest keeps the detailed
per-source diagnostics.

| Exit code | Status | Meaning |
| ---: | --- | --- |
| `0` | `complete` | At least one frame was published and all expected sources completed. |
| `1` | `fatal` | Configuration, provenance, manifest, recovery, or publication failed. |
| `2` | `degraded` | Frames were published, but coverage is partial, skipped, failed, or unknown. |
| `3` | `no_data` | No new frames were accepted or published. |

Every nonzero code is a shell failure. A `no_data` run never creates, deletes,
or replaces an extxyz file; if an output already exists, its bytes are
preserved.

Output files are kept separate:

- `work_dir/rlx_data.extxyz` from `collect --stage rlx`
- `work_dir/MD_data.extxyz` from `collect --stage md`
- `work_dir/valid.extxyz` from `collect --stage validation`

For relaxation data, DPmoire-lite reads the configured OUTCAR series and keeps
one frame every `outcar_collect_freq` ionic steps.

For MD data:

- `seed-aware` is the default. For `vasp_ml: true`, it validates the immutable
  Stage1 seed provenance and excludes that prefix; it never infers the prefix
  from the current `md/ML_AB`.
- Exact seed matching keeps the existing `mlab-seed-v1` identity unchanged. If
  VASP rewrites only numeric values in the trusted seed prefix, the fallback
  requires identical structure and order and applies the fixed, versioned rule
  `abs(a-b) <= 1e-12 * max(1, abs(a), abs(b))` component by component. The
  reference must be raw-hash verified for Manifest v2, or explicitly rebuilt
  from complete legacy `init_mlff/ML_ABN` evidence. Approved VASP equivalence
  alone remains `complete`; missing, partial, or failed source coverage causes
  `degraded`.
- Explicit `--mlff-collect-mode full-dedup` is valid only for MLFF MD. It reads
  every accepted final `ML_ABN`, retains the first exact copy of repeated
  configurations (including one shared seed), and performs more I/O than
  `seed-aware`.
- Using `full-dedup` for relaxation, validation, or non-ML MD is fatal (exit 1).
- If `vasp_ml: false`, DPmoire-lite reads OUTCAR files using
  `outcar_collect_freq`.

For validation data, all OUTCAR ionic steps are collected with frequency 1.

A valid current Manifest v2 is authoritative. Legacy or explicitly compatible
missing-manifest MLFF collection writes `MD_data.collect.yaml`; it never
fabricates or rewrites build provenance. Missing-manifest scanning is available
only with explicit `full-dedup`, and a scan that produces frames is at best
`degraded` because expected-source coverage is unknown.

The result manifest records aggregate exact/VASP-equivalent/mismatch counts at
`collect.dedup.seed_verification` and bounded per-source evidence at
`collect.sources[].seed_verification`. Diagnostics contain hashes, counts,
reference trust, first-mismatch fields, and maximum deltas; they never contain
complete seed configurations. Canonical `mlab-seed-v1` and `mlab-config-v1`
outputs and full-dedup behavior are unchanged.

## Config Tags

Config files use snake_case keys only. Old DPmoire names such as `VASP_ML`,
`K-mesh`, `POTCAR_dir`, `DFT_script`, `ENMAX`, and `OUTCAR_collect_freq` are
rejected.

| Tag | Type | Meaning |
| --- | --- | --- |
| `dft_script` | string | Slurm submit script filename. Manual mode copies it unchanged into calculation folders. Single-job mode additionally requires the documented `dpmoire_run_vasp`/marker contract and renders an init-specific script with the same basename. |
| `potcar_dir` | path | Root directory containing POTCAR subfolders. |
| `potcar_policy` | `recommend` or `minimal` | POTCAR selection policy. `recommend` uses VASP recommended element-folder mapping and is the default. `minimal` scans regular POTCAR variants in `potcar_dir` and uses the unique lowest-`ZVAL` candidate; a tie fails preflight as ambiguous. |
| `script_dir` | path | Directory containing prepared submit scripts. |
| `input_dir` | path | Directory containing layer POSCAR files, INCAR templates, and optional `vdw_kernel.bindat`. |
| `work_dir` | path | Root output directory for generated stages, manifests, backups, and collected datasets. |
| `n_nodes` | positive int | Reserved for DPmoire-lite wait-mode throttling. Submitted `--wait` is temporarily disabled; non-wait mode submits requested jobs and exits without throttling. |
| `stage` | `0`, `1`, or `all` | Build stage. `0` generates init, relaxation, and validation folders. `1` generates MD folders from completed relaxation outputs. `all` is temporarily unavailable. |
| `submit` | bool | If `false`, only generate folders. If `true`, submit generated folders with Slurm. |
| `auto_resub` | bool | Reserved for submitted wait workflows and not production-ready while those workflows are disabled. Ignored in non-wait mode. |
| `vasp_ml` | bool | Use VASP MLFF workflow for MD. Stage1 distributes `init_mlff/ML_ABN` and `init_mlff/ML_FFN`; MD collection reads `ML_ABN` instead of OUTCAR. |
| `outcar_collect_freq` | positive int | OUTCAR sampling stride for relaxation and non-ML MD collection. Validation always uses stride 1. VASP-ML MD collection reads `ML_ABN`, so this tag does not affect that path. |
| `do_relaxation` | bool | In stage0, generate relaxation folders under `rlx/`. |
| `init_mlff` | bool | In stage0, generate the initial `init_mlff/` folder. |
| `init_mlff_mode` | `manual` or `single-job` | Init layout. Defaults to `manual`. `single-job` creates separate bottom/top workspaces and one derived root script. With `submit: false`, inspect and submit that script once manually; with fire-and-forget `submit: true`, DPmoire-lite requests that one root submission and records its job ID without waiting for completion. |
| `init_bottom_incar` | relative path | Bottom-phase INCAR template under `input_dir`; required by `single-job`. |
| `init_top_incar` | relative path | Top-phase INCAR template under `input_dir`; required by `single-job`. |
| `sc_rlx` | bool | If `true`, relax supercell stacking structures. If `false`, relax primitive glide structures and expand the converged CONTCAR during stage1. |
| `preserve_grid_shift_md` | bool | Stage1 defaults to clearing all constraints. If `true`, preserve only DPmoire-lite grid-shift anchors; `F F T` means fixed x/y and movable z. |
| `array_submission` | bool | If `true`, generate one Slurm array script per stage root (`rlx/array_<dft_script>`, `md/array_<dft_script>`; `init_mlff` and `validation` excluded). The generated script wraps your `dft_script` template verbatim, injects `#SBATCH --array=0-N`, appends `-o %x.%A.%a.out` / `-e %x.%A.%a.err` overrides so array tasks cannot overwrite each other's output, and maps `SLURM_ARRAY_TASK_ID` to each calculation folder. Submit it once with `sbatch`; per-folder original scripts stay untouched. Generation only — nothing is submitted automatically. Defaults to `false`. |
| `array_max_concurrent` | non-negative int | With `array_submission` enabled, a value greater than zero injects `--array=0-N%K` to cap concurrently running tasks. Defaults to `0` (no cap). |
| `n_sectors` | int or `[nx, ny]` | Stacking-shift grid. `9` means `[9, 9]`; `[9, 8]` creates a rectangular grid. |
| `sc` | int or `[sx, sy]` | Supercell expansion used for MD and, when `sc_rlx: true`, relaxation. `2` means `[2, 2]`. |
| `d` | number | Distance value interpreted according to `d_mode`. |
| `d_mode` | `surface_gap` or `reference_plane_gap` | `surface_gap` sets `min_z(top) - max_z(bot) = d`; `reference_plane_gap` sets the selected reference-plane mean-z distance to `d`. Defaults to `surface_gap`. |
| `d_reference` | mapping, optional | Reference atom selectors for `reference_plane_gap`, for example `{top: [Mo], bot: [Mo]}` for the bundled MoTe2 example. Omit or use `all` to average all atoms in that layer. |
| `grid_shift_anchor` | element or `{top, bot}` mapping, optional | Picks the two anti-slide anchor atoms by element, one per layer, written as `F F T`. A bare element applies to both layers; a mapping sets each layer separately, for example `{top: Mo, bot: Mo}` for the bundled MoTe2 example. The element must exist in that layer and is validated before any directory is created. Omit for the default: the first atom of each layer in sorted order. Add `selection: nearest_pair` to pin the closest-approaching pair of the selected elements instead of the first match (ties break lexicographically: lowest top-layer atom index, then lowest bottom-layer atom index); the bare keyword `grid_shift_anchor: nearest_pair` pins the globally closest top-bottom atom pair without element filtering. |
| `k_mesh` | int | KPOINTS target. DPmoire-lite writes a Gamma mesh from the generated POSCAR in-plane cell lengths. |
| `encut_factor` | number | Before generation, DPmoire-lite resolves the selected POTCAR variants for the union of top- and bottom-layer elements. Every init, relaxation, bilayer/monolayer MD, and validation INCAR uses `encut_factor * max(ENMAX)` from that workflow-wide set. Each generated POTCAR still contains only the elements in its local POSCAR, in local POSCAR order. |
| `r_cut` | number | Value written to `ML_RCUT1` and `ML_RCUT2`. If negative, DPmoire-lite uses an automatic value based on the largest input-layer in-plane lattice length and `d`. |
| `symm_reduce` | bool | Reduce stacking shifts by symmetry using `pymatgen`/`spglib` and write `sym_reduced_stackings.txt`. |
| `twist_val` | bool | Generate twist validation calculation folders in stage0. Validation is not part of the MD timeline. |
| `min_val_n` | int | Minimum `n` used by the twist validation search. Used only when `twist_val: true`. |
| `max_val_n` | int | Maximum `n` used by the twist validation search. Used only when `twist_val: true`. |
| `include_monolayer_md` | bool | In stage1, also generate `md/top_layer` and `md/bot_layer` monolayer MD folders. |
| `outcar_patterns` | list of regex strings, optional | Regex list used to discover OUTCAR series during collection. Defaults, in priority order, to `OUTCAR<number>`, `OUT<number>`, `out<number>`, then unnumbered `OUTCAR`. |

## One-shot Stage Policy

If any target stage exists, including an empty directory, DPmoire-lite stops before modifying any file. It never moves, backs up, overwrites, or rebuilds a stage in place. Explicitly delete the complete conflicting stage, then rerun the build.

## CLI

```bash
DPmoireLite init-example my_case
DPmoireLite build config.yaml
DPmoireLite collect config.yaml --stage rlx
DPmoireLite collect config.yaml --stage md
DPmoireLite collect config.yaml --stage md --mlff-collect-mode full-dedup
DPmoireLite collect config.yaml --stage validation
```
