import shutil
from pathlib import Path

import pytest
import yaml

import dpmoire_lite.collect as collect_module
from dpmoire_lite.dataset import Dataset
from dpmoire_lite.collect import run_collect
from dpmoire_lite.manifest import Manifest, read_manifest, write_manifest


def write_collect_config(root, **overrides):
    input_dir = root / "input"
    script_dir = root / "scripts"
    potcar_dir = root / "potcars"
    input_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)
    potcar_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "dft_script": "DFT_script.sh",
        "potcar_dir": str(potcar_dir),
        "script_dir": str(script_dir),
        "input_dir": str(input_dir),
        "work_dir": str(root / "work"),
        "n_nodes": 1,
        "stage": 0,
        "submit": False,
        "auto_resub": False,
        "vasp_ml": True,
        "outcar_collect_freq": 8,
        "do_relaxation": True,
        "init_mlff": True,
        "sc_rlx": True,
        "n_sectors": [1, 1],
        "sc": [1, 1],
        "d": 4.0,
        "k_mesh": 20,
        "encut_factor": 1.5,
        "r_cut": -1,
        "symm_reduce": False,
        "twist_val": False,
        "min_val_n": 4,
        "max_val_n": 5,
        "include_monolayer_md": False,
    }
    data.update(overrides)
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_load_ml_abn_sample_counts_frames(tmp_path, sample_dir):
    source = sample_dir / "ML_ABN"
    target = tmp_path / "ML_ABN"
    shutil.copy2(source, target)
    dataset = Dataset()
    dataset.load_ml_ab(target)
    assert dataset.n_configs > 0
    assert len(dataset.data) == dataset.n_configs


def test_load_outcar_sample_counts_frames(tmp_path, sample_dir):
    source = sample_dir / "lambda_p0p00" / "OUTCAR"
    target = tmp_path / "OUTCAR"
    shutil.copy2(source, target)
    dataset = Dataset()
    dataset.load_outcar(target, freq=1)
    assert dataset.n_configs > 0


def test_load_outcar_rejects_non_positive_frequency():
    with pytest.raises(ValueError, match="positive"):
        Dataset().load_outcar(Path("whatever"), freq=0)


def test_collect_ml_md_writes_extxyz_and_manifest_counts(tmp_path, sample_dir):
    config_path = write_collect_config(tmp_path, stage=0, vasp_ml=True)
    work = tmp_path / "work"
    md_dir = work / "md" / "0_0"
    md_dir.mkdir(parents=True)
    shutil.copy2(sample_dir / "ML_ABN", md_dir / "ML_ABN")
    write_manifest(
        work,
        Manifest(stage="md", generated_at="test", directories=["md/0_0"]),
    )

    run_collect(config_path, stage="md")

    assert (work / "MD_data.extxyz").is_file()
    manifest = read_manifest(work, "md")
    assert manifest.collect["frames"] > 0
    assert manifest.collect["output"] == "MD_data.extxyz"


def test_collect_ml_md_records_missing_ml_abn_without_crashing(tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=True)
    work = tmp_path / "work"
    (work / "md" / "0_0").mkdir(parents=True)
    write_manifest(
        work,
        Manifest(stage="md", generated_at="test", directories=["md/0_0"]),
    )

    run_collect(config_path, stage="md")

    manifest = read_manifest(work, "md")
    records = manifest.skipped + manifest.failed
    assert records
    assert any("md/0_0/ML_ABN" in record["path"] for record in records)
    assert any("ML_ABN" in record["reason"] for record in records)


def test_collect_ml_md_skips_existing_ml_ab_prefix(tmp_path, sample_dir):
    config_path = write_collect_config(tmp_path, vasp_ml=True)
    work = tmp_path / "work"
    md_dir = work / "md" / "0_0"
    md_dir.mkdir(parents=True)
    shutil.copy2(sample_dir / "ML_ABN", md_dir / "ML_ABN")
    shutil.copy2(sample_dir / "ML_ABN", md_dir / "ML_AB")
    write_manifest(
        work,
        Manifest(stage="md", generated_at="test", directories=["md/0_0"]),
    )

    run_collect(config_path, stage="md")

    manifest = read_manifest(work, "md")
    assert manifest.collect["frames"] == 0
    assert manifest.collect["sources"] == 1


def test_collect_validation_uses_all_ionic_steps(monkeypatch, tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=False, outcar_collect_freq=11)
    work = tmp_path / "work"
    validation_dir = work / "validation" / "1.00deg"
    validation_dir.mkdir(parents=True)
    write_manifest(
        work,
        Manifest(stage="validation", generated_at="test", directories=["validation/1.00deg"]),
    )
    seen_freqs = []

    class FakeDataset:
        def __init__(self):
            self.n_configs = 0

        def load_outcar(self, path, freq):
            seen_freqs.append(freq)
            self.n_configs += 1

        def save_extxyz(self, path):
            Path(path).write_text("fake extxyz\n", encoding="utf-8")

    monkeypatch.setattr(collect_module, "Dataset", FakeDataset, raising=False)
    monkeypatch.setattr(
        collect_module,
        "find_outcar_series",
        lambda directory, patterns: [Path(directory) / "OUTCAR"],
        raising=False,
    )

    run_collect(config_path, stage="validation")

    assert seen_freqs == [1]
    manifest = read_manifest(work, "validation")
    assert manifest.collect["frames"] == 1
