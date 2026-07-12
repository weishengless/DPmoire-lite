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
