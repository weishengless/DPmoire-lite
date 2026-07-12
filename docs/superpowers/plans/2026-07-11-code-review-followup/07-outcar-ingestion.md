# Plan 07: OUTCAR Discovery and Streaming

Authoritative specs:

- [P2-4 OUTCAR pattern validation](../../../code-review-notes/P2-4-outcar-pattern-validation.md)
- [P2-5 OUTCAR streaming](../../../code-review-notes/P2-5-outcar-streaming.md)

Depends on:

- [Plan 00 Containment baseline](00-containment-baseline.md)
- [Plan 06R Route correction and execution governance](06r-route-correction-execution-governance.md)

Unlocks: Plan 08

## Goal and Done State

Provide one deterministic OUTCAR ingestion boundary that validates regex
configuration early, reports exactly which files were selected and why, and
streams frames through ASE's VASP-specific file-object iterator without
preloading the whole trajectory.

Done means:

- `outcar_patterns` is a nonempty list of nonempty valid regex strings;
- default history families precede the active unnumbered OUTCAR;
- selection order is pattern priority then natural filename order, independent
  of mtime;
- one file matching multiple patterns appears once under its first pattern;
- the selection result records path, pattern index, and order;
- frames are consumed inside a context manager using an open file object;
- early break, error, and normal completion close the handle deterministically;
- strict UTF-8 errors are visible;
- first-frame laziness is tested as behavior, not by checking `generator` type.

## Non-goals

- Do not decide complete/partial/failed source status; Plan 08 owns it.
- Do not aggregate across files or change per-file sampling origin.
- Do not use mtime as a fallback ordering signal.
- Do not use generic `ase.io.iread(format="vasp-out")`.
- Do not add disk-streaming extxyz aggregation.
- Do not install ASE 3.29 without approval.

## Files

Create:

- `tests/data/outcar/README.md`
- a minimal complete multi-frame OUTCAR under `tests/data/outcar/`

Modify:

- `src/dpmoire_lite/config.py`
- `src/dpmoire_lite/outcar.py`
- `src/dpmoire_lite/dataset.py`
- `tests/test_config.py`
- `tests/test_outcar.py`
- `tests/test_collect.py` only for the existing sampling adapter
- `tests/data/README.md`
- `example/config.yaml` and `src/dpmoire_lite/example/config.yaml` if default
  patterns are shown explicitly

## Required Interfaces

OUTCAR discovery returns structured selections, not just paths. Each selection
contains:

- path;
- matched pattern text/index;
- position in deterministic processing order.

Streaming exposes a context manager equivalent in semantics to:

```python
with open_outcar_frames(path) as frames:
    for frame in frames:
        ...
```

The iterator cannot escape the file-handle lifetime.

## Task 1: Add Strict Configuration Tests

Add failing tests:

- `test_outcar_patterns_defaults_to_historical_families_then_active()`;
- `test_outcar_patterns_rejects_scalar_string()`;
- `test_outcar_patterns_rejects_empty_list()`;
- `test_outcar_patterns_rejects_empty_string_item()`;
- `test_outcar_patterns_rejects_mixed_types()`;
- `test_outcar_patterns_rejects_invalid_regex_with_index()`;
- `test_outcar_patterns_deduplicates_exact_text_with_warning()`.

The error for a scalar string must include a correct YAML-list example.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_config.py -q -p no:cacheprovider -k "outcar_patterns"
```

Expected failure: current code converts any iterable with `tuple(...)` and
compiles regex only during discovery.

Implementation:

1. Validate list shape and item types before tuple conversion.
2. Compile each regex during config load for validation.
3. Preserve first occurrence order when removing exact duplicates.
4. Set defaults in this order: numbered `OUTCAR`, numbered `OUT`, numbered
   lowercase `out`, then unnumbered `OUTCAR`.

Run the focused command to green.

Checkpoint: config contract.

## Task 2: Implement Deterministic Structured Discovery

Add failing tests:

- `test_find_outcar_series_uses_pattern_priority_then_natural_sort()`;
- `test_outcar2_precedes_outcar10()`;
- `test_default_unnumbered_outcar_is_last()`;
- `test_discovery_order_is_independent_of_mtime()`;
- `test_file_matching_multiple_patterns_uses_first_once()`;
- `test_selection_records_pattern_index_and_order()`;
- `test_no_match_returns_empty_selection()`.

Replace the current mtime test rather than preserving old semantics.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_outcar.py -q -p no:cacheprovider -k "series or discovery or order or mtime"
```

Implementation requirements:

1. Assign each file to its first matching pattern.
2. Natural-sort names inside each pattern family.
3. Concatenate families by pattern order.
4. Select files only; reject directories and do not open contents here.
5. Return immutable selection records suitable for Plan 08 manifest diagnostics.

Run the focused command to green.

Checkpoint: discovery behavior.

## Task 3: Add a Sanitized Multi-frame OUTCAR Fixture

Create a minimal complete fixture that ASE 3.28 can parse for at least two ionic
steps with energy, forces, and stress. Record in `tests/data/outcar/README.md`:

- synthetic/cropped origin;
- retained parser blocks;
- removed paths, hostnames, accounts, and unrelated VASP output;
- no POTCAR or potential content;
- redistribution confirmation;
- expected frame count and fields.

If `example-test/0-walltime_restart/README.md` exists, read it first. The listed
MD OUTCAR segments are natural walltime-tail source evidence: each has hundreds
of complete force/free-energy blocks, no normal timing footer, and later
electronic output at EOF. A minimal sanitized crop may be derived from one of
those files, but the fixture record must name the relative source segment and
exact crop boundary. The test must establish what ASE yields; marker counts and
a missing footer are supporting evidence, not the parser result.

Add tests:

- `test_outcar_fixture_inventory_has_provenance_entry()`;
- `test_outcar_fixture_parses_expected_frames_and_properties()`;
- `test_outcar_fixture_contains_no_private_path_or_potcar_marker()`.

Do not copy a large local OUTCAR into the committed fixture corpus.

## Task 4: Implement the Context-managed File-object Iterator

Add failing tests:

- `test_open_outcar_frames_passes_file_object_to_iread_vasp_out()`;
- `test_open_outcar_frames_reads_first_frame()`;
- `test_first_frame_is_yielded_before_file_position_reaches_eof()`;
- `test_context_closes_handle_after_full_consumption()`;
- `test_context_closes_handle_after_break()`;
- `test_context_closes_handle_after_iterator_error()`;
- `test_invalid_utf8_is_not_ignored()`;
- `test_streamed_frames_match_small_batch_reference_semantics()`.

Use a recording wrapper for `read`, `readline`, `tell`, and `close`. A test that
only asserts the return value is a generator is insufficient.

Red command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_outcar.py -q -p no:cacheprovider -k "open_outcar or first_frame or closes or utf8 or streamed"
```

Implementation requirements:

1. Open text with `encoding="utf-8", errors="strict"`.
2. Call VASP-specific `iread_vasp_out(fd, index=":")` directly.
3. Yield the iterator only inside a context manager.
4. Do not accept a path string in the low-level call.
5. Close on every context exit path.
6. Do not materialize `list(frames)`.

Run the focused command to green.

Checkpoint: streaming wrapper.

## Task 5: Apply Sampling During Streaming

Add/adjust tests:

- `test_stream_sampling_freq_one_accepts_every_frame()`;
- `test_stream_sampling_accepts_zero_n_2n_per_file()`;
- `test_sampling_index_resets_for_each_outcar_segment()`;
- `test_nonpositive_frequency_fails_before_opening_file()`;
- `test_unselected_frames_are_not_retained_as_atoms()`.

Implementation:

1. Validate frequency before opening.
2. Enumerate each selected file from zero.
3. Decide whether to retain each yielded frame immediately.
4. Retain only sampled frames in Dataset/source buffers.
5. Propagate consumed-frame count and parser error to Plan 08; do not classify
   partial versus failed here.

Focused command:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -m pytest tests/test_outcar.py tests/test_collect.py -q -p no:cacheprovider -k "sampling or frequency or outcar"
```

## Task 6: Record ASE-version Validation Honestly

Tests must assert public semantics:

- first frame fields and values exist;
- iterator remains lazy;
- file object, not path string, is used;
- handle closure is deterministic.

Run locally in ASE 3.28:

```powershell
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'
& $python -c "import ase; print(ase.__version__)"
& $python -m pytest tests/test_outcar.py -q -p no:cacheprovider
```

If no existing ASE 3.29 interpreter is available, record 3.29 execution as an
external validation item. Do not install it or claim it passed. The tests
themselves must avoid 3.28-specific formatting so they can be run unchanged later.

Full checkpoint:

```powershell
& $python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

## Acceptance Traceability

| Requirement | Evidence |
| --- | --- |
| strict YAML regex list | Task 1 |
| regex compile at load with index | Task 1 |
| deterministic family/natural order | Task 2 |
| mtime irrelevant | Task 2 |
| selected pattern/order metadata | Task 2 |
| context-managed file object | Task 4 |
| strict decode and deterministic close | Task 4 |
| first-frame laziness | Task 4 |
| per-file sampling origin | Task 5 |
| portable OUTCAR fixture | Task 3 |
| ASE 3.28 verified / 3.29 honest gate | Task 6 |

## Plan Checkpoint

Plan 07 is complete when config, discovery, streaming, sampling, fixture, affected
collect, and full suites pass. Tail truncation versus internal corruption and
per-source manifest status remain open for Plan 08.
