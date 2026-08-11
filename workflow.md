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

- `init_mlff/` when `init_mlff: true`; `init_mlff_mode` selects the legacy
  single-folder or two-phase static layout.
- `rlx/<i>_<j>/` stacking relaxation folders when `do_relaxation: true`.
- `validation/<angle>/` twist validation folders when `twist_val: true`.

Each generated folder receives:

- `POSCAR`
- rendered `INCAR`
- `KPOINTS`
- `POTCAR`
- the configured submit script
- optional `vdw_kernel.bindat` if required by the INCAR template

Before writing any calculation folder, preflight resolves the selected POTCAR
variant and ENMAX for the union of all elements in the top and bottom input
layers. One workflow cutoff is then fixed as
`ENCUT = encut_factor * max(selected ENMAX)` and reused by init, relaxation,
bilayer and monolayer MD, and validation inputs. A folder's POTCAR remains local:
it contains only that folder's POSCAR elements in POSCAR order. Stage manifests
record the selected POTCAR directory names, ENMAX values, governing element,
factor, and final ENCUT; they never contain POTCAR payloads.
Stage1 compares its newly resolved plan with cutoff evidence in a newly generated
relaxation manifest and fails before MD writes if it has drifted. Historical
manifests without that evidence remain usable with an explicit warning and a
newly resolved plan.

If any target stage exists, including an empty directory, DPmoire-lite stops before modifying any file. It never moves, backs up, overwrites, or rebuilds a stage in place. Explicitly delete the complete conflicting stage, then rerun the build.

## 3. Init MLFF Paths

The initial MLFF folder provides the seed `ML_ABN` and `ML_FFN` used by VASP-ML
MD calculations.

Manual path:

1. Set `stage: 0`, `init_mlff: true`, and usually `submit: false`.
2. Run `DPmoireLite build config.yaml`.
3. Submit or run `init_mlff/` manually on the desired cluster.
4. Ensure `init_mlff/ML_ABN` and `init_mlff/ML_FFN` exist before stage1.

Auditable two-phase single-allocation workflow:

```yaml
stage: 0
submit: false
init_mlff: true
init_mlff_mode: single-job
init_bottom_incar: init_bottom_INCAR
init_top_incar: init_top_INCAR
```

This explicit mode preflights both layer structures, both scientific INCAR
templates, the submit-script source, every selected POTCAR/ENMAX, the shared
workflow cutoff, and all target paths before formal publication. It then creates
independent `init_mlff/bottom/` and `init_mlff/top/` directories. Each contains a
complete static VASP input set built from its own layer and cell; no POSCAR,
POTCAR, or species-dependent INCAR fields are copied from one layer over the
other. The complete candidate tree and its manifest are published together.

Single-job mode requires a Bash submit template with valid `#SBATCH` directives
before executable content and this exact interface:

```bash
dpmoire_run_vasp() {
    # Run synchronously and return the real VASP launcher exit code.
    srun vasp_std > "sout.${DPMOIRE_PHASE:-manual}"
}

dpmoire_run_vasp # DPMOIRE-LITE:RUN
```

The declaration, standalone closing brace, and marked call are deliberate
contract syntax. There must be exactly one launch-function definition and one
marker; neither may be nested in another shell function. The marker must be the
first top-level executable line after the function and the final executable line
in the file, although comments and blank lines may follow. The function must be
self-contained apart from exported environment and installed commands; do not
append `&` or hide the launcher status. DPmoire-lite leaves this source file
byte-for-byte unchanged and replaces only the exact marked call in the derived
`init_mlff/<dft_script>` copy. It never parses or transforms arbitrary launcher,
container, pipeline, or redirection text.

`init_mlff/manifest.yaml` uses `dpmoire-lite.init-workflow.v2`, records bottom as
`step-1-ready` and top as `planned`, and stores sizes/SHA-256 identities for the
static files, source template, and generated adapter. This evidence can detect a
later conflict without exposing POTCAR contents or other private payloads.

After `DPmoireLite build config.yaml`, inspect and submit exactly the derived
root script:

```bash
cd <work_dir>/init_mlff
sbatch <dft_script>
```

The generated adapter exports `dpmoire_run_vasp` and invokes the trusted
two-phase workflow using `SLURM_SUBMIT_DIR/..` as the work directory. Submit from
the derived script directory as shown: Slurm may execute a spooled script copy,
so the script file's runtime path is not a reliable workspace anchor. This fixed
relative submit-directory contract also lets the complete workspace be moved.
Each synchronous child launch runs in its own bottom/top
working directory with `DPMOIRE_PHASE=step1|step2`, plus
`DPMOIRE_PHASE_ROLE` and `DPMOIRE_PHASE_NAME`. A nonzero bottom exit or invalid
bottom MLFF output records failure and prevents the top launch even if the
template does not use `set -e`. Only validated top outputs publish root
`ML_ABN`/`ML_FFN`.

Completed `dpmoire-lite.init-workflow.v1` manifests remain readable so Stage1
can consume a previously published, hash-verified seed. Only v2 manifests carry
the submit-adapter evidence required to start this automated two-phase command.

Both VASP launches use the same Slurm allocation. Select resources compatible
with both phases and request walltime for their combined runtime. This mode still
requires `submit: false`: automatic `sbatch` is a later unit. It performs no
second submission, `sacct` query, polling, retry, resume, or cross-job dependency.

Non-wait submit path:

```bash
DPmoireLite build config.yaml
```

with `submit: true` submits the first init MLFF job only for the init workflow.
The second init MLFF step is left to the user. Stage0 still continues to
generate and, when enabled, submit relaxation and validation folders from the
same configuration.

Submitted `--wait` is temporarily disabled. After the first init job, inspect
its outputs, prepare the second init MLFF step manually, submit it manually, and
confirm `ML_ABN` and `ML_FFN` before proceeding to Stage1.

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

Stage1 clears MD constraints by default. Set `preserve_grid_shift_md: true` to
preserve only DPmoire-lite grid-shift anchors; their `F F T` mask means fixed
x/y and movable z. Arbitrary user constraints are not covered by this option.

If `symm_reduce: true`, DPmoire-lite uses `pymatgen`/`spglib` to reduce
symmetry-equivalent stackings and writes `sym_reduced_stackings.txt`.

## 5. Submitting Stage0

Set `submit: true` to submit generated folders with Slurm.

Automatic submission applies to the default `init_mlff_mode: manual`. The
`single-job` mode generates a ready-to-submit root adapter but still rejects
`submit: true` before writing any target; inspect it and run one `sbatch`
manually as described above.

Without `--wait`, DPmoire-lite submits every folder requested by the current
stage and exits:

```bash
DPmoireLite build config.yaml
```

This fire-and-forget path guarantees only a successful `sbatch` invocation. It
does not guarantee final job success or advance dependent stages. `n_nodes`
throttling and `auto_resub` are unavailable because the process does not keep
polling Slurm. Submitted `--wait` is temporarily disabled, so `auto_resub` is not
production-ready.

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

## 7. Disabled `stage: all`

`stage: all` is temporarily unavailable in every submit/wait combination because
Slurm terminal-state validation and failure propagation are not yet reliable.
There is no hidden bypass.

Use `stage: 0` with `submit: false`, distribute and submit the generated folders
manually, and inspect the init MLFF, relaxation, and optional validation outputs.
Only after those checks should you use `stage: 1` with `submit: false`, submit the
MD folders manually, and inspect their outputs.

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

The CLI writes one concise aggregate line to stderr. Detailed per-source
diagnostics remain in the selected result manifest.

| Exit code | Status | Result |
| ---: | --- | --- |
| `0` | `complete` | Frames published with complete expected-source coverage. |
| `1` | `fatal` | Configuration, provenance, manifest, recovery, or publication failure. |
| `2` | `degraded` | Frames published with partial, skipped, failed, or unknown coverage. |
| `3` | `no_data` | No new frames published. |

Every nonzero code is a shell failure. `no_data` never creates, deletes, or
replaces extxyz; an existing output stays byte-for-byte unchanged.

For `vasp_ml: true` MD, the default `seed-aware` mode validates the immutable
Stage1 seed provenance and never derives it from the current `md/ML_AB`. Older
restart trees can explicitly request:

The exact fast path preserves `mlab-seed-v1`. When VASP rewrites the trusted
seed prefix with numerically insignificant differences, the versioned fallback
requires identical elements, counts, atom order, and configuration order, then
checks every numeric component with
`abs(a-b) <= 1e-12 * max(1, abs(a), abs(b))`. Manifest v2 fallback reads only a
path-contained, raw-hash-verified reference; legacy fallback reads only complete
`init_mlff/ML_ABN` evidence and emits its compatibility warning. VASP-equivalent
verification is accepted as complete coverage and removes exactly the recorded
seed count once.

```bash
DPmoireLite collect config.yaml --stage md --mlff-collect-mode full-dedup
```

`full-dedup` reads every accepted final `ML_ABN`, retains the first exact copy
of repeated configurations (including one shared seed), and therefore performs
more I/O. It is valid only for MLFF MD; relaxation, validation, and non-ML MD
return fatal/exit 1.

Current Manifest v2 is authoritative. Legacy or missing-manifest compatibility
collection writes `MD_data.collect.yaml` without fabricating or rewriting build
provenance. Missing-manifest scanning is full-dedup-only and is at best
`degraded` when it produces frames because expected-source coverage is unknown.

For nonzero seed-aware publication, `collect.dedup.seed_verification` reports
aggregate `exact`, `vasp_equivalent`, and `mismatch` counts. Each verified source
has a bounded `collect.sources[].seed_verification` record containing identities,
reference trust, maximum absolute/scaled deltas, and an optional first mismatch.
Complete seed configurations are never serialized. Exact `mlab-seed-v1`, exact
`mlab-config-v1`, and explicit full-dedup remain independent and unchanged.

For relaxation and `vasp_ml: false` MD, DPmoire-lite reads OUTCAR series. The
default OUTCAR patterns are:

```yaml
outcar_patterns:
  - '^OUTCAR\d+$'
  - '^OUT\d+$'
  - '^out\d+$'
  - '^OUTCAR$'
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
