import warnings

import pytest

import dpmoire_lite.build as build_module
from dpmoire_lite.build import run_build

from test_build import write_build_config, write_converged_relaxation


def _mark_stage(work, stage: str, text: str = "keep me"):
    stage_dir = work / stage
    stage_dir.mkdir(parents=True, exist_ok=True)
    marker = stage_dir / "marker.txt"
    marker.write_text(text, encoding="utf-8")
    return marker


def _assert_explicit_delete_message(error: Exception, *stages: str) -> None:
    message = str(error).lower()
    for stage in stages:
        assert stage in message
    assert "delete" in message or "remove" in message
    assert "rerun" in message or "run again" in message


def test_stage0_existing_empty_init_mlff_blocks_every_target(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        n_sectors=[1, 1],
        twist_val=True,
        min_val_n=2,
        max_val_n=2,
    )
    work = tmp_path / "work"
    (work / "init_mlff").mkdir(parents=True)

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "init_mlff")
    assert (work / "init_mlff").is_dir()
    assert not (work / "rlx").exists()
    assert not (work / "validation").exists()
    assert not (work / "backups").exists()


def test_stage0_existing_rlx_blocks_init_and_validation_generation(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        n_sectors=[1, 1],
        twist_val=True,
        min_val_n=2,
        max_val_n=2,
    )
    work = tmp_path / "work"
    marker = _mark_stage(work, "rlx")

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "rlx")
    assert marker.read_text(encoding="utf-8") == "keep me"
    assert not (work / "init_mlff").exists()
    assert not (work / "validation").exists()
    assert not (work / "backups").exists()


def test_stage0_existing_validation_blocks_init_and_rlx_generation(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        n_sectors=[1, 1],
        twist_val=True,
        min_val_n=2,
        max_val_n=2,
    )
    work = tmp_path / "work"
    marker = _mark_stage(work, "validation")

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "validation")
    assert marker.read_text(encoding="utf-8") == "keep me"
    assert not (work / "init_mlff").exists()
    assert not (work / "rlx").exists()
    assert not (work / "backups").exists()


def test_stage0_reports_all_existing_target_stages(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        n_sectors=[1, 1],
        twist_val=True,
        min_val_n=2,
        max_val_n=2,
    )
    work = tmp_path / "work"
    markers = {stage: _mark_stage(work, stage, f"{stage} sentinel") for stage in ("init_mlff", "rlx", "validation")}

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "init_mlff", "rlx", "validation")
    for stage, marker in markers.items():
        assert marker.read_text(encoding="utf-8") == f"{stage} sentinel"
    assert not (work / "backups").exists()


def test_stage1_existing_empty_md_fails_without_backup(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=1,
        n_sectors=[1, 1],
        vasp_ml=False,
        include_monolayer_md=False,
    )
    work = tmp_path / "work"
    write_converged_relaxation(work)
    md_dir = work / "md"
    md_dir.mkdir(parents=True)

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "md")
    assert list(md_dir.iterdir()) == []
    assert not (work / "backups").exists()


def test_target_conflict_runs_before_input_preflight(tmp_path):
    config = write_build_config(tmp_path, stage=0, n_sectors=[1, 1], twist_val=False)
    work = tmp_path / "work"
    _mark_stage(work, "rlx")
    (tmp_path / "input" / "top_layer.poscar").unlink()

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value).lower()
    _assert_explicit_delete_message(exc_info.value, "rlx")
    assert "top_layer.poscar" not in message
    assert not (work / "init_mlff").exists()


def test_target_conflict_does_not_write_root_artifacts(monkeypatch, tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        n_sectors=[1, 1],
        symm_reduce=True,
        twist_val=False,
    )
    work = tmp_path / "work"
    _mark_stage(work, "rlx")
    artifact = work / "sym_reduced_stackings.txt"

    def write_root_artifact(self):
        artifact.write_text("must not be written", encoding="utf-8")
        return [(0, 0)]

    monkeypatch.setattr(build_module.StructureHandler, "find_sym_reduced_stackings", write_root_artifact)

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "rlx")
    assert not artifact.exists()


def test_target_conflict_does_not_construct_runner(monkeypatch, tmp_path):
    config = write_build_config(tmp_path, stage=0, n_sectors=[1, 1], submit=True, twist_val=False)
    work = tmp_path / "work"
    _mark_stage(work, "rlx")
    runner_constructed = False

    def fail_if_constructed(*_args, **_kwargs):
        nonlocal runner_constructed
        runner_constructed = True
        raise AssertionError("SlurmRunner was constructed before target conflict validation")

    monkeypatch.setattr(build_module, "SlurmRunner", fail_if_constructed)

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    _assert_explicit_delete_message(exc_info.value, "rlx")
    assert runner_constructed is False


def test_stage0_preflight_aggregates_incar_potcar_and_script_errors(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        do_relaxation=False,
        twist_val=False,
    )
    input_dir = tmp_path / "input"
    (input_dir / "init_INCAR").write_text(
        'ENCUT=400\nSYSTEM = "unfinished\n',
        encoding="utf-8",
    )
    (tmp_path / "potcars" / "H" / "POTCAR").unlink()
    (tmp_path / "scripts" / "DFT_script.sh").unlink()

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value)
    assert "init_INCAR" in message
    assert "POTCAR" in message
    assert "DFT_script.sh" in message
    assert not (tmp_path / "work").exists()


def test_stage0_preflight_aggregates_input_structure_and_vdw_errors(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        do_relaxation=False,
        twist_val=False,
    )
    input_dir = tmp_path / "input"
    (input_dir / "init_INCAR").write_text(
        "ENCUT=400\nLUSE_VDW=T\nML_RCUT1=6\nML_RCUT2=6\n",
        encoding="utf-8",
    )
    (input_dir / "top_layer.poscar").write_text("not a POSCAR\n", encoding="utf-8")

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value)
    assert "top_layer.poscar" in message
    assert "vdw_kernel.bindat" in message
    assert not (tmp_path / "work").exists()


def test_stage1_preflight_aggregates_all_relaxation_failures(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=1,
        n_sectors=[2, 1],
        vasp_ml=False,
        include_monolayer_md=False,
    )
    work = tmp_path / "work"
    write_converged_relaxation(work, name="0_0")
    (work / "rlx" / "0_0" / "OUTCAR").unlink()
    second = work / "rlx" / "1_0"
    second.mkdir(parents=True)
    (second / "CONTCAR").write_text("not a POSCAR\n", encoding="utf-8")
    (second / "OUTCAR").write_text(
        "reached required accuracy - stopping structural energy minimisation\n",
        encoding="utf-8",
    )
    (tmp_path / "input" / "MD_INCAR").unlink()

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value)
    assert "rlx/0_0" in message
    assert "Missing OUTCAR" in message
    assert "rlx/1_0" in message
    assert "CONTCAR" in message
    assert "MD_INCAR" in message
    assert not (work / "md").exists()


def test_stage1_preflight_fully_parses_initial_seed(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=1,
        n_sectors=[1, 1],
        vasp_ml=True,
        include_monolayer_md=False,
    )
    work = tmp_path / "work"
    write_converged_relaxation(work)
    init_mlff = work / "init_mlff"
    init_mlff.mkdir(parents=True)
    (init_mlff / "ML_ABN").write_text(
        "1.0 Version\n"
        "**************************************************\n"
        "The number of configurations\n"
        "--------------------------------------------------\n"
        "1\n",
        encoding="utf-8",
    )
    (init_mlff / "ML_FFN").write_text("ffn", encoding="utf-8")

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value).lower()
    assert "ml_abn" in message
    assert "configuration marker" in message
    assert not (work / "md").exists()


def test_preflight_failure_does_not_create_workdir_when_absent(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        do_relaxation=False,
        twist_val=False,
    )
    (tmp_path / "potcars" / "H" / "POTCAR").unlink()
    (tmp_path / "scripts" / "DFT_script.sh").unlink()

    with pytest.raises(Exception) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value)
    assert "POTCAR" in message
    assert "DFT_script.sh" in message
    assert not (tmp_path / "work").exists()


def test_preflight_failure_does_not_write_normalized_input_sibling(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        do_relaxation=False,
        twist_val=False,
    )
    input_dir = tmp_path / "input"
    top_layer = input_dir / "top_layer.poscar"
    top_layer.write_text(
        top_layer.read_text(encoding="utf-8").replace("\nH\n1\n", "\nI1\n1\n"),
        encoding="utf-8",
    )
    (tmp_path / "scripts" / "DFT_script.sh").unlink()

    with pytest.raises(Exception):
        run_build(config, wait=False)

    assert not any(path.name.endswith(".normalized") for path in input_dir.iterdir())
    assert not (tmp_path / "work").exists()


def test_preflight_warnings_are_deduplicated_per_template_and_tag(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        init_mlff=False,
        do_relaxation=True,
        twist_val=False,
        n_sectors=[2, 1],
    )
    (tmp_path / "input" / "rlx_INCAR").write_text(
        "ENCUT=400\nENCUT=500\nML_RCUT1=6\nML_RCUT2=6\n",
        encoding="utf-8",
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run_build(config, wait=False)

    matching = [
        item
        for item in caught
        if "ENCUT" in str(item.message) and "rlx_INCAR" in str(item.message)
    ]
    assert len(matching) == 1
    assert (tmp_path / "work" / "rlx" / "0_0").is_dir()
    assert (tmp_path / "work" / "rlx" / "1_0").is_dir()
