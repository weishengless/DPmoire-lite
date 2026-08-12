# [CHANGE] Modify One Green Execution Unit

Use this mode for an authorized repository change. A request to review or
diagnose alone does not authorize implementation.

## Change Loop

1. Restate one observable acceptance criterion.
2. Read the issue/spec/approved leaf plan and every scoped `AGENTS.md`.
3. Inspect `git status --short`; preserve unrelated work.
4. Find the owning production seam and representative focused tests.
5. Establish an expected behavioral RED before implementation.
6. Make the smallest coherent change at the owner seam.
7. Run the focused GREEN with the exact verified Python interpreter.
8. Run the default suite or the broadest proportionate check that can expose a
   regression. Do not leave a future-unit RED in the default suite.
9. Inspect the final diff and status.
10. Stop after this unit. Stage exact paths only when asked; commit, push, or
    open a pull request only with separate explicit authority.

Never use `git add .`, `git add -A`, destructive reset/checkout/clean, or private
calculation data. Never install or change packages without user approval.

## Put Behavior in Its Owner

| Concern | Owner |
| --- | --- |
| Config schema and safety validation | `config.py` |
| INCAR parsing/rendering | `incar.py` |
| Manifest schema and atomic manifest I/O | `manifest.py` |
| ML_AB parsing and identity | `mlab.py`, `mlff_seed.py` |
| Structures and constraint transforms | `structures.py` |
| Input assembly and POTCAR selection | `inputs.py` |
| Aggregate no-write checks | `build_preflight.py` |
| Generation orchestration | `build.py` |
| Collection discovery/status | `collect.py`, `collect_models.py` |
| Transactional output publication | `collect_publish.py` |
| User-facing command/exit mapping | `cli.py` |

Read `src/dpmoire_lite/AGENTS.md` for the current full ownership map. Avoid
duplicating manifest, provenance, or publication logic in the CLI.

## High-Risk Review

Before calling the unit green, check all applicable statements:

- Preflight remains aggregate and creates no calculation directories on error.
- One-shot stage creation stops before modifying any target on conflict.
- Stage1 inputs remain pinned and provenance-checked through publication.
- `stage: all` and submitted `--wait` remain fail-closed.
- Submission status does not imply job completion.
- Collection uses structured source results and transactional publication.
- Exit `2` can mean a changed dataset; exit `3` preserves any prior dataset.
- Manifests, journals, locks, and publication targets cannot be swapped by a
  late path race.
- Tests contain no POTCAR payload, private output, or direct `example-test/`
  dependency.

## Change Report

Report the acceptance criterion, RED command/result, GREEN command/result,
broader verification, exact changed paths, remaining risk, and git operations
actually performed. If verification cannot run, do not describe the unit as
green.
