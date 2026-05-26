# DPmoire-lite Design Spec

Date: 2026-05-26

## Goal

Create a new clean framework, `DPmoire-lite`, for generating VASP calculation folders and collecting VASP-derived datasets as `extxyz`. The project will reuse the useful algorithms from the current `DPmoire-master` codebase, but it will not carry over the training, calculator, or NequIP orchestration parts.

The new project root will be:

```text
E:\codespace\MLFF\DPmoire-lite
```

The Python package name will be `dpmoire_lite`, and the command line entry point will be:

```bash
DPmoireLite
```

## Non-goals

- Do not train NequIP, Allegro, MACE, DeepMD, or any other MLFF model.
- Do not merge `rlx_data.extxyz`, `MD_data.extxyz`, and `valid.extxyz` into a single dataset automatically.
- Do not retain the old `wosub` config interface.
- Do not support old mixed-case config fields such as `VASP_ML`, `K-mesh`, `POTCAR_dir`, `DFT_script`, or `ENMAX`.
- Do not expose a production CLI flag that skips strict stage1 checks. Test fixtures may bypass checks internally.

## Recommended Architecture

Use a clean new project and migrate only the required algorithms from `DPmoire-master`.

```text
DPmoire-lite/
  pyproject.toml
  README.md
  example/
    config.yaml
    input/
    scripts/
  src/dpmoire_lite/
    cli.py
    config.py
    manifest.py
    paths.py
    structures.py
    inputs.py
    slurm.py
    build.py
    collect.py
    dataset.py
    outcar.py
  tests/
```

Module responsibilities:

- `config.py`: read and validate snake_case YAML config; normalize `n_sectors` and `sc` to `(x, y)`.
- `manifest.py`: read/write per-stage `manifest.yaml` files using paths relative to `work_dir`.
- `paths.py`: centralize stage directory layout and backup naming.
- `structures.py`: build bilayers, shifted stackings, symmetry-reduced stackings, validation twist structures, primitive-to-supercell conversion.
- `inputs.py`: generate `POSCAR`, `INCAR`, `KPOINTS`, `POTCAR`, submit scripts, vdW kernel copies, and MLFF file distribution.
- `slurm.py`: handle `sbatch`, `sacct`, `n_nodes` throttling, waiting, and `auto_resub`.
- `build.py`: orchestrate stage0, stage1, and stage all.
- `collect.py`: collect rlx, MD, or validation datasets and update manifests.
- `dataset.py`: parse VASP `ML_ABN` and save `extxyz`.
- `outcar.py`: discover OUTCAR series and load frames through ASE.

## Config Schema

All config fields are snake_case. Old DPmoire config names are not accepted.

Example:

```yaml
dft_script: DFT_script.sh
potcar_dir: /path/to/potpaw_PBE
script_dir: ./scripts
input_dir: ./input
work_dir: .
n_nodes: 81

stage: 0
submit: false
auto_resub: false

vasp_ml: true
outcar_collect_freq: 8
do_relaxation: true
init_mlff: true
sc_rlx: true
n_sectors: [9, 8]
sc: [2, 2]
d: 6.3
k_mesh: 40
encut_factor: 1.6
r_cut: -1
symm_reduce: true

twist_val: true
min_val_n: 4
max_val_n: 5

include_monolayer_md: true
outcar_patterns:
  - "^OUTCAR$"
  - "^OUTCAR\\d+$"
  - "^OUT\\d+$"
  - "^out\\d+$"
```

`n_sectors` accepts either an integer or a two-element list:

```yaml
n_sectors: 9       # normalized to [9, 9]
n_sectors: [9, 8]
```

`sc` accepts either an integer or a two-element list:

```yaml
sc: 2              # normalized to [2, 2]
sc: [2, 3]
```

## Stage Semantics

### Stage 0

`stage: 0` generates the stage0-like calculation folders enabled by config flags:

- `init_mlff: true` generates `work_dir/init_mlff`.
- `do_relaxation: true` generates `work_dir/rlx/<i>_<j>`.
- `twist_val: true` generates `work_dir/validation/<angle>`.

These are independent VASP calculation folders. Validation is not part of the rlx-to-MD dependency chain.

### Stage 1

`stage: 1` generates:

- `work_dir/md/<i>_<j>`
- `work_dir/md/top_layer` and `work_dir/md/bot_layer` when `include_monolayer_md: true`

Stage1 is strict in production. It must verify:

- Every required rlx directory has a readable `CONTCAR`.
- Every required rlx `OUTCAR` contains `reached required accuracy - stopping structural energy minimisation`.
- When `vasp_ml: true`, `work_dir/init_mlff/ML_ABN` and `work_dir/init_mlff/ML_FFN` exist.

If any check fails, stage1 stops and reports the failing directories.

### Stage All

`stage: all` is allowed only with:

```bash
DPmoireLite build config.yaml --wait
```

and `submit: true` in config.

It generates and submits stage0, waits for the main dependency chain (`init_mlff` and `rlx`) to finish, then generates and submits stage1. Validation jobs may be submitted, but they do not block stage1.

## Directory Layout

Use separate directories for each stage:

```text
work_dir/
  init_mlff/
  rlx/
    0_0/
    0_1/
  md/
    0_0/
    0_1/
    top_layer/
    bot_layer/
  validation/
    7.34/
    6.08/
  backups/
    rlx/
      0_0_YYYYmmdd-HHMMSS/
    md/
      0_0_YYYYmmdd-HHMMSS/
```

If a target calculation directory already exists during build, back up that single directory to:

```text
work_dir/backups/<stage>/<name>_YYYYmmdd-HHMMSS
```

Then regenerate the target directory. Do not back up an entire stage unless each child directory individually conflicts.

## Manifests

Each stage writes its own manifest:

```text
work_dir/init_mlff/manifest.yaml
work_dir/rlx/manifest.yaml
work_dir/md/manifest.yaml
work_dir/validation/manifest.yaml
```

Manifest paths are relative to `work_dir` for portability across clusters.

Manifest content should include:

- stage name
- generated timestamp
- relevant config summary
- actual stackings or validation angles
- generated directories
- backup directories
- Slurm job IDs and statuses
- collect frame counts
- skipped and failed directories with reasons

Collect commands prefer the corresponding manifest to determine actual directories. If no manifest exists, collect falls back to deriving stackings or angles from config.

## Structure Generation

For `n_sectors: [nx, ny]`, stackings are:

```text
i = 0..nx-1
j = 0..ny-1
shift = i / nx * a + j / ny * b
directory = <i>_<j>
```

When `symm_reduce: true`, use the existing pymatgen/spglib-based structure matching strategy to reduce the actual stacking list. `pymatgen` and `spglib` are default dependencies, not optional extras.

`sc_rlx: true`:

- rlx uses `sc_x x sc_y` bilayer supercells.

`sc_rlx: false`:

- rlx uses primitive glide structures.
- stage1 reads each rlx `CONTCAR`, expands it to `sc_x x sc_y`, and writes MD `POSCAR`.

Stage1 must write MD `POSCAR` via ASE read/write from `CONTCAR`, not by direct file copy. This strips the velocity block that VASP appends to `CONTCAR`, so MD velocity initialization is not polluted by zero velocities from relaxation.

When `include_monolayer_md: true`, stage1 expands `input/top_layer.poscar` and `input/bot_layer.poscar` to `sc_x x sc_y` and creates `md/top_layer` and `md/bot_layer`.

## VASP Input Generation

Required input template filenames stay fixed:

- `init_INCAR`
- `rlx_INCAR`
- `MD_INCAR`
- `MD_monolayer_INCAR`
- `val_INCAR`
- `top_layer.poscar`
- `bot_layer.poscar`
- `vdw_kernel.bindat` only when required by the INCAR template

### KPOINTS

Keep the old `k_i * a_i ~= k_mesh` rule.

- Primitive rlx uses `k_scale = (1, 1)`.
- Supercell rlx, MD, monolayer MD, and init MLFF use `k_scale = (sc_x, sc_y)`.

### INCAR

Generated INCAR should replace:

- `ENCUT = encut_factor * max(POTCAR ENMAX)`
- `ML_RCUT1`
- `ML_RCUT2`
- `LANGEVIN_GAMMA`

If `r_cut >= 0`, use that for `ML_RCUT1` and `ML_RCUT2`; otherwise estimate from the structure as in the old DPmoire logic.

Do not auto-edit `ML_ISTART = 1` for init MLFF step2. Newer VASP reads `ML_AB` and `ML_FF` automatically when `ML_LMLFF = T` is set by the user's INCAR template.

### POTCAR

Use a strict resolver:

1. official/recommended VASP mapping from the current improved DPmoire logic
2. exact element directory

Do not fall back to arbitrary `_sv`, `_pv`, `_d`, `_3`, or `_2` guessing.

### vdW Kernel

Scan the relevant INCAR template. If it requires nonlocal vdW kernel support, require `input_dir/vdw_kernel.bindat` and copy it. If not required, do not enforce it.

## Init MLFF Workflow

Init MLFF keeps the old scientific intent: use single-layer MLFF data to seed bilayer VASP ML-AIMD.

Layer order:

1. `bot_layer.poscar`
2. `top_layer.poscar`

Both layers are expanded to `sc_x x sc_y` for init MLFF.

`submit: false`:

- stage0 generates only the first init MLFF folder using `bot_layer.poscar`.
- The user manually handles the second calculation outside the framework.

`submit: true` without `--wait`:

- Submit only the first init MLFF job and exit.
- Do not prepare or submit the second step.

`submit: true --wait`:

- Submit step1 with `bot_layer.poscar`.
- Wait for step1.
- If failed, apply `auto_resub` consistently with other Slurm jobs.
- Rename `ML_ABN -> ML_AB` and `ML_FFN -> ML_FF`.
- Replace `POSCAR` with the `top_layer.poscar` supercell.
- Submit step2 and wait.
- Keep final `ML_ABN` and `ML_FFN` as stage1 sources.

Stage1 distributes final `work_dir/init_mlff/ML_ABN` and `ML_FFN` to every MD directory as `ML_AB` and `ML_FF`.

## Slurm Behavior

`submit: false`:

- Generate folders only.

`submit: true`:

- Submit jobs for the requested stage and exit by default.

`submit: true --wait`:

- Submit and wait for completion.
- Use `auto_resub` to decide whether failed or canceled jobs should be resubmitted.

`n_nodes` limits how many jobs can be pending/running at the same time.

## CLI

First version:

```bash
DPmoireLite build config.yaml [--wait]
DPmoireLite collect config.yaml --stage rlx|md|validation
DPmoireLite init-example <target_dir>
```

`build` reads `stage` and `submit` from config.

`collect` explicitly collects one stage.

`init-example` copies the project `example/` template to a target directory.

## Collect Rules

Output files:

```text
work_dir/rlx_data.extxyz
work_dir/MD_data.extxyz
work_dir/valid.extxyz
```

Do not automatically create merged `data.extxyz`.

### OUTCAR Series

Any collect source that uses OUTCAR should discover an OUTCAR series. Defaults:

```yaml
outcar_patterns:
  - "^OUTCAR$"
  - "^OUTCAR\\d+$"
  - "^OUT\\d+$"
  - "^out\\d+$"
```

Users may override these regex patterns in config. Matched files are read in modification-time order from oldest to newest.

### Relaxation Collect

- Source: `work_dir/rlx/<i>_<j>` OUTCAR series.
- Sampling: every `outcar_collect_freq` ionic steps.
- Output: `work_dir/rlx_data.extxyz`.

### MD Collect

When `vasp_ml: true`:

- Source: `ML_ABN` only.
- This collects only ab initio configurations chosen by VASP MLFF, not every MD step.
- If an initial `ML_AB` exists, read its config count and skip the duplicated prefix in each `ML_ABN`.

When `vasp_ml: false`:

- Source: OUTCAR series.
- Sampling: every `outcar_collect_freq` ionic steps.

When `include_monolayer_md: true`, include `md/top_layer` and `md/bot_layer` in `MD_data.extxyz`.

### Validation Collect

Validation is collected only when the user explicitly runs:

```bash
DPmoireLite collect config.yaml --stage validation
```

- Source: validation OUTCAR series.
- Sampling: all ionic steps.
- Output: `work_dir/valid.extxyz`.

### Collect Error Handling

Collect is permissive:

- Missing files are skipped.
- OUTCAR parse errors are skipped.
- ML_ABN parse errors are skipped.
- Other directories continue processing.

Skipped or failed directories and their reasons are written back to the stage manifest.

## Testing Strategy

Use unit tests for:

- config schema and snake_case validation
- `n_sectors` integer/list normalization
- `sc` integer/list normalization
- OUTCAR pattern matching and mtime ordering
- configurable OUTCAR regex
- ASE read/write of `CONTCAR` to `POSCAR` without preserving the velocity block
- per-directory timestamp backup behavior
- stage1 dry-run using internal test bypasses for strict production checks
- fake `CONTCAR`, `ML_ABN`, and `ML_FFN` generation for stage1 tests
- collect behavior using copied sample data

The local machine does not need VASP. Tests should not run VASP.

For collect parser tests, copy sample files from:

```text
E:\codespace\MLFF\03_constrained_shear_scan
```

Available samples include:

- subdirectory `OUTCAR` files
- top-level `ML_ABN`
- top-level `ML_FFN`

Tests should copy these files into temporary directories and never modify the sample directory.

## Dependencies and Packaging

Use `pyproject.toml`.

Default dependencies:

- `numpy`
- `ase`
- `pyyaml`
- `pymatgen`
- `spglib`

Expose console script:

```toml
DPmoireLite = "dpmoire_lite.cli:main"
```

Initialize a new git repository in `E:\codespace\MLFF\DPmoire-lite`.

## Documentation

`README.md` should be bilingual Chinese/English.

`example/config.yaml` comments should be English.

Documentation must explain:

- stage0 and stage1 responsibilities
- manual folder generation workflow
- `submit: true` vs `submit: true --wait`
- validation as an independent timeline
- strict stage1 checks
- permissive collect behavior
- rectangular `n_sectors`
- rectangular `sc`
- init MLFF manual and automatic workflows

## Acceptance Criteria

- `DPmoireLite --help` runs.
- `DPmoireLite init-example <tmp>` creates a usable template.
- `DPmoireLite build example/config.yaml` can generate stage0 folders from mock/example inputs.
- Stage1 can generate MD folders and distribute MLFF files in tests.
- `DPmoireLite collect config.yaml --stage rlx` can produce `rlx_data.extxyz` from fixtures.
- `DPmoireLite collect config.yaml --stage md` can produce `MD_data.extxyz` from fixtures.
- `DPmoireLite collect config.yaml --stage validation` can produce `valid.extxyz` from fixtures.
- Unit tests cover the core config, path, backup, OUTCAR, CONTCAR, stage1, and collect rules above.
