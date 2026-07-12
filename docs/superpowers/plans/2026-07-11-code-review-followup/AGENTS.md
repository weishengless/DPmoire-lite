# Plan Execution Controller

## Active-state Recovery

- Read the repository rules, this file, Git facts, and
  `IMPLEMENTATION-STATUS.local.md` before selecting work.
- The local status is a compact recovery snapshot, never planning authority.
- If state is missing or disagrees with Git, perform one read-only
  reconstruction, correct the snapshot, and stop without implementation.
- Never resolve disagreement through reset, checkout, clean, deletion, or
  absorption of unknown changes.

## One Execution Unit

- Select exactly one leaf-plan Task or one explicitly approved adjacent
  RED-only/GREEN pair.
- Legal progress is `inspect -> red -> green -> verify -> commit`; `blocked` is
  a stop state.
- A valid RED fails for missing behavior, not imports, paths, syntax, fixtures,
  skips, or private data.
- No future-unit RED test may remain in default discovery across checkpoints.

## Execution Capsule

Every delegated capsule must state:

1. exact Task and phase;
2. execution profile, RED handoff mode, and `standalone_eligible` value;
3. directly applicable authorities;
4. exact allowed files and known pre-existing changes;
5. named RED tests, command, and expected failure;
6. minimal GREEN behavior;
7. focused, affected, and full-suite commands;
8. non-goals and stop conditions;
9. required evidence and report shape.

Missing or contradictory fields require a no-edit stop.

## Role Authority

- The normal workflow uses two persistent threads and a serial handoff in one
  shared worktree; only one thread may be write-capable at a time.
- The Sol orchestrator thread alone selects units, interprets plans, issues
  capsules, edits plans/specifications/state, reviews, stages, commits, or
  advances execution.
- The Luna worker thread edits only capsule-named files, follows the stated RED
  handoff, runs named tests, and returns an unstaged diff with evidence.
- A worker never delegates, widens scope, stages, commits, pushes, or changes
  active state.
- Model, reasoning, and speed are selected in each thread's UI; prompt text does
  not assign or prove a model profile.

## Contradictions and Failures

- Git/state disagreement, invalid RED, unknown changes, scope expansion, or a
  scientific contradiction stops the unit.
- Do not choose a new scientific, schema, compatibility, or recovery rule during
  implementation; return it for approved design correction.
- Do not skip, ignore, or xfail a required test to manufacture GREEN.

## Checkpoint

- Run focused, affected, and full suites, then inspect the complete diff and
  `git diff --check`.
- The orchestrator stages only the explicit owned files, audits the cached diff,
  and creates one green local commit.
- Update ignored state only after the commit, name the next unit at `inspect`,
  and stop after the checkpoint.
