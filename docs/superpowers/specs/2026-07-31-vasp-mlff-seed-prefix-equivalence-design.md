# VASP MLFF Seed-prefix Equivalence Verification Design

Date: 2026-07-31

Status: approved on 2026-07-31 and incorporated into P2-1 plus the focused
implementation ticket set.

## Purpose

Allow the default seed-aware MLFF collector to recognize an initial seed after
VASP has read and rewritten it with numerically insignificant float changes,
without weakening exact provenance, exact full-dedup identity, source-local
failure handling, or the fail-closed collection contract.

This design responds to a real VASP 6.5.1 calculation in the ignored local
`example-test/0-NbSe2H-TaSe2H` tree. Stage1 distributed a 63-configuration
`init_mlff/ML_ABN` byte-for-byte as every MD directory's `ML_AB`. The source raw
SHA-256 and `mlab-seed-v1` identity still match the Manifest v2 evidence. The
seven completed final `ML_ABN` files parse successfully, but VASP rewrote their
common seed prefix with maximum observed changes of approximately:

- positions: `1.0126e-13` angstrom;
- forces: `1.3878e-17` eV/angstrom;
- lattice, energy, stress, elements, counts, and ordering: unchanged.

Because `mlab-seed-v1` intentionally hashes parsed IEEE-754 values exactly, the
rewritten prefix has a different digest. Current seed-aware collection therefore
classifies every completed source as failed, accepts zero frames, and returns
`no_data`. Parsing the same final files without seed verification succeeds.

The raw local calculation is evidence only. It must not become an automated-test
dependency or be committed or packaged.

## Goals

- Preserve `mlab-seed-v1` as an exact scientific identity with no tolerance or
  rounding.
- Preserve `mlab-config-v1` exact identity and all full-dedup semantics.
- Verify that a final ML_ABN prefix is the ordered initial seed even when VASP
  has introduced bounded decimal round-trip noise.
- Keep structural and scientifically meaningful changes fail-closed.
- Keep the CLI and `config.yaml` interface unchanged.
- Record exact versus VASP-equivalent verification in structured source and
  result diagnostics.
- Concentrate reference validation, comparison, diagnostics, and policy in one
  deep module behind a small interface.
- Cover the real VASP read/rewrite behavior with minimal sanitized fixtures.

## Non-goals

- Do not change, replace, round, or reinterpret `mlab-seed-v1`.
- Do not use approximate equality for full-dedup, frame identity, training-data
  curation, or cross-source deduplication.
- Do not make tolerances configurable by users.
- Do not accept element, count, atom-order, configuration-order, shape, or
  finite-value changes.
- Do not infer seed provenance from current `md/<source>/ML_AB`, `ML_ISTART`,
  `ML_MODE`, OUTCAR, or agreement among final ML_ABN files.
- Do not silently replace the manifest's expected digest with a digest observed
  in final sources.
- Do not make `full-dedup` the default or change its intentional retention of one
  shared seed copy.
- Do not automate MD restart or relax any Slurm safety gate.

## Domain Model

The implementation and documentation must distinguish these terms.

### Exact seed identity

`mlab-seed-v1` is the exact canonical SHA-256 identity of an ordered seed
sequence. It proves exact parsed scientific content. Equivalent float text that
parses to the same float64 values has the same identity; numerically different
float64 values do not.

### Reference seed integrity

Manifest v2 records the initial reference at `mlff_seed.source`, its exact
configuration count, canonical identity, and the raw SHA-256 currently named
`ml_ab_sha256`. Before a non-exact comparison may use that reference, the source
file must remain within `work_dir`, exist, match the recorded raw SHA-256, parse
completely, contain exactly the recorded count, and reproduce the recorded
`mlab-seed-v1` identity.

### VASP seed-prefix equivalence

`vasp-seed-prefix-equivalence-v1` is a versioned pairwise verification rule. It
compares one trusted initial reference sequence directly with the same-length
ordered prefix of one final ML_ABN.

It is not an identity or deduplication schema. Approximate equality is not
transitive, so this rule must never produce a reusable approximate digest or
compare final sources with one another.

### Verification outcome

Each final source receives exactly one seed-prefix outcome:

- `exact`: final prefix has the expected `mlab-seed-v1` identity;
- `vasp_equivalent`: exact identity differs, but the trusted reference and final
  prefix satisfy the versioned pairwise rule;
- `mismatch`: the source is too short, the trusted reference cannot be
  established, a structural invariant differs, or a numeric delta exceeds the
  rule.

`exact` and `vasp_equivalent` both authorize removal of exactly the recorded seed
count. `mismatch` contributes zero frames.

## Selected Approach

Use exact verification as the fast path and trusted-reference pairwise
verification as a bounded fallback.

For each collection run:

1. Validate the typed manifest seed evidence without reading the reference
   source.
2. Parse each selected final ML_ABN under the existing complete/partial/failed
   rules.
3. Reject a source shorter than the recorded initial seed count.
4. Compute the exact `mlab-seed-v1` identity of the final prefix.
5. If it matches, return `exact` without requiring the reference file.
6. On the first exact mismatch, load and validate the trusted reference once;
   cache its success or structured failure for the remainder of the run.
7. Compare the ordered reference and final prefix configuration by
   configuration using `vasp-seed-prefix-equivalence-v1`.
8. If equivalent, return `vasp_equivalent`, remove exactly the recorded prefix,
   and expose the remaining parsed configurations to source aggregation.
9. Otherwise return `mismatch`, discard all parsed payload from that source, and
   record the first decisive mismatch plus aggregate delta evidence.

The lazy reference rule preserves current behavior for exact matches: an exact
final prefix remains collectable from a valid Manifest v2 even if the original
reference file is no longer present. A non-exact prefix needs the original,
raw-hash-verified reference because the manifest digest alone cannot support a
sound approximate comparison.

## Numeric Equivalence Rule

Structural values are exact:

- ordered element symbols and per-type counts;
- atom count;
- configuration count and sequence order;
- lattice, position, force, and stress array shapes;
- finite-value validation.

For each corresponding finite numeric scalar `a` from the trusted reference and
`b` from the final prefix, v1 accepts only when:

```text
abs(a - b) <= 1e-12 * max(1.0, abs(a), abs(b))
```

The comparison uses the existing ML_AB source units and conventions:

- lattice and Cartesian positions: angstrom;
- energy: eV;
- forces: eV/angstrom;
- raw VASP stress: kbar in `[xx, yy, zz, xy, yz, zx]` order.

The rule applies independently to lattice, positions, energy, forces, and raw
stress. Negative zero remains normalized by existing parsing/identity rules.
NaN and infinity remain parser failures.

The threshold is approximately 29 times the largest scaled difference observed
in the motivating VASP 6.5.1 prefix while remaining far below a scientifically
meaningful structural or label change. The approval evidence also includes a
paired VASP 6.4.1 input/output database: its 78-configuration input is the
ordered prefix of a 110-configuration output, with exact structure/order and
maximum observed changes of `8.6737e-19` angstrom in positions and
`6.9389e-18` eV/angstrom in forces. Both version pairs satisfy the v1 rule. If a
future supported VASP pair does not, the threshold returns for scientific
review; implementers must not raise it opportunistically.

The tolerance is fixed by the schema name. A future change requires a new
schema and design review.

## Deep Module and Seam

Add a focused seed-verification module, tentatively
`src/dpmoire_lite/mlff_seed.py`. Its interface should remain small:

```python
verifier = SeedPrefixVerifier.from_current_manifest(
    work_dir=work_dir,
    evidence=manifest.mlff_seed,
)
verification = verifier.verify(parsed_final.configurations)
```

A legacy adapter may construct the same verifier from the existing complete
`init_mlff/ML_ABN` recovery evidence. Current Manifest v2 and legacy recovery are
two real adapters at this seam; collection consumes one verification result.

The module implementation owns:

- typed seed-evidence validation;
- lazy, cached reference loading;
- raw SHA-256 and canonical identity validation of the reference;
- exact fast-path verification;
- v1 pairwise structural and numeric comparison;
- maximum absolute and scaled delta calculation;
- deterministic first-mismatch diagnostics.

The interface returns an immutable structured result and performs no publication
or manifest mutation. `mlab.py` continues to own parsing and exact canonical
identity. `collect.py` maps the structured verification to `SourceResult`, seed
removal, and aggregate status. CLI code remains unaware of comparison details.

Deleting this module would force reference integrity, numeric policy,
diagnostics, and caching back into multiple collection paths, so the module earns
its depth and provides locality for future VASP compatibility rules.

## Source and Aggregate Semantics

- Exact and VASP-equivalent complete sources remain `complete`.
- Exact and VASP-equivalent accepted tail-truncated sources remain `partial`.
- VASP equivalence alone does not make an aggregate `degraded`; it is an approved
  seed verification outcome, not lost coverage.
- A verification mismatch makes that source `failed` and contributes zero
  frames and zero identities.
- Other complete sources may still publish a degraded aggregate under the
  existing rules.
- If every available source fails or is skipped, the existing non-destructive
  `no_data` behavior remains.
- Missing declared ML_ABN files remain `skipped`; early collection behavior does
  not change.
- `full-dedup` remains an explicit independent mode and does not call the
  equivalence verifier.

## Diagnostics and Manifest Result

Accepted and failed MLFF source diagnostics add a `seed_verification` record:

```yaml
seed_verification:
  status: vasp_equivalent
  schema: vasp-seed-prefix-equivalence-v1
  configurations: 63
  expected_exact_sha256: "..."
  actual_exact_sha256: "..."
  reference:
    source: init_mlff/ML_ABN
    raw_sha256: "..."
    trust: manifest-v2
  max_deltas:
    lattice: {absolute: 0.0, scaled: 0.0}
    positions: {absolute: 1.0126e-13, scaled: 3.4626e-14}
    energy: {absolute: 0.0, scaled: 0.0}
    forces: {absolute: 1.3878e-17, scaled: 1.3878e-17}
    stress_kbar: {absolute: 0.0, scaled: 0.0}
```

Exact matches may omit zero-valued delta maps but must record `status: exact` and
the exact identities. Mismatches record the first configuration index, field,
component index when applicable, expected/actual values, absolute delta, scaled
delta, and stable reason. Diagnostics must not dump full configurations.

The aggregate collect record includes counts of exact, VASP-equivalent, and
mismatched seed verifications. Existing source and frame counts remain
authoritative.

## Considered Approaches

### Change or round `mlab-seed-v1`

Rejected. It would silently change existing manifests, exact full-dedup identity,
and the meaning of an approved schema.

### Store a tolerance-quantized digest

Rejected. Quantization has bucket-edge failures, while approximate equality is
not transitive. Direct comparison with one trusted reference is simpler and more
auditable.

### Accept a digest shared by several final sources

Rejected. Several calculations can repeat the same wrong or manually replaced
seed. Agreement among consumers does not prove agreement with the producer.

### Rewrite the manifest with the observed final digest

Rejected. This would replace immutable producer evidence with consumer output
and make provenance circular.

### Always use full-dedup

Rejected as the default fix. It intentionally retains one initial seed copy,
changes dataset semantics, and performs extra I/O. It remains the explicit
operational workaround and legacy recovery mode.

### User-configurable tolerances

Rejected. A user could unknowingly weaken provenance until distinct
configurations pass. Compatibility policy belongs to a reviewed versioned rule,
not per-run configuration.

## Compatibility

### Current Manifest v2

No CLI, config, or top-level manifest schema change is required. Existing
`mlff_seed.source`, `configurations`, `digest_schema`, `seed_prefix_sha256`, and
`ml_ab_sha256` provide the required producer evidence.

Exact matches remain backward-compatible and do not require the reference file.
The fallback accepts a rewritten prefix only after the existing source bytes are
re-established from the raw hash.

### Legacy manifest

Legacy seed-aware collection already rebuilds evidence from a complete
`work/init_mlff/ML_ABN`. The legacy adapter may use the same pairwise rule after
recording `trust: legacy-rebuilt` and the existing warning. Missing or incomplete
legacy reference evidence remains a source failure.

### Missing manifest and full-dedup

Missing-manifest seed-aware collection remains fatal. Explicit full-dedup
inventory, exact deduplication, compatibility result manifests, and status rules
remain unchanged.

## Performance Contract

- Exact-path work remains one prefix hash per accepted source.
- Reference bytes are hashed and parsed at most once per collection run, and only
  after an exact mismatch.
- Pairwise verification is linear in the seed atom fields of each non-exact
  source.
- No final-source pairwise comparison, RMSD, symmetry matching, distance matrix,
  or fuzzy deduplication is allowed.
- Failed sources do not mutate the aggregate candidate or any exact-identity
  index.

## Test Strategy

### Required behavioral RED

Create a minimal sanitized paired fixture from the motivating VASP 6.5.1 data:

- the input seed configuration as supplied in ML_AB;
- the same configuration as rewritten in the final ML_ABN prefix;
- one distinct post-seed configuration sufficient to prove prefix removal.

The current default seed-aware collector must reproduce the exact bug: complete
parse, seed-prefix mismatch, zero accepted post-seed frames. This becomes the
behavioral RED before implementation.

Automated tests must use only the sanitized fixture and never read
`example-test/`.

### Verification matrix

- exact digest match returns `exact` without reading the reference source;
- the paired VASP 6.5.1 rewrite returns `vasp_equivalent` and collects only the
  post-seed frame;
- a paired sanitized VASP 6.4.1 rewrite satisfies the same v1 rule;
- whitespace-only and equivalent float-text changes still take the exact path;
- a numeric delta exactly at the v1 threshold passes;
- a delta immediately above it fails with field/component context;
- element, count, atom order, configuration order, and shape changes fail;
- lattice, position, energy, force, and stress changes are independently tested;
- missing, escaping, raw-hash-mismatched, incomplete, or canonical-mismatched
  references fail closed on the fallback path;
- exact matches still work when the reference file is absent;
- a reference is loaded at most once across several rewritten sources;
- one failed source cannot pollute accepted frames or identities;
- equivalent partial-tail sources retain only complete post-seed frames;
- early collection with missing ML_ABN directories remains degraded when other
  sources contribute frames;
- full-dedup results and exact duplicate counts remain byte-for-byte unchanged.

### Real-case confirmation

After focused tests pass, run the original ignored calculation only as a manual
confirmation. The expected seed-aware result for its present state is:

- seven VASP-equivalent completed sources;
- fourteen skipped sources without ML_ABN;
- zero seed-verification failures;
- exactly 1,655 accepted post-seed frames;
- aggregate `degraded` because declared source coverage is incomplete.

Do not record or commit private frame content.

## Fixture and Data-safety Rules

- Derive the smallest fixture that preserves the observed float deltas and VASP
  format labels.
- Remove usernames, hostnames, job/account identifiers, absolute paths, comments,
  and unrelated configurations.
- Never copy POTCAR, ML_FF/ML_FFN, WAVECAR, CHGCAR, or other private calculation
  output into tests.
- Document VASP version evidence, crop boundaries, original field units,
  expected deltas, and redistribution confirmation in `tests/data/mlab/README.md`.
- Verify `git ls-files` and package contents contain no `example-test/` path or
  private source.

## Incorporated Authority and Execution Tickets

Approval incorporated the scientific decision into P2-1. The focused
[implementation ticket set](../plans/2026-07-31-vasp-seed-prefix-equivalence/README.md)
owns the remaining execution traceability:

- P2-1: distinguish exact seed identity from pairwise VASP prefix equivalence;
  limit the existing no-rounding rule to identity/dedup schemas; define the
  approved fallback and diagnostics.
- Plan 03: add paired VASP read/rewrite fixtures and the structured verifier
  models without changing canonical identity.
- Plan 08: create one verifier per candidate, reuse it across sources, and map
  mismatch outcomes to source-local failure.
- Plan 10: publish seed-verification diagnostics and add the real producer/VASP
  consumer regression to final integration coverage.
- README/workflow: explain that default seed-aware collection accepts the
  versioned VASP rewrite rule while still excluding the initial seed.

Execution follows the repository rule of one behavioral RED-to-green checkpoint
per model run.

## Acceptance Criteria

This design is complete only when:

- `mlab-seed-v1` and `mlab-config-v1` exact identities are unchanged;
- no approximate comparison is reachable from full-dedup;
- exact matches retain their current fast path and reference-file independence;
- a raw-hash-verified reference allows real VASP 6.4.1/6.5.1 rewrite noise but
  rejects changes outside the reviewed v1 rule;
- default seed-aware collection excludes exactly the initial seed count and
  accepts all valid post-seed frames in the motivating case;
- structured diagnostics distinguish exact, VASP-equivalent, and mismatch
  outcomes without dumping private configurations;
- reference loading is lazy, cached, path-contained, and fail-closed;
- complete/partial/skipped/failed source ownership and aggregate status remain
  internally consistent;
- focused, full, packaging, private-data, and git-diff checks pass;
- no raw private calculation or POTCAR content is tracked or packaged.
