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
