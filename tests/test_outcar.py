import os
import dataclasses
from pathlib import Path

import numpy as np
import pytest
from ase.io.vasp import read_vasp_out
import dpmoire_lite.outcar as outcar_module
from dpmoire_lite.outcar import find_outcar_series


OUTCAR_FIXTURE_ROOT = Path(__file__).parent / "data" / "outcar"
OUTCAR_FIXTURE = OUTCAR_FIXTURE_ROOT / "complete_two_frame.OUTCAR"
OUTCAR_INVENTORY = OUTCAR_FIXTURE_ROOT / "README.md"
DATA_INVENTORY = OUTCAR_FIXTURE_ROOT.parent / "README.md"


def test_outcar_fixture_inventory_has_provenance_entry():
    inventory = OUTCAR_INVENTORY.read_text(encoding="utf-8")
    data_inventory = DATA_INVENTORY.read_text(encoding="utf-8")

    assert "`complete_two_frame.OUTCAR`" in inventory
    assert "`outcar/complete_two_frame.OUTCAR`" in data_inventory
    for field in (
        "Source type:",
        "Reason for cropping:",
        "Retained blocks:",
        "Removed private data:",
        "Redistribution confirmation:",
        "Expected parser behavior:",
    ):
        assert field in inventory


def test_outcar_fixture_parses_expected_frames_and_properties():
    frames = read_vasp_out(str(OUTCAR_FIXTURE), index=":")

    assert len(frames) == 2
    for frame in frames:
        assert len(frame) == 1
        assert {"energy", "free_energy", "forces", "stress"} <= set(
            frame.calc.results
        )
        assert np.isfinite(frame.get_potential_energy())
        assert frame.get_forces().shape == (1, 3)
        assert np.isfinite(frame.get_forces()).all()
        assert frame.get_stress().shape == (6,)
        assert np.isfinite(frame.get_stress()).all()


def test_outcar_fixture_contains_no_private_path_or_potcar_marker():
    payload = OUTCAR_FIXTURE.read_text(encoding="utf-8")

    # Stock ASE 3.28 requires these synthetic OUTCAR species metadata lines.
    species_metadata = [
        line.strip() for line in payload.splitlines() if "POTCAR:" in line
    ]
    assert species_metadata == ["POTCAR: synthetic H", "POTCAR: synthetic H"]
    payload_without_species_metadata = "\n".join(
        line for line in payload.splitlines() if "POTCAR:" not in line
    ).casefold()

    forbidden_markers = (
        "potcar",
        "paw_pbe",
        "potpaw",
        "titel",
        "vrhfin",
        "lexch",
        "eatom",
        "atomic configuration",
        "end of dataset",
        "wavecar",
        "chgcar",
        "c:\\users\\",
        "e:\\",
        "/home/",
        "/scratch/",
        "sbatch",
        "slurm",
        "hostname",
        "account",
        "partition",
    )
    for marker in forbidden_markers:
        assert marker not in payload_without_species_metadata


def touch(path, mtime):
    path.write_text(path.name, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def selection_names(items):
    return [getattr(item, "path", item).name for item in items]


def open_outcar_frames_for_test(path):
    opener = getattr(outcar_module, "open_outcar_frames", None)
    assert callable(opener), "open_outcar_frames is not implemented"
    return opener(path)


class RecordingTextHandle:
    def __init__(self, handle):
        self._handle = handle
        self.name = handle.name
        self.logical_bytes = 0
        self.events = []

    def _record_text(self, text):
        self.logical_bytes += len(text.encode("utf-8"))

    def read(self, size=-1):
        self.events.append("read")
        text = self._handle.read(size)
        self._record_text(text)
        return text

    def readline(self, size=-1):
        self.events.append("readline")
        text = self._handle.readline(size)
        self._record_text(text)
        return text

    def tell(self):
        self.events.append("tell")
        return self.logical_bytes

    def close(self):
        self.events.append("close")
        return self._handle.close()

    @property
    def closed(self):
        return self._handle.closed

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if line == "":
            raise StopIteration
        return line

    def __getattr__(self, name):
        return getattr(self._handle, name)


def install_recording_text_handle(monkeypatch):
    real_open = Path.open
    handles = []

    def open_recording(path, *args, **kwargs):
        handle = RecordingTextHandle(real_open(path, *args, **kwargs))
        handles.append(handle)
        return handle

    monkeypatch.setattr(Path, "open", open_recording)
    return handles


def test_find_outcar_series_uses_pattern_priority_then_natural_sort(tmp_path):
    patterns = [r"^OUTCAR\d+$", r"^OUT\d+$"]
    touch(tmp_path / "OUTCAR10", 10)
    touch(tmp_path / "OUTCAR2", 20)
    touch(tmp_path / "OUT10", 30)
    touch(tmp_path / "OUT2", 40)
    (tmp_path / "OUTCAR3").mkdir()

    names = selection_names(find_outcar_series(tmp_path, patterns=patterns))

    assert names == ["OUTCAR2", "OUTCAR10", "OUT2", "OUT10"]


def test_outcar2_precedes_outcar10(tmp_path):
    touch(tmp_path / "OUTCAR10", 10)
    touch(tmp_path / "OUTCAR2", 20)

    assert selection_names(find_outcar_series(tmp_path)) == ["OUTCAR2", "OUTCAR10"]


def test_default_unnumbered_outcar_is_last(tmp_path):
    touch(tmp_path / "OUTCAR1", 40)
    touch(tmp_path / "OUT0", 30)
    touch(tmp_path / "out1", 20)
    touch(tmp_path / "OUTCAR", 10)

    assert selection_names(find_outcar_series(tmp_path)) == [
        "OUTCAR1",
        "OUT0",
        "out1",
        "OUTCAR",
    ]


def test_discovery_order_is_independent_of_mtime(tmp_path):
    patterns = [r"^OUTCAR\d+$", r"^OUT\d+$", r"^out\d+$", r"^OUTCAR$"]
    names = ["OUTCAR10", "OUTCAR2", "OUT10", "OUT2", "out11", "out3", "OUTCAR"]
    for index, name in enumerate(names):
        touch(tmp_path / name, index + 1)

    first = selection_names(find_outcar_series(tmp_path, patterns=patterns))
    for index, name in enumerate(reversed(names)):
        os.utime(tmp_path / name, (100 + index, 100 + index))
    second = selection_names(find_outcar_series(tmp_path, patterns=patterns))

    expected = ["OUTCAR2", "OUTCAR10", "OUT2", "OUT10", "out3", "out11", "OUTCAR"]
    assert first == expected
    assert second == expected


def test_file_matching_multiple_patterns_uses_first_once(tmp_path):
    patterns = [r"^OUT", r"^OUTCAR\d+$"]
    touch(tmp_path / "OUTCAR2", 10)

    selections = find_outcar_series(tmp_path, patterns=patterns)

    assert len(selections) == 1
    selection = selections[0]
    assert selection.pattern == patterns[0]
    assert selection.pattern_index == 0
    assert selection.order == 0


def test_selection_records_pattern_index_and_order(tmp_path):
    patterns = [r"^OUTCAR\d+$", r"^OUT\d+$", r"^OUTCAR$"]
    touch(tmp_path / "OUTCAR2", 10)
    touch(tmp_path / "OUT10", 20)
    touch(tmp_path / "OUTCAR", 30)

    selections = find_outcar_series(tmp_path, patterns=patterns)

    assert isinstance(selections, tuple)
    assert [
        (selection.path, selection.pattern, selection.pattern_index, selection.order)
        for selection in selections
    ] == [
        (tmp_path / "OUTCAR2", patterns[0], 0, 0),
        (tmp_path / "OUT10", patterns[1], 1, 1),
        (tmp_path / "OUTCAR", patterns[2], 2, 2),
    ]
    for selection in selections:
        assert dataclasses.is_dataclass(selection)
        assert selection.__dataclass_params__.frozen is True
        assert Path(selection) == selection.path
    with pytest.raises(dataclasses.FrozenInstanceError):
        selections[0].order = 99


def test_no_match_returns_empty_selection(tmp_path):
    assert find_outcar_series(tmp_path, patterns=[r"^OUTCAR$"]) == ()
    assert find_outcar_series(tmp_path / "missing", patterns=[r"^OUTCAR$"]) == ()


def test_find_outcar_series_matches_lowercase_out_prefix(tmp_path):
    touch(tmp_path / "out0", 10)
    touch(tmp_path / "outcar", 20)
    names = selection_names(find_outcar_series(tmp_path))
    assert names == ["out0"]


def test_find_outcar_series_accepts_config_regex(tmp_path):
    touch(tmp_path / "history.relax", 10)
    touch(tmp_path / "OUTCAR", 20)
    names = selection_names(
        find_outcar_series(tmp_path, patterns=[r"^history\.relax$"])
    )
    assert names == ["history.relax"]


def test_open_outcar_frames_passes_file_object_to_iread_vasp_out(
    monkeypatch,
):
    observed = {}

    def fake_iread_vasp_out(file_object, index):
        observed["file_object"] = file_object
        observed["index"] = index
        return iter(())

    monkeypatch.setattr(
        outcar_module,
        "iread_vasp_out",
        fake_iread_vasp_out,
        raising=False,
    )

    with open_outcar_frames_for_test(OUTCAR_FIXTURE) as frames:
        assert list(frames) == []

    file_object = observed["file_object"]
    assert not isinstance(file_object, (str, Path))
    assert hasattr(file_object, "name")
    assert hasattr(file_object, "read")
    assert hasattr(file_object, "readline")
    assert observed["index"] == ":"


def test_open_outcar_frames_reads_first_frame():
    with open_outcar_frames_for_test(OUTCAR_FIXTURE) as frames:
        frame = next(frames)

    assert len(frame) == 1
    assert np.isfinite(frame.get_potential_energy())
    assert np.isfinite(frame.calc.results["free_energy"])
    assert np.isfinite(frame.get_forces()).all()
    assert np.isfinite(frame.get_stress()).all()


def test_first_frame_is_yielded_before_file_position_reaches_eof(monkeypatch):
    handles = install_recording_text_handle(monkeypatch)

    with open_outcar_frames_for_test(OUTCAR_FIXTURE) as frames:
        frame = next(frames)
        assert len(frame) == 1
        assert handles[0].tell() < OUTCAR_FIXTURE.stat().st_size


def test_context_closes_handle_after_full_consumption(monkeypatch):
    handles = install_recording_text_handle(monkeypatch)

    with open_outcar_frames_for_test(OUTCAR_FIXTURE) as frames:
        assert len(list(frames)) == 2

    assert handles[0].closed


def test_context_closes_handle_after_break(monkeypatch):
    handles = install_recording_text_handle(monkeypatch)

    with open_outcar_frames_for_test(OUTCAR_FIXTURE) as frames:
        for _frame in frames:
            break

    assert handles[0].closed


def test_context_closes_handle_after_iterator_error(monkeypatch):
    handles = install_recording_text_handle(monkeypatch)
    sentinel = RuntimeError("sentinel iterator failure")

    def failing_iread_vasp_out(_file_object, index):
        assert index == ":"

        def frames():
            yield object()
            raise sentinel

        return frames()

    monkeypatch.setattr(
        outcar_module,
        "iread_vasp_out",
        failing_iread_vasp_out,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="sentinel iterator failure"):
        with open_outcar_frames_for_test(OUTCAR_FIXTURE) as frames:
            next(frames)
            next(frames)

    assert handles[0].closed


def test_invalid_utf8_is_not_ignored(tmp_path):
    invalid_path = tmp_path / "invalid.OUTCAR"
    invalid_path.write_bytes(b"\xff")

    with pytest.raises(UnicodeDecodeError):
        with open_outcar_frames_for_test(invalid_path) as frames:
            next(frames)


def test_streamed_frames_match_small_batch_reference_semantics():
    batch_frames = read_vasp_out(str(OUTCAR_FIXTURE), index=":")
    with open_outcar_frames_for_test(OUTCAR_FIXTURE) as frames:
        streamed_frames = list(frames)

    assert len(streamed_frames) == len(batch_frames) == 2
    for streamed, batch in zip(streamed_frames, batch_frames, strict=True):
        assert streamed.get_chemical_symbols() == batch.get_chemical_symbols()
        np.testing.assert_allclose(streamed.cell.array, batch.cell.array)
        np.testing.assert_allclose(streamed.positions, batch.positions)
        assert streamed.get_potential_energy() == pytest.approx(
            batch.get_potential_energy()
        )
        assert streamed.calc.results["free_energy"] == pytest.approx(
            batch.calc.results["free_energy"]
        )
        np.testing.assert_allclose(streamed.get_forces(), batch.get_forces())
        np.testing.assert_allclose(streamed.get_stress(), batch.get_stress())
