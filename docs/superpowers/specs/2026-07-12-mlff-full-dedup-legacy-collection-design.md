# MLFF Full-dedup and Legacy Collection Design

Date: 2026-07-12

Status: approved and incorporated into authoritative notes and plans.

## Purpose

Keep the normal DPmoire-lite MLFF workflow seed-aware while adding an explicit
recovery path for older or manually modified MD calculation trees. The recovery
path must collect every complete ML_ABN configuration that can be accepted under
the existing partial-source rules, then remove only configurations whose complete
canonical scientific content is identical.

This design is motivated by a real older calculation in which on-the-fly MLFF was
started without an external database and was later restarted. The first supplied
OUTCAR segment records the historical `ML_ISTART=0` control and the later segment
records `ML_ISTART=1`; the current directory contains an `ML_AB` with 44
configurations and a final `ML_ABN` with 105. The exact copy-time provenance of
the retained `ML_AB` is not needed for the rule: in a fresh-start restart chain,
treating the current `ML_AB` count as an initial external-seed count can discard
valid configurations produced before the latest restart.

Newer VASP versions use `ML_MODE` rather than requiring users to set
`ML_ISTART`. Collection therefore must not infer the initial seed policy from
either tag. The default workflow's Manifest v2 seed record remains authoritative;
the optional recovery mode avoids seed inference by collecting all complete
configurations and deduplicating them exactly.

The local evidence inventory is recorded in the ignored file
`example-test/0-walltime_restart/README.md`. Raw `example-test/` files are not
production dependencies and must never be committed or packaged.

## Goals

- Leave the default build workflow and its initial-MLFF seed distribution
  unchanged.
- Keep seed-aware collection as the default.
- Let a user explicitly select full ML_ABN collection plus exact deduplication.
- Preserve data accumulated before one or more manual MD restarts.
- Support prior DPmoire-lite output layouts and verified VASP 6.4.1/6.5.1
  ML_AB/ML_ABN formats.
- Keep source classification, atomic publication, and CLI outcomes auditable.
- Make the optional path linear in the amount of parsed scientific data, with no
  pairwise structure comparison.

## Non-goals

- Do not change Stage0 or Stage1 generation behavior.
- Do not automate MD restart.
- Do not infer behavior from `ML_ISTART`, `ML_MODE`, or the current `md/ML_AB`.
- Do not collect historical `ML_ABN0`, `ML_ABN1`, or restart snapshots; only the
  final `ML_ABN` in each selected MD directory is a source.
- Do not perform fuzzy, tolerance-based, RMSD, symmetry, or nearest-neighbor
  deduplication.
- Do not accept deprecated DPmoire configuration key names. Compatibility uses
  the current CLI and a current-format configuration pointing to an old
  `work_dir`.
- Do not recursively search arbitrary directory trees.
- Do not introduce a disk-backed Dataset or SQLite dedup index in this change.

## Considered Approaches

### Explicit dual-mode collection

Keep seed-aware behavior as the default and add an explicit full-collection mode
with exact canonical deduplication. This preserves current output semantics while
providing a safe path for older or manually changed calculations.

This is the selected approach.

### Automatic VASP-tag inference

Inspect `ML_ISTART`, `ML_MODE`, or an available OUTCAR series and automatically
choose a seed policy. This is rejected because tag usage differs across VASP
versions, early restart segments may be absent, and a silent wrong inference can
lose valid data.

### Always collect all and deduplicate

Remove seed-aware behavior and always deduplicate complete ML_ABN contents. This
is rejected because it changes the default dataset semantics, always retains one
copy of the initial seed in `MD_data.extxyz`, and imposes unnecessary parsing on
the standard workflow.

## User-facing Contract

The collect CLI adds:

```text
--mlff-collect-mode seed-aware|full-dedup
```

The default is `seed-aware`. The option applies only when `--stage md` and
`vasp_ml: true`; an explicit non-default value for relaxation, validation, or
non-ML MD is a CLI usage error.

Examples:

```powershell
DPmoireLite collect config.yaml --stage md
DPmoireLite collect config.yaml --stage md --mlff-collect-mode full-dedup
```

The CLI value maps to a typed core enum. Core code must not branch on an
unvalidated free-form string.

## Source Inventory and Compatibility

Source inventory follows this precedence:

1. A valid Manifest v2 supplies the exact declared directories.
2. A legacy manifest with no schema marker supplies its declared directories
   after the same work-dir path-boundary validation.
3. A missing stage manifest is accepted only for explicit `full-dedup`. The
   collector scans direct children of `work_dir/md`, selects directories that
   contain an exact `ML_ABN` filename, and orders them naturally.
4. An invalid YAML manifest, unsupported explicit schema version, invalid v2
   manifest, or directory escape is fatal. It must never fall back to a scan.

The missing-manifest scan:

- is one directory level only;
- accepts ordinary stacking names and named monolayer directories without
  assuming a particular numeric grid;
- rejects resolved paths outside `work_dir`, including resolvable symlink
  escapes;
- ignores files, backups, nested descendants, `ML_AB`, `ML_ABN0`, and other
  similarly named artifacts;
- records every discovered relative directory and `directory_discovery:
  legacy-scan`.

`seed-aware` with a missing manifest remains fatal. The scan is an explicit,
bounded exception to the existing rule that a missing stage manifest is fatal;
it is not a general relaxation of manifest validation.

Compatibility covers the prior DPmoire-lite layout
`work_dir/md/<source>/ML_ABN`. It does not cover arbitrary earlier DPmoire layouts
without a separately reviewed adapter.

## Collection Data Flow

Both modes share deterministic inventory, structured ML_ABN parsing, EOF
classification, source-local ownership, and final publication.

### Seed-aware mode

For a current Manifest v2, read the immutable initial seed count, digest schema,
and prefix digest, verify the final ML_ABN prefix, and expose only configurations
after that prefix.

For a supported legacy manifest, retain the existing strict recovery policy:
derive seed evidence only from a complete `work/init_mlff/ML_ABN`, verify the
final prefix, and never infer the count from a possibly grown current `md/ML_AB`.

### Full-dedup mode

Do not require, infer, or skip an initial seed. For every selected final ML_ABN:

1. Parse configurations in file order.
2. Classify the source as complete, accepted tail-partial, or failed before it
   can mutate the aggregate candidate.
3. For an accepted source, process every complete configuration in order.
4. Compute its exact canonical identity.
5. Retain and convert the configuration to ASE only if that identity has not
   already been accepted.

The global order is inventory order followed by configuration order. The first
occurrence wins. Its source metadata is retained on the output frame. Later
identical occurrences increase duplicate counts but do not enter the Dataset.

If final ML_ABN files contain a common init seed, `full-dedup` retains that seed
once in `MD_data.extxyz`. If a source was started from an empty on-the-fly MLFF
database, every unique configuration remains. This difference from `seed-aware`
is intentional and must be documented in CLI help.

## Exact Configuration Identity

Plan 03 must expose one canonical configuration serializer rather than creating a
second scientific encoding in the collector. The same canonical configuration
bytes are used by the existing `mlab-seed-v1` prefix identity and the new
per-configuration identity. Adding `mlab-config-v1` must not change the sequence
framing or digest semantics of `mlab-seed-v1`.

`mlab-config-v1` hashes exactly one configuration and includes:

- ordered element symbols and per-type counts;
- atom count;
- 3x3 lattice in angstrom;
- Cartesian positions in angstrom, in file order;
- total energy in eV;
- forces in eV/angstrom, in file order;
- raw VASP stress in kbar ordered `[xx, yy, zz, xy, yz, zx]`.

It excludes source path, configuration number, comments, whitespace, and float
text formatting. Before serialization:

- all numbers are finite IEEE-754 float64 values;
- `-0.0` becomes `+0.0`;
- no tolerance rounding is applied;
- array shapes and element/type counts are validated;
- fixed-width big-endian values, UTF-8 strings, and explicit length/shape
  prefixes follow the `mlab-seed-v1` rules.

Deduplication happens on parsed ML_ABN values before ASE stress sign/unit
conversion. Equality means the same `mlab-config-v1` SHA-256 identity. No
geometry-only or approximate equality is allowed.

## Source-local Commit Boundary

Hash calculation may occur while a source is parsed, but a source cannot update
the global `seen` set or aggregate Dataset until its final classification is
known.

- A complete source contributes all of its unique configurations.
- An accepted EOF-tail partial source contributes its complete prefix.
- A first-configuration truncation or internal-corruption failure contributes
  zero configurations and zero committed identities.
- Duplicate detection never turns an otherwise complete source into partial or
  failed.

This preserves the existing rule that a failed source cannot leave hidden frames
or identities in the aggregate result.

## Performance Contract

`full-dedup` is a single-pass hash-index operation:

```text
parsed configuration -> incremental canonical SHA-256 -> seen-set lookup
```

- Time is linear in the total parsed atom fields.
- The implementation feeds canonical fields incrementally to the hash and does
  not materialize a file-sized canonical byte buffer.
- The index stores one fixed-size digest per unique configuration plus source
  counts.
- Duplicate configurations are not converted to ASE.
- Pairwise RMSD, distance matrices, symmetry matching, and nested frame loops are
  prohibited.

The mode still reads repeated seed bytes from each source and therefore performs
more I/O than `seed-aware`. That is an explicit correctness/performance trade-off
selected by the user. The current in-memory `CollectionCandidate` remains the
dominant dataset storage boundary; disk streaming is deferred.

Tests assert one identity calculation per accepted complete configuration and
deterministic output, not a machine-specific wall-clock threshold.

## Result and Manifest Contract

The aggregate result records at least:

- `collection_mode`;
- `dedup_schema` when applicable;
- total complete configurations seen;
- unique configurations retained;
- duplicates removed;
- per-source seen, retained, and duplicate counts;
- input manifest/layout kind and directory discovery method;
- complete, partial, skipped, and failed source diagnostics;
- deterministic selected source order.

Duplicates are expected in `full-dedup` and do not make the aggregate degraded.

For a valid current Manifest v2, the existing atomic publication transaction
updates the stage manifest's collect section.

For a legacy or missing stage manifest, the collector must not overwrite the
legacy file or fabricate build provenance. It instead publishes
`work_dir/MD_data.collect.yaml` as the result manifest paired atomically with
`work_dir/MD_data.extxyz`. The result manifest carries its own schema version,
transaction ID, output hash, inventory evidence, and source diagnostics. Plan 09
must accept this validated alternate result-manifest path without weakening its
lock, journal, candidate-validation, or recovery rules.

The compatibility result manifest is authoritative only while no valid current
MD Manifest v2 exists and only when its recorded output hash matches the current
dataset. If a current v2 manifest is later introduced, the current stage manifest
takes precedence; a mismatching older compatibility result is historical
evidence, not current state.

## Aggregate Status and Exit Codes

Existing status meanings remain, with one explicit missing-manifest exception:

- Current or legacy declared inventory, at least one retained frame, and all
  declared sources complete: `complete` / exit 0.
- Missing-manifest legacy scan with retained frames: at best `degraded` / exit 2,
  even if every discovered source is complete, because expected-source coverage
  is unknown.
- Any accepted frames plus partial/skipped/failed sources: `degraded` / exit 2.
- Zero accepted frames without a fatal invariant failure: `no_data` / exit 3;
  preserve any previous output according to the publication contract.
- Invalid config, invalid/escaping manifest, unsupported mode/stage combination,
  or publication/recovery failure: `fatal` / exit 1.

Duplicate removal alone does not affect status. A `full-dedup` run in which all
seen configurations collapse to duplicates still succeeds if at least one unique
frame is present in the final candidate.

## VASP and Layout Compatibility

The portable fixture corpus must contain minimal sanitized samples derived from:

- a VASP 6.4.1 ML_ABN;
- a VASP 6.5.1 ML_AB/ML_ABN.

Tests verify the stable labeled blocks and scientific parse result rather than
hard-coded header line numbers or basis-set lengths. If a future VASP format
changes an authoritative block, the parser must fail with block/location context
until a versioned fixture and explicit compatibility rule are added.

The collector does not require `ML_FF` or `ML_FFN` because they are not dataset
sources. It does not inspect `ML_ISTART` or `ML_MODE` to decide collection mode.

## Test Matrix

### CLI and default preservation

- omitted mode equals explicit `seed-aware`;
- `full-dedup` is accepted only for MLFF MD;
- non-ML MD, relaxation, and validation reject the non-default mode;
- the current build config and generated workflow are unchanged.

### Exact identity

- duplicates within one source and across sources are removed;
- equivalent whitespace and float text formatting deduplicate;
- changes to element order, cell, position, energy, force, or raw stress remain;
- negative zero normalizes, while NaN/Inf fail parsing;
- deterministic first occurrence and source metadata are preserved;
- no fuzzy comparison API is called.

### Seed and restart behavior

- `seed-aware` verifies and excludes the immutable initial seed;
- `full-dedup` retains one copy of a repeated seed;
- fresh on-the-fly MLFF retains all unique final configurations;
- a restart-time `ML_AB` count cannot remove configurations produced before the
  restart;
- multiple final sources with different seeds retain their distinct scientific
  configurations.

### Source failure boundary

- complete, EOF-tail partial, first-frame truncated, and internally corrupt
  sources follow the existing status rules;
- a failed source cannot pollute the global identity index;
- a partial source contributes and deduplicates only its complete prefix.

### Compatibility inventory

- valid current v2 uses declared directories;
- legacy manifest uses validated declared directories without being rewritten;
- missing manifest is fatal in `seed-aware`;
- explicit `full-dedup` scans only immediate MD children with exact `ML_ABN`;
- scan order is natural and deterministic;
- backups, nested paths, similarly named files, and path escapes are excluded;
- invalid or unsupported v2 is fatal and never falls back to scan;
- missing-manifest output is degraded because coverage is unknown.

### Publication

- current v2 updates its collect section atomically;
- legacy/missing collection publishes the compatibility result manifest without
  rewriting source provenance;
- result-manifest/output hashes and transaction IDs match;
- fault injection and recovery cover the alternate result-manifest path;
- no-data and fatal runs preserve prior output according to the existing table.

## Fixture and Data-safety Rules

- Read `example-test/0-walltime_restart/README.md` before deriving new fixtures.
- Use only the minimum blocks needed to demonstrate one parser or compatibility
  boundary.
- Remove usernames, hostnames, account/job information, absolute paths, and
  unrelated output.
- Never commit POTCAR, potential content, force-field binaries, or a complete
  calculation output.
- Record source type, VASP version evidence, crop boundaries, expected fields,
  and redistribution confirmation in the tracked fixture README.
- Automated tests must not read `example-test/`.

## Required Specification and Plan Amendments

After this written design is approved, update the existing corpus before
implementation:

- P2-1: retain seed-prefix verification as the default and add the explicit
  full-collection/exact-dedup alternative.
- P2-6: keep missing manifest fatal by default, but allow explicit
  `full-dedup` legacy scan with degraded coverage and a compatibility result
  manifest.
- Plan 03: expose reusable per-configuration canonical identity and add VASP
  6.4.1/6.5.1 sanitized fixtures.
- Plan 08: keep parsed ML_ABN payloads source-local and expose the default
  seed-aware candidate without eager ASE conversion.
- Plan 08A: add the mode contract, exact dedup fold, bounded legacy/missing
  inventory, and source-local identity commit boundary as a separate checkpoint
  so Plan 08 does not become oversized.
- Plan 09: support the validated alternate compatibility result-manifest path in
  the same publication/recovery engine.
- Plan 10: wire the CLI enum, validation, audit fields, and exact exit codes.
- Roadmap and low-reasoning prompt: describe the new evidence, mode boundary,
  fixture rules, and checkpoint sequence.

Plan 06's normal Stage1 seed producer remains unchanged.

## Acceptance Criteria

This design is implemented only when:

- default collection output remains seed-aware and regression-compatible;
- explicit `full-dedup` never uses current `ML_AB` to skip configurations;
- exact canonical duplicates are removed in deterministic first-seen order;
- near or scientifically different configurations are retained;
- the implementation is linear and contains no pairwise comparison path;
- current, legacy, and explicitly scanned missing-manifest layouts follow their
  distinct safety and audit rules;
- VASP 6.4.1 and 6.5.1 fixture suites pass;
- output frame counts equal the sum of unique accepted source contributions;
- every published output has a matching current or compatibility result manifest
  and recoverable transaction;
- raw local calculation files and POTCAR content remain untracked and unpackaged.
