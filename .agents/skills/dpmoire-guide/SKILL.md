---
name: dpmoire-guide
description: "Guide agents through DPmoire-lite repository learning, safe workflow operation, diagnosis, and code changes. Use when a request mentions DPmoire-lite, the DPmoireLite CLI, config.yaml tags, stage 0 or 1, build or collect, init MLFF, VASP, MLFF, OUTCAR, manifests, provenance, Slurm submission, tests, or package architecture. Route weak or low-context models through [LEARN], [RUN], [DEBUG], or [CHANGE] with explicit evidence and stop conditions."
---

# DPmoire-lite Agent Guide

Use this skill as a router. Read current repository sources instead of treating
this skill as a second implementation specification.

## Start Every Task

1. Locate the repository root.
2. Read the root `AGENTS.md` completely.
3. Find every narrower `AGENTS.md` that governs files you may inspect or edit.
4. Choose one primary mode from the table below.
5. Read the matching reference before using other tools.
6. State the task contract before acting:

```text
Mode: [LEARN|RUN|DEBUG|CHANGE]
Goal: <one observable outcome>
Evidence: <files, command output, or failing test needed>
Writes allowed: <none, or exact paths/actions authorized by the user>
Stop when: <explicit checkpoint>
```

Resource paths in this file are relative to the directory containing
`SKILL.md`, not to the shell working directory. If shell access exists, run:

```text
<verified-python> <repo-root>/.agents/skills/dpmoire-guide/scripts/repo_snapshot.py --repo <repo-root>
```

Use the verified Python interpreter required by the root `AGENTS.md`. Use the
snapshot for orientation only; it does not replace reading instructions.

## Choose One Primary Mode

| Mode | Choose it when the requested outcome is | Read |
| --- | --- | --- |
| `[LEARN]` | A read-only explanation, architecture map, or guided tour | `references/learn.md` |
| `[RUN]` | An authorized CLI, generation, collection, or submission operation | `references/run.md` |
| `[DEBUG]` | A diagnosis or evidence-backed explanation of a failure, with no fix authorized | `references/debug.md` |
| `[CHANGE]` | A code, test, documentation, or configuration change, including an authorized bug fix | `references/change.md` |

An explicit user tag normally wins. Correct it aloud when the requested action
does not match it. For example, a request to "fix" is `[CHANGE]`, while a
request to "diagnose only" is `[DEBUG]`. A `[CHANGE]` task may reproduce a bug;
do not switch to a second green execution unit after the requested checkpoint.

Also read `references/config-tags.md` when the user asks about configuration
keys, "tags", or example `config.yaml` values.

## Keep These Safety Gates Closed

- Treat a mode tag as routing, not permission. Do not write, submit jobs, delete
  stages, or alter external state unless the user authorized that action.
- Follow the authority order and exact Python rule in the scoped `AGENTS.md`
  files. Never replace the required interpreter with bare `python` or `python3`.
- Preserve `stage: all` and submitted `--wait` as fail-closed unless an approved
  unit explicitly changes that product decision.
- Treat an existing target stage, including an empty directory or symlink, as a
  hard stop. Never delete, move, back up, merge, or rebuild it automatically.
- Treat `submission_requested` as Slurm acceptance only, never calculation
  completion. Do not invent polling, retries, resume, or dependencies.
- Treat every nonzero `collect` exit as a shell failure. Distinguish `fatal`,
  `degraded`, and `no_data` before recommending an action.
- Never fabricate or hand-edit build provenance, collection evidence, recovery
  journals, or lock files to make a run pass.
- Never use `example-test/` in automated tests. Never expose, package, stage, or
  commit POTCAR payloads or private calculation output.
- Preserve unrelated tracked and untracked changes. Do not commit, push, open a
  pull request, or stage broad file sets without explicit authority.

## Report a Checkable Result

End with all applicable fields:

```text
Outcome: <completed, diagnosed, or stopped>
Evidence: <exact commands, exit codes, statuses, and repo-relative paths>
Changed: <exact paths, or none>
Safety state: <targets/outputs/manifests preserved or intentionally changed>
Next step: <one safe action, or none>
```

Separate commands you actually ran from commands you only recommend. If facts
are missing, say what is unknown and stop at the safest reversible boundary.
