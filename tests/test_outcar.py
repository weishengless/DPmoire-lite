import os
import dataclasses
from pathlib import Path

import pytest
from dpmoire_lite.outcar import find_outcar_series


def touch(path, mtime):
    path.write_text(path.name, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def selection_names(items):
    return [getattr(item, "path", item).name for item in items]


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
