# DPmoire-lite

Chinese documentation is available in [README_CH.md](README_CH.md). A detailed
workflow guide is available in [workflow.md](workflow.md), with the Chinese
version in [workflow_CH.md](workflow_CH.md).

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
- `init_INCAR`: INCAR template for the initial single-layer MLFF calculation.
- `rlx_INCAR`: INCAR template for stacking relaxation calculations.
- `MD_INCAR`: INCAR template for bilayer MD calculations.
- `MD_monolayer_INCAR`: INCAR template for optional monolayer MD calculations.
- `val_INCAR`: INCAR template for optional twist validation calculations.

Layer POSCAR files must use slab cells whose in-plane vectors lie in the
Cartesian xy plane and whose c vector is aligned with Cartesian z. DPmoire-lite
rejects tilted slab cells because the spacing modes operate along z.

If an INCAR template contains `LUSE_VDW = T`, `input_dir` must also contain
`vdw_kernel.bindat`; it will be copied into generated calculation folders.

`script_dir` must contain the Slurm script named by `dft_script`.

`potcar_dir` should point to a VASP POTCAR root. With the default
`potcar_policy: recommend`, DPmoire-lite tries VASP's recommended mapped folder
first, for example `Li_sv` for `Li`, then falls back to the plain element
folder. With `potcar_policy: minimal`, it selects the regular POTCAR candidate
with the smallest `ZVAL` in `potcar_dir`.

## Workflow

`stage: 0` creates the first calculation set:

- `init_mlff/` if `init_mlff: true`
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

- Manual mode: stage0 creates the first `init_mlff` job, and if `submit: true`
  without `--wait`, submits that first init job only. Stage0 still continues to
  generate and optionally submit any enabled relaxation or validation folders.
  After the user finishes preparing `ML_ABN` and `ML_FFN`, stage1 can use those
  files.
- The second init MLFF step and the Stage0-to-Stage1 transition must currently be
  completed manually after inspecting the preceding outputs.

## Collection Semantics

Collection is permissive. Missing or unreadable sources are skipped where
possible, and the stage manifest records collected frame counts plus skipped or
failed records.

Output files are kept separate:

- `work_dir/rlx_data.extxyz` from `collect --stage rlx`
- `work_dir/MD_data.extxyz` from `collect --stage md`
- `work_dir/valid.extxyz` from `collect --stage validation`

For relaxation data, DPmoire-lite reads the configured OUTCAR series and keeps
one frame every `outcar_collect_freq` ionic steps.

For MD data:

- If `vasp_ml: true`, DPmoire-lite reads `ML_ABN` and collects only the ab initio
  configurations. If `ML_AB` exists, already-seen configurations are skipped.
- If `vasp_ml: false`, DPmoire-lite reads OUTCAR files using
  `outcar_collect_freq`.

For validation data, all OUTCAR ionic steps are collected with frequency 1.

## Config Tags

Config files use snake_case keys only. Old DPmoire names such as `VASP_ML`,
`K-mesh`, `POTCAR_dir`, `DFT_script`, `ENMAX`, and `OUTCAR_collect_freq` are
rejected.

| Tag | Type | Meaning |
| --- | --- | --- |
| `dft_script` | string | Slurm submit script filename. The file is copied from `script_dir` into each generated calculation folder and submitted with `sbatch`. |
| `potcar_dir` | path | Root directory containing POTCAR subfolders. |
| `potcar_policy` | `recommend` or `minimal` | POTCAR selection policy. `recommend` uses VASP recommended element-folder mapping and is the default. `minimal` scans regular POTCAR variants in `potcar_dir` and uses the lowest-`ZVAL` candidate. |
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
| `sc_rlx` | bool | If `true`, relax supercell stacking structures. If `false`, relax primitive glide structures and expand the converged CONTCAR during stage1. |
| `n_sectors` | int or `[nx, ny]` | Stacking-shift grid. `9` means `[9, 9]`; `[9, 8]` creates a rectangular grid. |
| `sc` | int or `[sx, sy]` | Supercell expansion used for MD and, when `sc_rlx: true`, relaxation. `2` means `[2, 2]`. |
| `d` | number | Distance value interpreted according to `d_mode`. |
| `d_mode` | `surface_gap` or `reference_plane_gap` | `surface_gap` sets `min_z(top) - max_z(bot) = d`; `reference_plane_gap` sets the selected reference-plane mean-z distance to `d`. Defaults to `surface_gap`. |
| `d_reference` | mapping, optional | Reference atom selectors for `reference_plane_gap`, for example `{top: [Mo], bot: [Mo]}` for the bundled MoTe2 example. Omit or use `all` to average all atoms in that layer. |
| `k_mesh` | int | KPOINTS target. DPmoire-lite writes a Gamma mesh from the generated POSCAR in-plane cell lengths. |
| `encut_factor` | number | INCAR `ENCUT` is rendered as `encut_factor * max(POTCAR ENMAX)` for the selected elements. |
| `r_cut` | number | Value written to `ML_RCUT1` and `ML_RCUT2`. If negative, DPmoire-lite uses an automatic value based on the largest input-layer in-plane lattice length and `d`. |
| `symm_reduce` | bool | Reduce stacking shifts by symmetry using `pymatgen`/`spglib` and write `sym_reduced_stackings.txt`. |
| `twist_val` | bool | Generate twist validation calculation folders in stage0. Validation is not part of the MD timeline. |
| `min_val_n` | int | Minimum `n` used by the twist validation search. Used only when `twist_val: true`. |
| `max_val_n` | int | Maximum `n` used by the twist validation search. Used only when `twist_val: true`. |
| `include_monolayer_md` | bool | In stage1, also generate `md/top_layer` and `md/bot_layer` monolayer MD folders. |
| `outcar_patterns` | list of regex strings, optional | Regex list used to discover OUTCAR series during collection. Defaults to `OUTCAR`, `OUTCAR<number>`, `OUT<number>`, and `out<number>`. |

## Directory Replacement Policy

When a generated child directory already exists, DPmoire-lite backs up that
child directory with a timestamp suffix and regenerates it. This is scoped to
the stage child being regenerated, not to the whole `work_dir`.

## CLI

```bash
DPmoireLite init-example my_case
DPmoireLite build config.yaml
DPmoireLite collect config.yaml --stage rlx
DPmoireLite collect config.yaml --stage md
DPmoireLite collect config.yaml --stage validation
```
