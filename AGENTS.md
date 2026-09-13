# Repository Execution Rules

## Authoritative Sources

- Product behavior follows the approved notes, designs, and leaf plans.
- Git facts outrank ignored runtime state when they disagree.
- Dynamic execution state belongs only in the ignored local status snapshot.

## Verified Python

- Primary development environment is the `dpmoire` conda env on the Linux
  cluster: `~/anaconda3/envs/dpmoire/bin/python` (Python 3.10, pytest
  included). The canonical source tree is the git clone at
  `~/work/soft/DPmoire-lite`.
- The Windows interpreter `C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe`
  belonged to the retired Windows workspace and is no longer authoritative.
- Verify `sys.executable` before relying on Python.
- Do not use bare `python`, `python3`, or a WindowsApps alias.
- Do not install or change packages without explicit user approval.

## Execution Boundary

- Complete at most one green execution unit per model run.
- Establish an expected behavioral RED before implementation.
- Never commit a failing default suite or future-unit RED inventory.
- When two persistent threads share this worktree, use serial handoff and allow
  only one write-capable thread at a time.
- Stop after the checkpoint; do not begin the next unit in the same run.

## Git and Safety

- Preserve unrelated tracked and untracked changes.
- Stage an exact file list owned by the active unit; never use `git add .` or
  `git add -A`.
- Inspect cached names and run cached diff checks before committing.
- Do not reset, checkout, clean, rewrite history, push, or open a PR unless the
  user grants that authority.
- Local implementation status and history are never staged or committed.

## Private Data and Fixtures

- Never use `example-test/` as a direct automated-test dependency.
- Never stage or package POTCAR content or private calculation output.
- Derived fixtures must be minimal, sanitized, redistributable, and documented.

## Preserved Boundaries

- Keep `stage: all` and submitted `--wait` fail-closed.
- Do not implement deferred Slurm automation, MD restart, or fuzzy deduplication
  outside an approved unit.
- Keep the Linux `renameat2` no-replace publish fallback and its mock-`EINVAL`
  tests; GPFS does not implement `RENAME_NOREPLACE`, so the fallback is the
  supported no-replace publication path there.

## Scoped Instructions

- Read every narrower AGENTS.md that governs a file before editing it.
- Narrower instructions may refine workflow but cannot override authoritative
  scientific or safety decisions.

## Agent Skills

### DPmoire-lite guide

For package learning, operation, diagnosis, or changes, use the repository skill
at `.agents/skills/dpmoire-guide/SKILL.md` (`$dpmoire-guide` in Codex). Start with
one task tag: `[LEARN]`, `[RUN]`, `[DEBUG]`, or `[CHANGE]`. See
`docs/dpmoire-agent-guide.zh-CN.md` for beginner prompts and error meanings.
