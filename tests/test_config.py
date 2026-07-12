from pathlib import Path

import pytest
import yaml

from dpmoire_lite.config import ConfigError, load_config, normalize_pair


def assert_temporary_safety_error(error: ConfigError) -> None:
    message = str(error)
    assert "temporarily disabled" in message
    assert "Slurm terminal-state validation and failure propagation" in message
    assert "submit: false" in message
    assert "manually" in message


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


def test_stage_all_is_temporarily_disabled_for_submit_false(tmp_path):
    (tmp_path / "potcars").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "input").mkdir()
    config_file = tmp_path / "config.yaml"
    write_config(config_file, stage="all", submit=False)
    config = load_config(config_file)
    with pytest.raises(ConfigError) as exc_info:
        config.validate_build_mode(wait=False)
    assert_temporary_safety_error(exc_info.value)


def test_stage_all_is_temporarily_disabled_for_submit_true_wait(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(config_file, stage="all", submit=True)
    config = load_config(config_file)

    with pytest.raises(ConfigError) as exc_info:
        config.validate_build_mode(wait=True)

    assert_temporary_safety_error(exc_info.value)


def test_submitted_wait_is_disabled_for_stage0(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(config_file, stage=0, submit=True)
    config = load_config(config_file)

    with pytest.raises(ConfigError) as exc_info:
        config.validate_build_mode(wait=True)

    assert_temporary_safety_error(exc_info.value)


def test_submitted_wait_is_disabled_for_stage1(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(config_file, stage=1, submit=True)
    config = load_config(config_file)

    with pytest.raises(ConfigError) as exc_info:
        config.validate_build_mode(wait=True)

    assert_temporary_safety_error(exc_info.value)


def test_manual_stage0_and_stage1_remain_allowed(tmp_path):
    for stage in (0, 1):
        config_file = tmp_path / f"config-stage{stage}.yaml"
        write_config(config_file, stage=stage, submit=False)
        config = load_config(config_file)

        config.validate_build_mode(wait=False)


def test_old_field_names_are_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("VASP_ML: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="snake_case"):
        load_config(config_file)


def test_quoted_false_boolean_values_parse_to_false(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(
        config_file,
        submit="false",
        auto_resub="NO",
        vasp_ml="0",
        do_relaxation="False",
        init_mlff="no",
        sc_rlx="false",
        symm_reduce="0",
        twist_val="NO",
        include_monolayer_md="False",
    )
    config = load_config(config_file)
    assert config.submit is False
    assert config.auto_resub is False
    assert config.vasp_ml is False
    assert config.do_relaxation is False
    assert config.init_mlff is False
    assert config.sc_rlx is False
    assert config.symm_reduce is False
    assert config.twist_val is False
    assert config.include_monolayer_md is False


def test_invalid_boolean_string_raises_field_context(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(config_file, submit="sometimes")
    with pytest.raises(ConfigError, match="submit"):
        load_config(config_file)


def test_invalid_numeric_values_raise_field_context(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(config_file, k_mesh=True)
    with pytest.raises(ConfigError, match="k_mesh"):
        load_config(config_file)

    write_config(config_file, d="not-a-number")
    with pytest.raises(ConfigError, match="d"):
        load_config(config_file)


def test_d_mode_defaults_to_surface_gap(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(config_file)

    config = load_config(config_file)

    assert config.d_mode == "surface_gap"
    assert config.d_reference is None
    assert config.potcar_policy == "recommend"


@pytest.mark.parametrize("policy", ["recommend", "minimal"])
def test_load_config_accepts_valid_potcar_policies(tmp_path, policy):
    config_file = tmp_path / "config.yaml"
    write_config(config_file, potcar_policy=policy)

    config = load_config(config_file)

    assert config.potcar_policy == policy


def test_load_config_rejects_invalid_potcar_policy(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(config_file, potcar_policy="vasp_recommended")

    with pytest.raises(ConfigError, match="potcar_policy"):
        load_config(config_file)


@pytest.mark.parametrize("mode", ["surface_gap", "reference_plane_gap"])
def test_load_config_accepts_valid_d_modes(tmp_path, mode):
    config_file = tmp_path / "config.yaml"
    write_config(config_file, d_mode=mode)

    config = load_config(config_file)

    assert config.d_mode == mode


def test_load_config_accepts_reference_elements(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(
        config_file,
        d_mode="reference_plane_gap",
        d_reference={"top": ["Pt"], "bot": ["Pt", "Mo"]},
    )

    config = load_config(config_file)

    assert config.d_reference == {"top": ("Pt",), "bot": ("Pt", "Mo")}


def test_load_config_accepts_all_reference_keyword(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(
        config_file,
        d_mode="reference_plane_gap",
        d_reference={"top": "all", "bot": "all"},
    )

    config = load_config(config_file)

    assert config.d_reference == {"top": "all", "bot": "all"}


def test_load_config_ignores_reference_when_surface_gap(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(
        config_file,
        d_mode="surface_gap",
        d_reference={"top": ["Xx"], "bot": ["Pt"]},
    )

    config = load_config(config_file)

    assert config.d_reference is None


def test_load_config_rejects_invalid_reference_element(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(
        config_file,
        d_mode="reference_plane_gap",
        d_reference={"top": ["Xx"], "bot": ["Mo"]},
    )

    with pytest.raises(ConfigError, match="d_reference.top"):
        load_config(config_file)


def test_load_config_rejects_invalid_d_mode(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(config_file, d_mode="center_distance")

    with pytest.raises(ConfigError, match="d_mode"):
        load_config(config_file)


def test_preserve_grid_shift_md_defaults_false_when_omitted(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(config_file)

    config = load_config(config_file)

    assert config.preserve_grid_shift_md is False


@pytest.mark.parametrize("value", [True, False])
def test_preserve_grid_shift_md_accepts_explicit_boolean(tmp_path, value):
    config_file = tmp_path / "config.yaml"
    write_config(config_file, preserve_grid_shift_md=value)

    config = load_config(config_file)

    assert config.preserve_grid_shift_md is value


def test_preserve_grid_shift_md_rejects_invalid_boolean(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(config_file, preserve_grid_shift_md="sometimes")

    with pytest.raises(ConfigError, match="preserve_grid_shift_md"):
        load_config(config_file)


def test_source_and_bundled_examples_show_false_default():
    repo_root = Path(__file__).resolve().parents[1]

    for relative_path in ("example/config.yaml", "src/dpmoire_lite/example/config.yaml"):
        data = yaml.safe_load((repo_root / relative_path).read_text(encoding="utf-8"))
        assert data["preserve_grid_shift_md"] is False
