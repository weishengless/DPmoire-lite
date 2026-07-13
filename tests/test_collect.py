from contextlib import contextmanager
import shutil
from pathlib import Path

import pytest
import yaml

import dpmoire_lite.collect as collect_module
import dpmoire_lite.dataset as dataset_module
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


def install_dataset_stream_spies(monkeypatch, streams, opened):
    @contextmanager
    def fake_open_outcar_frames(path):
        source_path = Path(path)
        opened.append(source_path)
        yield iter(streams[source_path])

    def reject_batch_reader(*_args, **_kwargs):
        raise AssertionError("batch OUTCAR reader used instead of streaming context")

    monkeypatch.setattr(
        dataset_module,
        "open_outcar_frames",
        fake_open_outcar_frames,
        raising=False,
    )
    monkeypatch.setattr(
        dataset_module,
        "read_outcar_frames",
        reject_batch_reader,
        raising=False,
    )


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
    assert manifest.schema_version == 2
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


def test_collect_ml_md_skips_source_when_ml_ab_count_fails(tmp_path, sample_dir):
    config_path = write_collect_config(tmp_path, vasp_ml=True)
    work = tmp_path / "work"
    md_dir = work / "md" / "0_0"
    md_dir.mkdir(parents=True)
    shutil.copy2(sample_dir / "ML_ABN", md_dir / "ML_ABN")
    (md_dir / "ML_AB").write_text("not\nan\nML_AB\nfile\nbad-count\n", encoding="utf-8")
    write_manifest(
        work,
        Manifest(stage="md", generated_at="test", directories=["md/0_0"]),
    )

    run_collect(config_path, stage="md")

    manifest = read_manifest(work, "md")
    assert not (work / "MD_data.extxyz").exists()
    assert manifest.collect["frames"] == 0
    assert manifest.collect["sources"] == 0
    assert manifest.collect["written"] is False
    assert any(
        record["path"] == "md/0_0/ML_AB"
        and "Could not read ML_AB count" in record["reason"]
        for record in manifest.failed
    )


def test_collect_removes_stale_output_when_no_frames_are_collected(tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=True)
    work = tmp_path / "work"
    work.mkdir(parents=True)
    stale_output = work / "MD_data.extxyz"
    stale_output.write_text("old dataset\n", encoding="utf-8")
    write_manifest(
        work,
        Manifest(stage="md", generated_at="test", directories=[]),
    )

    run_collect(config_path, stage="md")

    manifest = read_manifest(work, "md")
    assert not stale_output.exists()
    assert manifest.collect["frames"] == 0
    assert manifest.collect["output"] == "MD_data.extxyz"
    assert manifest.collect["written"] is False
    assert manifest.collect["removed_stale_output"] is True


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


def test_collect_missing_stage_manifest_fails_without_output_or_manifest(tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=False)
    work = tmp_path / "work"

    with pytest.raises(Exception) as exc_info:
        run_collect(config_path, stage="rlx")

    message = str(exc_info.value).lower()
    assert "manifest" in message
    assert "rlx" in message
    assert not (work / "rlx_data.extxyz").exists()
    assert not (work / "rlx" / "manifest.yaml").exists()


def test_collect_does_not_derive_directories_from_config(tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=False, n_sectors=[2, 1])
    work = tmp_path / "work"
    (work / "rlx" / "0_0").mkdir(parents=True)

    with pytest.raises(Exception) as exc_info:
        run_collect(config_path, stage="rlx")

    message = str(exc_info.value).lower()
    assert "manifest" in message
    assert "rlx" in message
    assert not (work / "rlx_data.extxyz").exists()
    assert not (work / "rlx" / "manifest.yaml").exists()


def test_collect_does_not_scan_validation_directories_without_manifest(tmp_path):
    config_path = write_collect_config(tmp_path, vasp_ml=False)
    work = tmp_path / "work"
    (work / "validation" / "1.00deg").mkdir(parents=True)

    with pytest.raises(Exception) as exc_info:
        run_collect(config_path, stage="validation")

    message = str(exc_info.value).lower()
    assert "manifest" in message
    assert "validation" in message
    assert not (work / "valid.extxyz").exists()
    assert not (work / "validation" / "manifest.yaml").exists()


def test_stream_sampling_freq_one_accepts_every_frame(tmp_path, monkeypatch):
    path = tmp_path / "segment.OUTCAR"
    frames = ["frame-0", "frame-1", "frame-2", "frame-3"]
    opened = []
    install_dataset_stream_spies(monkeypatch, {path: frames}, opened)
    accepted = []
    dataset = Dataset()
    dataset.add_atoms = accepted.append

    dataset.load_outcar(path, freq=1)

    assert accepted == frames
    assert opened == [path]


def test_stream_sampling_accepts_zero_n_2n_per_file(tmp_path, monkeypatch):
    path = tmp_path / "segment.OUTCAR"
    frames = [f"frame-{index}" for index in range(6)]
    opened = []
    install_dataset_stream_spies(monkeypatch, {path: frames}, opened)
    accepted = []
    dataset = Dataset()
    dataset.add_atoms = accepted.append

    dataset.load_outcar(path, freq=2)

    assert accepted == ["frame-0", "frame-2", "frame-4"]
    assert opened == [path]


def test_sampling_index_resets_for_each_outcar_segment(tmp_path, monkeypatch):
    first_path = tmp_path / "first.OUTCAR"
    second_path = tmp_path / "second.OUTCAR"
    streams = {
        first_path: ["first-0", "first-1", "first-2"],
        second_path: ["second-0", "second-1", "second-2"],
    }
    opened = []
    install_dataset_stream_spies(monkeypatch, streams, opened)
    accepted = []
    dataset = Dataset()
    dataset.add_atoms = accepted.append

    dataset.load_outcar(first_path, freq=2)
    dataset.load_outcar(second_path, freq=2)

    assert accepted == ["first-0", "first-2", "second-0", "second-2"]
    assert opened == [first_path, second_path]


def test_nonpositive_frequency_fails_before_opening_file(monkeypatch):
    def fail_open(*_args, **_kwargs):
        raise AssertionError("streaming context opened before frequency validation")

    def fail_batch(*_args, **_kwargs):
        raise AssertionError("batch reader opened before frequency validation")

    monkeypatch.setattr(
        dataset_module,
        "open_outcar_frames",
        fail_open,
        raising=False,
    )
    monkeypatch.setattr(
        dataset_module,
        "read_outcar_frames",
        fail_batch,
        raising=False,
    )

    for frequency in (0, -1):
        with pytest.raises(ValueError, match="freq must be a positive integer"):
            Dataset().load_outcar(Path("not-opened.OUTCAR"), freq=frequency)


def test_unselected_frames_are_not_retained_as_atoms(tmp_path, monkeypatch):
    path = tmp_path / "streaming.OUTCAR"
    frames = [f"frame-{index}" for index in range(6)]
    opened = []
    accepted = []

    @contextmanager
    def fake_open_outcar_frames(requested_path):
        source_path = Path(requested_path)
        opened.append(source_path)

        def stream():
            for index, frame in enumerate(frames):
                assert accepted == [
                    frames[previous]
                    for previous in range(index)
                    if previous % 2 == 0
                ]
                yield frame

        yield stream()

    def reject_batch_reader(*_args, **_kwargs):
        raise AssertionError("batch OUTCAR reader used instead of streaming context")

    monkeypatch.setattr(
        dataset_module,
        "open_outcar_frames",
        fake_open_outcar_frames,
        raising=False,
    )
    monkeypatch.setattr(
        dataset_module,
        "read_outcar_frames",
        reject_batch_reader,
        raising=False,
    )
    dataset = Dataset()
    dataset.add_atoms = accepted.append

    dataset.load_outcar(path, freq=2)

    assert accepted == ["frame-0", "frame-2", "frame-4"]
    assert opened == [path]
