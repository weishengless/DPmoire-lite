# DPmoire-lite Workflow

This document describes the intended end-to-end workflow for generating VASP
calculation folders and collecting datasets with DPmoire-lite. It complements
the tag-level reference in `README.md`.

## 1. Prepare A Case

Start by copying the bundled example:

```bash
DPmoireLite init-example my_case
cd my_case
```

The current example uses:

- `config.yaml` as the single workflow configuration file.
- `scripts/sub` as the Slurm submission script, matching `dft_script: sub`.
- `input/top_layer.poscar` and `input/bot_layer.poscar` as the input monolayers.
- `input/*_INCAR` templates for each calculation type.

Before running the workflow, edit at least:

- `potcar_dir`: point it to the real VASP POTCAR root on your cluster.
- `script_dir` and `dft_script`: ensure the submit script exists and matches
  your cluster environment.
- `input/top_layer.poscar` and `input/bot_layer.poscar`: replace them with the
  target monolayers or pre-matched cells.
- INCAR templates: adjust functional, MD settings, MLFF tags, resources, and
  precision for the target calculation.
- `work_dir`: choose where generated calculation folders and datasets will be
  written.

The example also contains a deprecated `username` entry. DPmoire-lite does not
use it; it is kept only as a harmless note for older user workflows.

## 2. Stage0: Generate Initial Calculations

Run stage0 when you need to create the first calculation set:

```bash
DPmoireLite build config.yaml
```

With the default non-submit mode, DPmoire-lite only writes folders. It does not
run VASP.

Stage0 can generate three independent groups, controlled by config tags:

- `init_mlff/` when `init_mlff: true`.
- `rlx/<i>_<j>/` stacking relaxation folders when `do_relaxation: true`.
- `validation/<angle>/` twist validation folders when `twist_val: true`.

Each generated folder receives:

- `POSCAR`
- rendered `INCAR`
- `KPOINTS`
- `POTCAR`
- the configured submit script
- optional `vdw_kernel.bindat` if required by the INCAR template

If a target child directory already exists, DPmoire-lite backs up that child
directory with a timestamp suffix and regenerates it. It does not move the whole
`work_dir`.

## 3. Init MLFF Paths

The initial MLFF folder provides the seed `ML_ABN` and `ML_FFN` used by VASP-ML
MD calculations.

Manual path:

1. Set `stage: 0`, `init_mlff: true`, and usually `submit: false`.
2. Run `DPmoireLite build config.yaml`.
3. Submit or run `init_mlff/` manually on the desired cluster.
4. Ensure `init_mlff/ML_ABN` and `init_mlff/ML_FFN` exist before stage1.

Non-wait submit path:

```bash
DPmoireLite build config.yaml
```

with `submit: true` submits the first init MLFF job only for the init workflow.
The second init MLFF step is left to the user. Stage0 still continues to
generate and, when enabled, submit relaxation and validation folders from the
same configuration.

Wait path:

```bash
DPmoireLite build config.yaml --wait
```

with `submit: true` waits for the first init MLFF job, renames `ML_ABN` to
`ML_AB` and `ML_FFN` to `ML_FF`, replaces `POSCAR` with the top-layer supercell,
submits the second init MLFF job, and waits for that second job to finish.

## 4. Relaxation Grid

Relaxation structures are controlled by `n_sectors`, `sc`, `sc_rlx`, and
`symm_reduce`.

`n_sectors` defines the stacking-shift grid:

```yaml
n_sectors: 9       # [9, 9]
n_sectors: [9, 8]  # rectangular grid
```

`sc` defines the supercell expansion:

```yaml
sc: 2       # [2, 2]
sc: [3, 2]  # rectangular supercell
```

If `sc_rlx: true`, relaxation folders contain supercell stacking structures.
If `sc_rlx: false`, relaxation folders contain primitive glide structures; the
converged `CONTCAR` is expanded to `sc` during stage1.

If `symm_reduce: true`, DPmoire-lite uses `pymatgen`/`spglib` to reduce
symmetry-equivalent stackings and writes `sym_reduced_stackings.txt`.

## 5. Submitting Stage0

Set `submit: true` to submit generated folders with Slurm.

Without `--wait`, DPmoire-lite submits every folder requested by the current
stage and exits:

```bash
DPmoireLite build config.yaml
```

In this mode, `n_nodes` and `auto_resub` cannot be enforced because the process
does not keep polling Slurm.

With `--wait`, DPmoire-lite keeps polling Slurm:

```bash
DPmoireLite build config.yaml --wait
```

In this mode:

- at most `n_nodes` jobs are active in the DPmoire-lite polling loop;
- `auto_resub: true` resubmits failed jobs once per calculation directory;
- stage0 waits for init, relaxation, and validation jobs that it submits.

## 6. Stage1: Generate MD Calculations

After relaxation jobs have completed, set:

```yaml
stage: 1
```

Then run:

```bash
DPmoireLite build config.yaml
```

Stage1 is intentionally strict. Before writing MD folders, it checks all
required relaxation sources:

- each required `rlx/<i>_<j>/OUTCAR` must exist and contain VASP's convergence
  phrase;
- each required `rlx/<i>_<j>/CONTCAR` must exist and be readable by ASE;
- if `vasp_ml: true`, `init_mlff/ML_ABN` and `init_mlff/ML_FFN` must exist.

If any preflight check fails, stage1 reports all detected failures and does not
generate a partial MD set.

For each accepted relaxation source, stage1 writes `md/<i>_<j>/POSCAR` from the
relaxed `CONTCAR`. The output POSCAR is rewritten through ASE so velocity blocks
from VASP relaxation outputs are not carried into MD. This allows VASP to
initialize MD velocities from the target temperature.

When `vasp_ml: true`, stage1 copies:

- `init_mlff/ML_ABN` to each MD folder as `ML_AB`
- `init_mlff/ML_FFN` to each MD folder as `ML_FF`

If `include_monolayer_md: true`, stage1 also generates:

- `md/top_layer`
- `md/bot_layer`

## 7. Automated `stage: all`

`stage: all` runs the dependency chain automatically. It requires:

```yaml
stage: all
submit: true
```

and must be launched with:

```bash
DPmoireLite build config.yaml --wait
```

The workflow is:

1. generate and submit `init_mlff/`;
2. wait for the first init MLFF step;
3. prepare, submit, and wait for the second init MLFF step;
4. generate and submit relaxation folders;
5. wait for relaxation folders to finish;
6. generate, submit, and wait for validation folders if `twist_val: true`;
7. generate, submit, and wait for MD folders.

In `stage: all`, `n_nodes` throttling and `auto_resub` apply to every waited
submission in the automatic chain: init MLFF, relaxation, validation, and final
MD. Validation outputs are not inputs to stage1, but validation jobs are still
waited and throttled when `twist_val: true`.

Use `stage: all` only when the same machine and scheduler can run the dependency
chain continuously. For multi-cluster or manually staged workflows, use
`stage: 0` and `stage: 1` separately.

## 8. Validation Timeline

Validation is independent from the relaxation-to-MD timeline.

When `twist_val: true`, stage0 generates twist validation folders under
`validation/`. These calculations are not inputs to stage1, and their completion
does not control MD generation.

Collect validation data explicitly:

```bash
DPmoireLite collect config.yaml --stage validation
```

Validation collection reads OUTCAR files with stride 1, regardless of
`outcar_collect_freq`.

## 9. Collecting Datasets

Run collection only after the corresponding VASP calculations have finished.

Relaxation dataset:

```bash
DPmoireLite collect config.yaml --stage rlx
```

Output:

```text
work_dir/rlx_data.extxyz
```

MD dataset:

```bash
DPmoireLite collect config.yaml --stage md
```

Output:

```text
work_dir/MD_data.extxyz
```

Validation dataset:

```bash
DPmoireLite collect config.yaml --stage validation
```

Output:

```text
work_dir/valid.extxyz
```

Collection is permissive. Missing or unreadable sources are skipped when
possible, and the manifest records collected frame counts, skipped paths, and
failed paths.

For `vasp_ml: true` MD, DPmoire-lite reads `ML_ABN` and collects only ab initio
frames. If `ML_AB` exists, already-seen configurations are skipped so repeated
collection does not duplicate the seed data.

For relaxation and `vasp_ml: false` MD, DPmoire-lite reads OUTCAR series. The
default OUTCAR patterns are:

```yaml
outcar_patterns:
  - '^OUTCAR$'
  - '^OUTCAR\d+$'
  - '^OUT\d+$'
  - '^out\d+$'
```

This covers common restarted relaxation histories such as `OUTCAR0`, `OUT1`,
and `out2`, while avoiding broad names such as `OUTCAR.bad`.

## 10. Typical Manual Workflow

For a manually staged cluster workflow:

```bash
DPmoireLite init-example my_case
cd my_case

# Edit config.yaml and inputs.
# stage: 0, submit: false
DPmoireLite build config.yaml

# Manually submit and finish init_mlff and rlx calculations.
# Ensure init_mlff/ML_ABN, init_mlff/ML_FFN, rlx/*/OUTCAR, and rlx/*/CONTCAR exist.

# stage: 1, submit: false
DPmoireLite build config.yaml

# Manually submit and finish MD calculations.
DPmoireLite collect config.yaml --stage rlx
DPmoireLite collect config.yaml --stage md
```

Run validation separately only when needed:

```bash
DPmoireLite collect config.yaml --stage validation
```
