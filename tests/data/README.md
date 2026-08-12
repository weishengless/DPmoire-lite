# Sanitized Test Fixtures

Only minimal, redistributable parser fixtures belong in this directory. Raw
files from `example-test/` or another private calculation directory must not be
copied here.

Each fixture addition must record all of the following fields in this file:

- **Source type:** the kind of source represented (for example, ML_AB, ML_ABN,
  or OUTCAR), without identifying a private path or calculation.
- **Reason for cropping:** the smallest parser behavior or edge case retained by
  the fixture.
- **Removed private data:** confirmation that user paths, job or account names,
  cluster details, unrelated output, and potential data were removed.
- **Redistribution confirmation:** why the remaining content is safe and
  permitted to redistribute; POTCAR content is never permitted.
- **Expected parser behavior:** the exact complete, partial, or invalid outcome
  and any frame/configuration count the fixture must produce.

No parser fixtures are introduced by Plan 00. Their owning parser plans will add
fixture-specific records below this policy before committing the corresponding
files.

## OUTCAR fixtures

### `outcar/complete_two_frame.OUTCAR`

- **Source type:** fully synthetic minimal OUTCAR-style text for stock ASE 3.28.
- **Reason for cropping:** retain only the structure required to parse two complete ionic frames.
- **Retained blocks:** synthetic species/count metadata plus cell, position/force, stress, and free-energy records.
- **Removed private data:** no source calculation was copied; paths, users, hosts, jobs, accounts, cluster details, and unrelated output are absent.
- **Redistribution confirmation:** all values and metadata are synthetic; the fixture contains no POTCAR or potential payload.
- **Expected parser behavior:** complete; two frames, each with energy, free energy, forces, and stress.

See `outcar/README.md` for the parser-specific compatibility boundary.

### Derived OUTCAR classification fixtures

The following Task 5 fixtures are derived only from the synthetic
`outcar/complete_two_frame.OUTCAR` above and contain no private calculation
data:

### `outcar/tail_truncated_second_frame.OUTCAR`

- **Source type:** synthetic OUTCAR stream with a structurally started but incomplete second ionic step.
- **Reason for cropping:** retain source lines 1–25 inclusive so the second step ends immediately after its single position/force row, exercising acceptable EOF-tail partial classification.
- **Retained blocks:** both synthetic species declarations, the first complete frame, and the second frame through its `POSITION/TOTAL-FORCE` row.
- **Removed private data:** no user paths, job or account names, host details, unrelated output, or potential payload was introduced; the source is only the committed synthetic fixture.
- **Redistribution confirmation:** all values are synthetic; the two `POTCAR: synthetic H` lines are OUTCAR species metadata permitted by the fixture policy, not POTCAR content.
- **Expected parser behavior:** one complete frame is yielded, then EOF follows a started second frame; the source classifier must retain the first sampled frame as PARTIAL.

### `outcar/internal_corruption_second_frame.OUTCAR`

- **Source type:** synthetic OUTCAR stream with internal corruption after one complete ionic step.
- **Reason for cropping:** retain the complete two-frame fixture and replace only the numeric second-frame position/force row at source line 25 with the nonnumeric token `BROKEN`, exercising fatal internal-corruption classification.
- **Retained blocks:** the complete synthetic header, first frame, second-frame cell and position/force block, and all final energy records.
- **Removed private data:** no private paths, jobs, accounts, host details, unrelated output, or potential payload was introduced.
- **Redistribution confirmation:** the fixture is a minimal transformation of the committed synthetic file; its `POTCAR: synthetic H` lines remain metadata only and no POTCAR is included.
- **Expected parser behavior:** the first frame may be observed before the second-frame parse error, but the classifier must return FAILED with zero accepted payload and must not salvage that prefix.
