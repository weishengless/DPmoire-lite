# Portable OUTCAR Fixture Inventory

This directory contains only minimal synthetic OUTCAR-style parser data. It
does not contain bytes copied from a private calculation or from a POTCAR.

## `complete_two_frame.OUTCAR`

- **Source type:** fully synthetic OUTCAR-style text written for the stock ASE 3.28 parser grammar.
- **Reason for cropping:** provide the smallest complete multi-frame reference for two ionic steps.
- **Retained blocks:** two synthetic `POTCAR:` species metadata declarations required by ASE, `ions per type`, the SCF delimiter, and per-frame cell, position/force, stress, free-energy, and zero-temperature energy records.
- **Removed private data:** no local calculation was copied; paths, usernames, hostnames, jobs, accounts, cluster details, and unrelated VASP output are absent.
- **Redistribution confirmation:** every numeric value and metadata token is synthetic. The `POTCAR:` lines are OUTCAR species declarations only; there is no POTCAR, pseudopotential table, licensed potential payload, or private calculation payload.
- **Expected parser behavior:** stock ASE 3.28 yields exactly two one-atom frames; each exposes finite energy, free energy, forces, and stress.

The ignored `example-test/` corpus and locally downloaded potential trees are
not test dependencies and were not read or copied to construct this fixture.

## `tail_truncated_second_frame.OUTCAR`

- **Source type:** derived synthetic OUTCAR stream for an acceptable EOF-tail partial source.
- **Reason for cropping:** retain complete source lines 1–25 inclusive and stop immediately after the second frame's single `POSITION/TOTAL-FORCE` row.
- **Retained blocks:** synthetic species/count metadata, the first complete ionic frame, and the second frame through its started position/force block.
- **Removed private data:** this file is derived only from `complete_two_frame.OUTCAR`; no private paths, jobs, accounts, hosts, unrelated output, or potential payload were added.
- **Redistribution confirmation:** every value is synthetic and redistributable. The existing `POTCAR: synthetic H` declarations are permitted OUTCAR species metadata, not POTCAR content.
- **Expected parser behavior:** the first frame completes, the second frame ends at EOF without a timing footer, and the source classifier reports PARTIAL while retaining the prior sampled prefix.

## `internal_corruption_second_frame.OUTCAR`

- **Source type:** derived synthetic OUTCAR stream for an internal second-frame parse failure.
- **Reason for cropping:** retain the complete fixture through its final energy records and replace the numeric second-frame position/force row at source line 25 with `BROKEN`.
- **Retained blocks:** the complete synthetic header, first frame, second-frame cell and position/force block, and both frames' stress/free-energy/zero-temperature records.
- **Removed private data:** no private calculation, path, job, account, host, unrelated output, or potential payload was introduced.
- **Redistribution confirmation:** the transformation uses only the committed synthetic fixture. Its `POTCAR: synthetic H` lines are metadata required by ASE and no POTCAR file or payload is present.
- **Expected parser behavior:** the first frame is observed before the nonnumeric second-frame row raises an internal parse error; the classifier reports FAILED with zero accepted payload and does not salvage the prefix.
