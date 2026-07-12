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
