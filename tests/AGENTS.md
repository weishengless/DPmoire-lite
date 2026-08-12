# Test and Fixture Rules

## TDD

- Add only the current unit's smallest named tests and fixtures.
- Run the focused command and observe the intended behavioral RED before GREEN.
- Import, syntax, path, fixture, permission, or private-data failures are not a
  valid RED.
- After minimal GREEN, run the focused suite, affected suite, and full suite.
- Never commit failing tests or retain future-unit RED tests in default discovery.

## Pytest Temporary Paths

- On Windows/Codex, every pytest gate uses scoped `require_escalated`
  non-sandbox execution.
- Use a unique, absolute basetemp named
  `$env:TEMP\dpmoire-pytest-<unit>-<gate>-<nonce>`.
- Before pytest, confirm the basetemp path does not exist; never reuse it.
- If scoped escalation is unavailable, report a blocker. Do not skip tests or
  classify an environment failure as behavioral RED.
- Do not change permissions to make a pytest gate pass.
- Do not use the default `pytest-of-*` temporary root in this environment.
- The repository ignore contract includes `/pytest-of-*/` for standard residue.

## Fixture Provenance and Privacy

- Keep fixtures minimal, deterministic, sanitized, and redistributable.
- Document source format, transformations, and purpose in the matching data
  README.
- Never read `example-test/` directly from automated tests.
- Never include POTCAR data, usernames, hostnames, cluster paths, or private
  calculation payloads.

## Portability

- A platform skip must name a real unavailable host capability and must not hide
  core behavior.
- Use synthetic portable data for positive and negative contracts.
- External ASE validation is recorded separately when the required version is
  not already available; do not install or change environments automatically.

## Verification

- Use the verified interpreter from the repository rules.
- Disable the pytest cache provider for repository checkpoints.
- Give RED, focused, affected, and full-suite gates distinct basetemp names.
- Report exact commands, exit codes, selected results, skips, and warnings.
