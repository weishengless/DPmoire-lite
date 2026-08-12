# [RUN] Operate the Workflow Safely

Use this mode only for an operation the user authorized. State expected writes,
costly external effects, and the stop condition before running a command.

## Pre-Run Gate

1. Read the current config without editing it.
2. Record `stage`, `submit`, `vasp_ml`, `init_mlff`, and `init_mlff_mode`.
3. Confirm whether the user authorized generation only, collection, deletion,
   or real Slurm submission. These are different permissions.
4. Inspect the target paths and required upstream outputs before `build`.
5. Use the interpreter and environment required by `AGENTS.md`; verify its
   `sys.executable` before relying on it.
6. Prefer `submit: false` for inspectable generation. Reject `stage: all` and
   submitted `--wait` as the current product requires.

Do not proceed when the intended command or its external cost is ambiguous.

## Public Commands

```text
DPmoireLite init-example <target-dir>
DPmoireLite build <config.yaml>
DPmoireLite collect <config.yaml> --stage rlx
DPmoireLite collect <config.yaml> --stage md
DPmoireLite collect <config.yaml> --stage validation
DPmoireLite collect <config.yaml> --stage md --mlff-collect-mode full-dedup
```

Use `DPmoireLite --help` and the subcommand help from the active checkout when
syntax is uncertain. `run-init-mlff` is an internal generated-workflow entry
point; do not present it as a normal manual starting command.

## Build Decision Table

| Observation | Meaning | Required action |
| --- | --- | --- |
| `Build target conflict(s)` | At least one complete target boundary already exists | Stop. Report every target. Never partially rebuild or delete it for the user. |
| `Build preflight failed` | Inputs cannot safely produce the requested stage | Stop. No calculation directories should have been generated. Diagnose every listed item. |
| `build status=generated` | Files were generated; no `sbatch` request succeeded | Inspect generated files. Do not claim any calculation ran. |
| `build status=submission_requested` | Slurm accepted at least one request | Record job evidence, then stop unless monitoring was separately authorized. Do not claim completion. |
| `build status=submission_failed` or nonzero | Submission failed | Preserve the manifest evidence and scheduler context; diagnose before retrying. |

An existing empty directory or symlink still counts as a conflict. If deletion
is truly needed, name the exact complete stage and obtain explicit user
authority in a separate, checkable step.

## Stage Order

1. Generate Stage0.
2. Finish and inspect enabled init/relaxation/validation calculations.
3. Verify required `CONTCAR` outputs and their provenance.
4. For MLFF MD, verify completed `init_mlff/ML_ABN` and `ML_FFN` evidence.
5. Generate Stage1.
6. Finish MD calculations.
7. Collect each requested dataset separately.

Do not infer completion from directory existence, `sbatch` acceptance, or a
stale ignored status file.

## Collect Decision Table

| Exit | Status | Publication meaning | Next action |
| ---: | --- | --- | --- |
| `0` | `complete` | Frames published with complete expected-source coverage | Report output and evidence. |
| `1` | `fatal` | Config, provenance, manifest, recovery, lock, or publication failed | Preserve state and switch to `[DEBUG]`. |
| `2` | `degraded` | Frames published, but coverage is partial, skipped, failed, or unknown | Report output plus every coverage gap; do not call it complete. |
| `3` | `no_data` | No new frames were published | Confirm any previous extxyz stayed unchanged, then inspect sources. |

Every nonzero exit is a shell failure. `full-dedup` is valid only for MLFF MD.
Missing-manifest compatibility discovery is explicit MD `full-dedup` only and
cannot establish complete expected-source coverage.

## Run Report

Record the exact command, working directory, executable, exit code, one-line
status, paths created or preserved, and whether any external submission was
requested. Never replace these facts with "it worked."
