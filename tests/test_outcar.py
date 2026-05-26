import os

from dpmoire_lite.outcar import find_outcar_series


def touch(path, mtime):
    path.write_text(path.name, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_find_outcar_series_uses_default_patterns_and_mtime(tmp_path):
    touch(tmp_path / "OUTCAR1", 30)
    touch(tmp_path / "OUTCAR", 40)
    touch(tmp_path / "OUT0", 20)
    touch(tmp_path / "OUTCAR-relax", 5)
    touch(tmp_path / "OUTCAR.bad", 6)
    names = [path.name for path in find_outcar_series(tmp_path)]
    assert names == ["OUT0", "OUTCAR1", "OUTCAR"]


def test_find_outcar_series_matches_lowercase_out_prefix(tmp_path):
    touch(tmp_path / "out0", 10)
    touch(tmp_path / "outcar", 20)
    names = [path.name for path in find_outcar_series(tmp_path)]
    assert names == ["out0"]


def test_find_outcar_series_accepts_config_regex(tmp_path):
    touch(tmp_path / "history.relax", 10)
    touch(tmp_path / "OUTCAR", 20)
    names = [path.name for path in find_outcar_series(tmp_path, patterns=[r"^history\.relax$"])]
    assert names == ["history.relax"]
