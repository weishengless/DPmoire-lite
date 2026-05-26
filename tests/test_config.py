from pathlib import Path

import pytest
import yaml

from dpmoire_lite.config import ConfigError, load_config, normalize_pair


def write_config(path: Path, **overrides):
    data = {
        "dft_script": "DFT_script.sh",
        "potcar_dir": str(path.parent / "potcars"),
        "script_dir": str(path.parent / "scripts"),
        "input_dir": str(path.parent / "input"),
        "work_dir": str(path.parent / "work"),
        "n_nodes": 2,
        "stage": 0,
        "submit": False,
        "auto_resub": False,
        "vasp_ml": True,
        "outcar_collect_freq": 8,
        "do_relaxation": True,
        "init_mlff": True,
        "sc_rlx": True,
        "n_sectors": [9, 8],
        "sc": [2, 3],
        "d": 6.3,
        "k_mesh": 40,
        "encut_factor": 1.6,
        "r_cut": -1,
        "symm_reduce": True,
        "twist_val": True,
        "min_val_n": 4,
        "max_val_n": 5,
        "include_monolayer_md": True,
    }
    data.update(overrides)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_normalize_pair_accepts_int_and_pair():
    assert normalize_pair(9, field="n_sectors") == (9, 9)
    assert normalize_pair([9, 8], field="n_sectors") == (9, 8)


def test_normalize_pair_rejects_bad_values():
    with pytest.raises(ConfigError, match="n_sectors"):
        normalize_pair([9, 8, 7], field="n_sectors")
    with pytest.raises(ConfigError, match="positive"):
        normalize_pair([9, 0], field="n_sectors")


def test_load_config_resolves_paths(tmp_path):
    (tmp_path / "potcars").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "input").mkdir()
    config_file = tmp_path / "config.yaml"
    write_config(config_file)
    config = load_config(config_file)
    assert config.n_sectors == (9, 8)
    assert config.sc == (2, 3)
    assert config.input_dir == tmp_path / "input"
    assert config.work_dir == tmp_path / "work"


def test_stage_all_requires_submit_wait_at_build_time(tmp_path):
    (tmp_path / "potcars").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "input").mkdir()
    config_file = tmp_path / "config.yaml"
    write_config(config_file, stage="all", submit=False)
    config = load_config(config_file)
    with pytest.raises(ConfigError, match="stage: all"):
        config.validate_build_mode(wait=False)


def test_old_field_names_are_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("VASP_ML: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="snake_case"):
        load_config(config_file)
