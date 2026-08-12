# Production Module Rules

## Authority

- Scientific and failure contracts come from the
  [authoritative notes](../../docs/code-review-notes/README.md) and approved
  plans; do not copy their algorithms into this file.
- Preserve public behavior unless the active execution capsule names a change.

## Module Ownership

- `config.py` validates configuration and fail-closed safety gates.
- `incar.py` owns INCAR parsing, diagnostics, and conservative rendering.
- `manifest.py` owns Manifest schema validation, classification, and atomic
  manifest I/O boundaries.
- `mlab.py` owns ML_AB parsing and canonical identities.
- `structures.py` owns structure parsing and structure-domain preparation.
- `inputs.py` owns VASP input preparation and source selection.
- `provenance.py` owns structure identity and provenance-domain results.
- `build_preflight.py` orders validators and builds immutable prepared results.
- `build.py` generates only from prepared results.
- Collection parsers, models, publication, orchestration, and CLI layers keep
  their documented responsibilities separate.

## Cross-module Invariants

- Parsers do not publish, mutate workflow state, or choose CLI outcomes.
- Publication code does not reinterpret scientific sources.
- CLI code maps structured outcomes; it does not parse text logs for status.
- Preflight remains the aggregate no-side-effect interpretation boundary.
- Generation does not reparse or reselect already prepared inputs.
- Manifests and provenance results remain the cross-stage authority.
- Immutable results use tuple-like collections and copy mutable ASE values at
  ownership boundaries.

## Change Discipline

- Keep changes inside capsule-named modules and tests.
- Avoid duplicate compatibility readers, generic workflow frameworks, and
  unrelated refactors.
- Preserve fail-closed automation, path-containment, and one-shot publication
  semantics while editing adjacent code.
