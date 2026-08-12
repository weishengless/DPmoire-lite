# [DEBUG] Diagnose Without Destroying Evidence

Use this mode for explanation and root-cause isolation. Make no fix unless the
user separately authorizes a `[CHANGE]` task.

## Capture the Failure First

Record:

- exact command and working directory;
- verified interpreter or CLI path;
- complete exit code and one-line status;
- bounded stderr around the first failure;
- current `git status --short`;
- relevant config values without POTCAR content or private calculation data;
- existence and identity of the stage manifest, target, lock, and journal.

Do not rerun a write-capable command merely to obtain cleaner output.

## Classify the First Divergence

| Layer | Typical evidence owner |
| --- | --- |
| Config shape or unsafe mode | `config.py`, `example/config.yaml`, CLI stderr |
| Build inputs and target conflicts | `build_preflight.py`, stage paths |
| Slurm or VASP execution | generated script, bounded job output, init manifest |
| Stage authority and input identity | `manifest.py`, `provenance.py` |
| OUTCAR or ML_AB source parsing | `outcar.py`, `mlab.py`, source result evidence |
| Collection status and coverage | `collect.py`, collection fields in the result manifest |
| Lock, recovery, or atomic publication | `collect_publish.py`, `file_lock.py`, journal evidence |

Stop at the first layer where observed state differs from expected state. Later
errors are usually consequences.

## Common Failure Matrix

| Symptom | Meaning | Safe response |
| --- | --- | --- |
| Missing required field or rejected old names such as `VASP_ML` or `K-mesh` | Config schema failure | Compare with the current example and `README.md` Config Tags; use snake_case. |
| `stage: all` or submitted `--wait` rejected | Deliberate fail-closed boundary | Use separate Stage0 and Stage1 generation with manual inspection. Do not bypass it. |
| `Build target conflict(s)` | One-shot stage already exists, even if empty | Preserve it. Report exact conflicts and ask the human to decide whether the entire stage should be removed. |
| Stage1 reports missing/invalid relaxation or seed evidence | Upstream calculation or provenance is incomplete | Identify the first missing `CONTCAR`, manifest fact, `ML_ABN`, or `ML_FFN`; finish or restore the authoritative upstream workflow. |
| `submission_requested` but no finished outputs | Only `sbatch` acceptance was recorded | Inspect scheduler/job results outside DPmoire-lite; do not generate Stage1 yet. |
| `submission_failed` / `SUBMIT_FAILED` | The request failed after generation | Preserve bounded manifest evidence and diagnose scheduler/script context before retry. |
| `collect` exit `1` / `fatal` | No safe publication result can be claimed | Read the fatal diagnostic and classify config, provenance, manifest, lock, recovery, or publication cause. |
| `collect` exit `2` / `degraded` | A dataset was published with incomplete or unknown coverage | Keep the dataset and report failed/skipped/partial sources; do not rerun blindly. |
| `collect` exit `3` / `no_data` | Nothing new was accepted | Verify an existing output is byte-preserved; inspect declared sources and patterns. |
| `full-dedup collection requires MLFF MD mode` | Mode used for rlx, validation, or non-ML MD | Return to default `seed-aware` or choose the correct MLFF MD config. |
| Missing current manifest in seed-aware collection | Immutable seed/source authority is unavailable | Do not fabricate a manifest. Use the correct build provenance; explicit legacy MD full-dedup is a separate compatibility choice. |
| Lock, journal, recovery, or target-changed diagnostic | Concurrent access, interrupted publication, or path race | Stop all competing writers. Preserve the lock/journal and inspect ownership and target identity before recovery. |

## Prove the Cause

Use the smallest read-only inspection or focused test that distinguishes the
leading hypothesis from alternatives. Cite the production branch and the test
that establishes expected behavior. Do not use `example-test/` as a fixture.

## Diagnosis Report

```text
Observed: <facts and exit/status>
Expected: <source-backed behavior>
First divergence: <earliest mismatching layer>
Cause: <mechanism, not a restatement of the error>
Confidence: <high/medium/low and why>
Changed: none
Safe next step: <one bounded action requiring the right authority>
```
