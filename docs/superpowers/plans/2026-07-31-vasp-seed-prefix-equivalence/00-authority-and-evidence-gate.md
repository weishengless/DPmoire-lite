# Ticket 00: Authority and Paired-evidence Gate

Depends on: explicit review of the proposed design

Unlocks: Ticket 01

## Goal

Turn the proposed scientific rule into approved planning authority and prove
that the required paired VASP evidence exists before any fixture or production
implementation begins.

This is a documentation/evidence checkpoint, not a behavior change. It requires
explicit user approval; creating these tickets does not approve the design.

## Files

Modify only after explicit approval:

- `docs/superpowers/specs/2026-07-31-vasp-mlff-seed-prefix-equivalence-design.md`
- `docs/code-review-notes/P2-1-partial-collection-and-mlff-seed.md`
- `docs/superpowers/plans/2026-07-11-code-review-followup/03-mlab-parser-digest.md`
- `docs/superpowers/plans/2026-07-11-code-review-followup/08-source-collection.md`
- `docs/superpowers/plans/2026-07-11-code-review-followup/10-collect-integration-cli.md`
- `docs/superpowers/plans/2026-07-31-vasp-seed-prefix-equivalence/README.md`

Read-only evidence scope:

- ignored local ML_AB/ML_ABN/OUTCAR evidence under `example-test/`;
- existing `tests/data/mlab/` fixture inventory.

## Evidence Gate

Confirm paired input/output evidence for each supported VASP version:

1. the exact ML_AB supplied as the initial seed;
2. the final ML_ABN whose ordered prefix came from that input;
3. associated VASP version evidence from OUTCAR or an equivalent authoritative
   record;
4. exact seed count and ordered structural correspondence;
5. maximum absolute and scaled deltas for lattice, positions, energy, forces,
   and raw stress.

The motivating VASP 6.5.1 pair is known. The existing corpus contains VASP 6.4.1
format evidence, but a paired read/rewrite relationship must be proven rather
than inferred from proximity or matching counts.

## Stop Conditions

Stop without edits beyond an evidence note if any of these holds:

- the user has not explicitly approved the proposed design;
- no trustworthy paired VASP 6.4.1 input/output evidence is available;
- either version exceeds the proposed
  `1e-12 * max(1, abs(a), abs(b))` rule;
- configuration order or a structural field differs;
- redistribution/sanitization permission is unclear;
- the new rule contradicts another authoritative scientific decision.

Request the missing evidence or return the numeric rule for design review. Do
not broaden the rule in this ticket.

## Authority Amendments

After the gate passes and approval is explicit:

1. Mark the design approved and record its evidence summary.
2. Amend P2-1 to distinguish exact identity from pairwise VASP equivalence.
3. State that the existing no-rounding rule still governs
   `mlab-seed-v1`/`mlab-config-v1`, not the separate verifier.
4. Add pointers from Plans 03, 08, and 10 to this ticket set without rewriting
   their completed historical checkpoints.
5. Mark this README approved for execution while keeping runtime progress out of
   tracked planning files.

## Verification

```powershell
rg -n "vasp-seed-prefix-equivalence-v1|mlab-seed-v1|no tolerance|full-dedup" `
  docs/code-review-notes/P2-1-partial-collection-and-mlff-seed.md `
  docs/superpowers/specs/2026-07-31-vasp-mlff-seed-prefix-equivalence-design.md `
  docs/superpowers/plans/2026-07-11-code-review-followup/03-mlab-parser-digest.md `
  docs/superpowers/plans/2026-07-11-code-review-followup/08-source-collection.md `
  docs/superpowers/plans/2026-07-11-code-review-followup/10-collect-integration-cli.md
git diff --check
git status --short
```

Expected: exact identities remain exact; pairwise equivalence is limited to seed
verification; full-dedup remains exact and independent; only the named documents
change.

## Checkpoint

Authority and paired VASP evidence are sufficient for fixture derivation. Stop;
do not start Ticket 01 in the same run.
