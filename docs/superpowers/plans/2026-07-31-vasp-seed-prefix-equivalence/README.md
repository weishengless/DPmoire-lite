# VASP Seed-prefix Equivalence Implementation Tickets

Date: 2026-07-31

Status: approved for execution. Ticket 00 incorporated the design into P2-1 and
confirmed trustworthy paired VASP 6.4.1 and 6.5.1 evidence on 2026-07-31.

Design:
[VASP MLFF Seed-prefix Equivalence Verification Design](../../specs/2026-07-31-vasp-mlff-seed-prefix-equivalence-design.md)

## Objective

Repair default seed-aware collection for VASP-rewritten initial seed prefixes
while preserving exact `mlab-seed-v1`, exact `mlab-config-v1`, full-dedup,
source-local ownership, non-destructive no-data behavior, and existing CLI/config
interfaces.

## Execution Rules

1. Execute exactly one ticket per model run and stop after its green checkpoint.
2. Ticket 00 is a hard authority/evidence gate. Do not start production or test
   fixture edits before it is complete.
3. For every behavior ticket, observe the named RED before implementation. A
   valid RED fails for missing behavior, not imports, syntax, paths, private-data
   skips, or unavailable fixtures.
4. End every ticket with its focused, affected, and full-suite commands green.
   Do not leave later-ticket RED tests in default discovery.
5. Use only:

   ```powershell
   $python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
   & $python -c "import sys; print(sys.executable)"
   $env:PIP_NO_CACHE_DIR = '1'
   ```

6. Do not install or change packages without explicit user approval.
7. Read `tests/AGENTS.md` before editing tests and
   `src/dpmoire_lite/AGENTS.md` before editing production modules.
8. Never use `example-test/` as an automated-test dependency. Derived fixtures
   must be minimal, sanitized, documented, redistributable, and free of POTCAR
   or private calculation content.
9. Preserve `stage: all`, submitted `--wait`, deferred Slurm automation, MD
   restart, and fuzzy deduplication as existing fail-closed/non-goal areas.
10. Preserve unrelated tracked and untracked changes. Stage only the exact files
    owned by the active ticket, and commit only with explicit user authority.
11. If real VASP evidence contradicts the approved numeric rule, stop and return
    to the design. Do not tune tolerances during implementation.

## Ticket Index

| ID | Ticket | Depends on | Checkpoint |
| --- | --- | --- | --- |
| 00 | [Authority and paired-evidence gate](00-authority-and-evidence-gate.md) | proposed design | approved authority and sufficient VASP pairs |
| 01 | [Portable paired VASP fixtures](01-portable-paired-vasp-fixtures.md) | 00 | sanitized fixture inventory green |
| 02 | [Deep seed-prefix verifier](02-deep-seed-prefix-verifier.md) | 01 | exact/equivalent/mismatch core green |
| 03 | [Current and legacy reference adapters](03-current-and-legacy-reference-adapters.md) | 02 | lazy trusted-reference adapters green |
| 04 | [Seed-aware source integration](04-seed-aware-source-integration.md) | 03 | source candidate semantics green |
| 05 | [Publication diagnostics and final integration](05-publication-diagnostics-and-final-integration.md) | 04 | end-to-end/docs/audits green |

## Dependency Graph

```mermaid
flowchart LR
    T00["00 Authority + evidence"] --> T01["01 Portable fixtures"]
    T01 --> T02["02 Deep verifier"]
    T02 --> T03["03 Reference adapters"]
    T03 --> T04["04 Source integration"]
    T04 --> T05["05 Final integration"]
```

## Shared Module Shape

The implementation introduces one deep module at
`src/dpmoire_lite/mlff_seed.py`.

Its small interface is centered on:

```python
verifier = SeedPrefixVerifier.from_current_manifest(
    work_dir=work_dir,
    evidence=manifest.mlff_seed,
)
verification = verifier.verify(parsed_final.configurations)
```

The module hides exact fast-path identity, lazy reference loading, raw-hash and
canonical validation, the fixed versioned numeric rule, caching, and structured
diagnostics. `mlab.py` remains the parser/exact-identity owner. `collect.py`
remains orchestration and maps the returned result into source semantics.

Current Manifest v2 and legacy seed recovery are the two adapters at this seam.
Full-dedup does not use it.

## Completion Definition

The ticket set is complete only when:

- the proposed design and P2-1 are approved and consistent;
- paired sanitized VASP 6.4.1 and 6.5.1 read/rewrite evidence satisfies the
  approved rule;
- exact identities and full-dedup outputs are unchanged;
- real VASP rewrite noise is accepted only against a trusted initial reference;
- mismatch sources remain source-local and contribute zero frames;
- exact/equivalent/mismatch evidence is published transactionally;
- the motivating case produces 1,655 post-seed frames in the current partial
  state and remains degraded only because fourteen declared sources are absent;
- focused, affected, full, package, fixture-safety, and git audits pass;
- no private calculation or POTCAR content is tracked or packaged.
