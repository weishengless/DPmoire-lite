# DPmoire-lite Code Review

Date: 2026-07-11

Reviewed commit: `9f43b951a67f36d1ac65023c1a18501c6a6e6c1a`

Follow-up branch: `codex/code-review-followup`

> Follow-up discussions refined several findings and changed the implementation
> scope. See [`docs/code-review-notes/README.md`](code-review-notes/README.md).
> Where the original review and a follow-up note differ, the follow-up note is
> authoritative.

## Executive Summary

The repository has a clear module split, sensible path-containment checks, safe
YAML loading, focused geometry tests, strict stage1 input preflight, and a wheel
test that verifies the bundled example. The main risks are not general code
style issues; they affect scientific correctness and unattended Slurm workflow
reliability.

Do not rely on the current `stage: all` workflow for unattended production data
generation until the P1 findings below are addressed. In particular, relaxation
constraints can leak into MD, failed Slurm jobs do not reliably stop dependent
stages, and real `sacct` state strings can bypass throttling and resubmission.

## Verification Baseline

The final review run completed with:

```text
93 passed, 5 skipped
```

All five skips come from `tests/conftest.py`, which points to the machine-local
sample directory:

```text
E:\codespace\MLFF\03_constrained_shear_scan
```

Consequently, the real OUTCAR/ML_ABN parsing tests in
`tests/test_collect.py:53,63,77,114,133` are not portable and normally skip in
CI or on another developer machine.

## Findings

### P1-1: Relaxation Constraints Leak Into MD When `sc_rlx: true`

Locations:

- `src/dpmoire_lite/structures.py:174-176`
- `src/dpmoire_lite/structures.py:324-330`
- `src/dpmoire_lite/build.py:101`
- `src/dpmoire_lite/build.py:379-387`

Stage0 deliberately applies `FixedLine` constraints to one atom in each layer,
which ASE writes as `F F T`. In the `sc_rlx: true` stage1 path,
`rewrite_contcar_as_poscar()` reads the constrained CONTCAR and writes it back
without clearing its ASE constraints.

A targeted reproduction produced the following MD POSCAR content:

```text
Selective dynamics
Direct
... F F T
... F F T
```

VASP applies selective-dynamics flags during both relaxation and MD, so these
atoms remain laterally constrained. This biases MD trajectories, forces, and
training data.

Recommended fix:

1. Clear constraints before every MD POSCAR write, or write with
   `ignore_constraints=True`.
2. Make the behavior explicit for both `sc_rlx` branches.
3. Add an end-to-end regression test that starts with a realistic constrained
   relaxation CONTCAR and asserts that the MD POSCAR contains no `Selective
   dynamics` line.

Reference: <https://vasp.at/wiki/POSCAR>

### P1-2: Failed Slurm Jobs Do Not Stop Dependent Stages

Locations:

- `src/dpmoire_lite/slurm.py:110-126`
- `src/dpmoire_lite/build.py:245-248`
- `src/dpmoire_lite/build.py:170-177`

`SlurmRunner.submit_many()` returns failed terminal jobs normally. Callers do
not require the final attempt for each calculation directory to be
`COMPLETED`. As a result:

- init step2 may be prepared after a failed init step1 if partial ML files exist;
- a failed retry can still allow the dependency chain to continue;
- validation or final MD jobs can fail while the CLI still exits successfully;
- existence-only checks can distribute partial `ML_ABN` or `ML_FFN` files.

A minimal runner returning `FAILED` completed `submit_many()` without raising:

```text
returned_normally=[('rlx/0_0', 'FAILED')]
```

Recommended fix:

1. Group attempts by calculation path and inspect the latest attempt.
2. Require `COMPLETED` before a dependent stage proceeds.
3. Persist the final statuses before raising an aggregated workflow error.
4. Return a nonzero CLI exit status when waited jobs fail after retries.
5. Validate required MLFF files as nonempty and structurally readable rather
   than checking existence only.

### P1-3: Default-Width `sacct` Output Breaks State Classification

Locations:

- `src/dpmoire_lite/slurm.py:9-26`
- `src/dpmoire_lite/slurm.py:77-83`
- `src/dpmoire_lite/slurm.py:110-123`

The query requests the default-width `State` field. Slurm appends `+` when a
state does not fit. Examples include:

```text
REQUEUE_HOLD  -> REQUEUE_H+
OUT_OF_MEMORY -> OUT_OF_ME+
CONFIGURING   -> truncated value
```

The implementation compares exact strings. A truncated held or configuring
job is therefore treated as terminal, which frees a throttle slot too early.
A truncated failure also bypasses `auto_resub`. Unlisted nonterminal flags such
as `SIGNALING`, `STAGE_OUT`, and `POWER_UP_NODE` fail in the same way because
the current logic treats every unknown value as terminal.

Recommended fix:

1. Request untruncated, machine-readable output, for example:

   ```text
   sacct --noheader --parsable2 --allocations \
     --format=JobIDRaw,State%30
   ```

2. Normalize any ancillary state text before classification.
3. Use explicit successful and failed terminal-state sets.
4. Treat unknown states as active/indeterminate or raise an actionable error;
   never fail open by marking them complete.
5. Add tests using realistic truncated output and every supported active flag.

References:

- <https://slurm.schedmd.com/sacct.html>
- <https://slurm.schedmd.com/job_state_codes.html>

### P1-4: Valid INCAR Syntax Is Not Rewritten Safely

Locations:

- `src/dpmoire_lite/inputs.py:72-82`
- `src/dpmoire_lite/inputs.py:139-157`

The current implementation splits lines on whitespace. It does not correctly
support valid VASP statements such as:

```text
ENCUT=400
ML_RCUT1=6
ENCUT = 450; ISMEAR = -1
LUSE_VDW=T; ENCUT=400
```

Confirmed behavior:

- `ENCUT=400` and `ML_RCUT1=6` remain unchanged;
- replacing the first tag in a semicolon-separated line removes the unrelated
  trailing statement;
- `LUSE_VDW=T; ...` is not recognized as requiring `vdw_kernel.bindat`.

This can silently retain incorrect cutoffs and ML radii, remove user settings,
or create a calculation directory without its required vdW kernel.

Recommended fix:

1. Parse statements around comments, semicolons, and `=` instead of relying on
   whitespace positions.
2. Preserve unrelated statements and comments.
3. Define duplicate-tag behavior consistently, preferably using the last active
   statement as VASP does.
4. Add tests for no-space assignment, semicolon-separated statements, comments,
   lowercase tags, and duplicate tags.

Reference: <https://vasp.at/wiki/INCAR>

### P1-5: Stage1 Does Not Validate Stage0 Build Provenance

Locations:

- `src/dpmoire_lite/build.py:86-101`
- `src/dpmoire_lite/build.py:448-466`

Stage1 uses the current `config.sc_rlx` to decide whether the relaxation
CONTCAR must be expanded. The relaxation manifest neither records nor validates
that flag.

Confirmed reproduction:

1. Run stage0 with `sc_rlx: true` and `sc: [2, 1]`.
2. The relaxation structure contains 4 atoms.
3. Change only `stage` and set `sc_rlx: false` before stage1.
4. Stage1 expands the already-expanded CONTCAR, producing an 8-atom MD
   structure.

The reverse change can omit a required expansion. Both cases silently produce
scientifically incorrect cells.

Recommended fix:

1. Record `sc_rlx` and other cross-stage semantic fields in the relaxation
   manifest.
2. Record hashes for the config and relevant structure inputs.
3. Before stage1 mutation, reject incompatible provenance changes with a clear
   aggregated error.
4. Distinguish intentionally mutable stage1 settings from immutable stage0
   structure semantics.

### P2-1: A Malformed ML_ABN Source Can Contribute Partial Frames

Locations:

- `src/dpmoire_lite/dataset.py:46-111`
- `src/dpmoire_lite/collect.py:91-95`

`Dataset.load_ml_ab()` appends configurations directly to the shared output
dataset. If a later configuration is malformed, collection records the source
as failed but does not roll back previously appended frames.

A synthetic file with one valid configuration followed by one malformed
configuration produced:

```text
IndexError: list index out of range
frames_retained_after_failure=1
```

The resulting extxyz can therefore contain frames from a source that the
manifest says failed.

Recommended fix:

- Parse each source into a temporary `Dataset` and merge only after complete
  success; or
- explicitly implement and document partial-frame salvage, recording exactly
  how many frames were accepted before failure.

The all-or-nothing approach is safer for reproducible scientific datasets.

### P2-2: Build Generation, Submission, and Manifest Updates Are Not Transactional

Locations:

- `src/dpmoire_lite/build.py:89-135`
- `src/dpmoire_lite/build.py:272-295`
- `src/dpmoire_lite/build.py:397-409`

Existing targets are moved to backups before all immutable inputs have been
validated. Manifests are written only after generation and submission finish.
If a POTCAR, template, submit script, vdW kernel, or an intermediate `sbatch`
call fails:

- the live stage can contain a partial mixture of new directories;
- the manifest can remain stale or be absent;
- previously submitted jobs may have no persisted job IDs;
- rerunning can submit duplicate jobs.

Recommended fix:

1. Preflight all immutable inputs and POTCAR resolutions before moving targets.
2. Generate each target in a temporary sibling directory.
3. Atomically replace the target only after it is complete.
4. Persist job IDs immediately after every successful submission.
5. Write manifests atomically and include an explicit workflow status.

### P2-3: Zero-Frame Collection Deletes the Previous Dataset

Locations:

- `src/dpmoire_lite/collect.py:34-47`
- `src/dpmoire_lite/dataset.py:144-145`

If the current run collects zero frames, it directly unlinks an existing
dataset. A temporary filesystem outage, config mismatch, or parser regression
can therefore delete the last known-good `MD_data.extxyz`,
`rlx_data.extxyz`, or `valid.extxyz`.

Nonempty output is also written directly to the final path, so a write failure
can leave a truncated replacement.

Recommended fix:

1. Preserve the previous output by default when no new frames are available.
2. Add an explicit option if destructive empty replacement is required.
3. Write new data to a temporary file, validate it, then use an atomic replace.
4. Optionally timestamp-backup the previous dataset alongside stage backups.

### P2-4: `outcar_patterns` Is Not Validated

Locations:

- `src/dpmoire_lite/config.py:278`
- `src/dpmoire_lite/config.py:313`
- `src/dpmoire_lite/outcar.py:12-22`

The loader applies `tuple()` to any supplied value. A scalar string becomes a
tuple of characters; the resulting `^` pattern matches every filename in the
calculation directory. An invalid regular expression is not detected until
collection, where it aborts outside the per-source error handling.

Recommended fix:

- require a nonempty list or tuple of nonempty strings;
- compile every expression during config loading;
- raise `ConfigError` with the failing pattern and regex error;
- add scalar, mixed-type, empty-list, and malformed-regex tests.

## Test and Tooling Gaps

1. Vendor small, redistributable OUTCAR and ML_ABN fixtures under `tests/data/`
   so core parser tests do not depend on a developer-local drive.
2. Add a realistic Slurm output fixture rather than testing only idealized full
   state strings.
3. Add an end-to-end Stage0-to-Stage1 constraint test.
4. Add direct tests for `find_sym_reduced_stackings()`; the bundled example
   enables `symm_reduce`, but no current test executes that path.
5. Add CI for the supported Python versions and package build.
6. Add a lightweight lint/type-check configuration. Current optional attributes
   in `StructureHandler` are assumed non-null by several methods and are not
   statically narrowed.

## Scalability Improvements

`src/dpmoire_lite/outcar.py:26` calls `read_vasp_out(path, ":")`. ASE converts
that request into a complete in-memory list before the configured sampling
frequency is applied. Large moire MD OUTCAR files can therefore create a much
larger memory peak than the final dataset requires.

Prefer `iread_vasp_out()` or `ase.io.iread()` and sample while streaming. If
source-level rollback is required, stream into a temporary extxyz or temporary
dataset artifact and commit it after a complete parse.

Reference: <https://docs.ase-lib.org/_modules/ase/io/vasp.html>

## Recommended Implementation Order

1. **Protect scientific correctness**
   - remove MD constraints;
   - add the regression test;
   - enforce Stage0/Stage1 provenance compatibility.
2. **Make Slurm orchestration trustworthy**
   - parse untruncated states;
   - classify only explicit terminal states;
   - stop dependency chains on final failure;
   - persist submission state incrementally.
3. **Fix input rendering**
   - implement syntax-aware INCAR parsing;
   - cover valid VASP statement forms.
4. **Make collection transactional**
   - isolate per-source parsing;
   - atomically publish output;
   - preserve last-known-good datasets.
5. **Harden configuration and provenance**
   - validate regex patterns and numeric/cross-field constraints;
   - version manifests and record relevant hashes.
6. **Close test and CI gaps**
   - vendor fixtures;
   - exercise Slurm, symmetry reduction, packaging, and parser paths in CI.

## Completion Checklist for the Follow-up Branch

- [ ] MD POSCARs contain no unintended selective-dynamics constraints.
- [ ] Failed waited jobs cause a nonzero command result and stop dependencies.
- [ ] Held, configuring, requeued, and stage-out jobs continue to occupy slots.
- [ ] Failure states trigger at most one retry per calculation directory.
- [ ] Valid no-space and semicolon INCAR statements are rendered correctly.
- [ ] Stage1 rejects incompatible Stage0 provenance.
- [ ] Failed ML_ABN parsing cannot silently contribute partial frames.
- [ ] Dataset and manifest publication is atomic.
- [ ] A zero-frame collection preserves the previous dataset by default.
- [ ] All collection parser tests run without external machine-local fixtures.
- [ ] Full test suite and wheel build pass in a clean environment.
