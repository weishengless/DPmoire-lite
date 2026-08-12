# [LEARN] Read-Only Package Tour

Use this mode to explain the package without changing files or running a
calculation.

## Read in This Order

1. `AGENTS.md`: repository authority, safety, Python, and git rules.
2. `README.md`: supported public behavior, status meanings, config tags, and
   one-shot policy.
3. `workflow.md`: operational sequence and Slurm/MLFF details.
4. `example/config.yaml`: the commented configuration template.
5. `pyproject.toml`: package metadata and the `DPmoireLite` entry point.
6. `src/dpmoire_lite/AGENTS.md`: module ownership and architectural seams.
7. Only then read the relevant production module and its focused tests.

Do not begin with a whole-repository dump. Search for the user's term with
`rg`, then read the owning module and a representative test.

## Use This Architecture Map

| Question | Start here |
| --- | --- |
| CLI syntax or exit mapping | `src/dpmoire_lite/cli.py` |
| Config parsing and validation | `src/dpmoire_lite/config.py` |
| Aggregate no-write build checks | `src/dpmoire_lite/build_preflight.py` |
| Stage generation and submission orchestration | `src/dpmoire_lite/build.py` |
| Structures, input files, or INCAR rendering | `structures.py`, `inputs.py`, `incar.py` |
| Stage authority or input identity | `manifest.py`, `provenance.py` |
| Init-MLFF workflow | `init_mlff*.py`, `slurm.py` |
| OUTCAR or ML_AB parsing | `outcar.py`, `mlab.py`, `mlff_seed.py` |
| Collection decisions and statuses | `collect.py`, `collect_models.py`, `mlff_collect.py` |
| Transactional dataset publication | `collect_publish.py`, `file_lock.py`, `lock_safety.py` |

Resolve unqualified filenames in that table under `src/dpmoire_lite/`.

## Teach the Workflow in Five Sentences

1. `stage: 0` prepares init, relaxation, and optional validation targets.
2. The user runs or submits those calculations and verifies their real outputs.
3. `stage: 1` consumes provenance-checked relaxation results and, for MLFF MD,
   the completed immutable init seed files.
4. The user finishes MD calculations.
5. `collect` publishes a stage-specific dataset and records bounded evidence.

Explain that a generated directory is not a completed calculation, a submitted
job is not a successful job, and a manifest is authority rather than a cache.

## Answer Template

```text
Short answer: <one paragraph>
Flow: <ordered inputs -> owner -> outputs>
Authority: <current files that establish the answer>
Safety invariant: <what the workflow deliberately refuses to do>
Try next: <one read-only prompt or harmless help command>
```

Use plain language first. Define each package term before using its acronym.
Mention uncertainty when code, docs, and tests do not agree; use the authority
rules in `AGENTS.md` to resolve the disagreement.
