import hashlib
import shutil
from pathlib import Path

import pytest
import yaml
from ase import Atoms
from ase.io.vasp import write_vasp

import dpmoire_lite.init_mlff as init_mlff_module
from dpmoire_lite.build import run_build
from dpmoire_lite.init_mlff import (
    InitMlffWorkflow,
    InitMlffWorkflowError,
    validate_published_init_mlff_seed,
)
from dpmoire_lite.manifest import read_manifest, write_manifest

from test_build import write_build_config


MLAB_FIXTURES = Path(__file__).parent / "data" / "mlab"


class SyntheticCalculationAdapter:
    def __init__(self, work_dir, handlers):
        self.work_dir = Path(work_dir)
        self.handlers = handlers
        self.calls = []

    def run(self, request):
        result = read_manifest(self.work_dir, "init_mlff")
        assert result.manifest is not None
        workflow = result.manifest.init_workflow
        self.calls.append(
            (
                request.phase,
                request.role,
                request.directory,
                workflow["state"],
                workflow["phases"][request.name]["state"],
            )
        )
        return self.handlers[request.phase](request.directory)


def _write_phase_templates(input_dir):
    for name in ("init_bottom_INCAR", "init_top_INCAR"):
        (input_dir / name).write_text(
            "ENCUT=400\nML_RCUT1=6\nML_RCUT2=6\n",
            encoding="utf-8",
        )


def _write_op_layer(path):
    write_vasp(
        path,
        Atoms(
            ["O", "Pt"],
            positions=[[0.0, 0.0, 3.0], [1.0, 1.0, 3.0]],
            cell=[4.0, 4.0, 12.0],
            pbc=True,
        ),
        direct=True,
    )


def _prepare_workflow(tmp_path, *, do_relaxation=False):
    config_path = write_build_config(
        tmp_path,
        stage=0,
        init_mlff_mode="single-job",
        init_bottom_incar="init_bottom_INCAR",
        init_top_incar="init_top_INCAR",
        do_relaxation=do_relaxation,
        twist_val=False,
        n_sectors=[1, 1],
        include_monolayer_md=False,
    )
    input_dir = tmp_path / "input"
    _write_phase_templates(input_dir)
    _write_op_layer(input_dir / "bot_layer.poscar")
    _write_op_layer(input_dir / "top_layer.poscar")
    for element in ("O", "Pt"):
        potcar = tmp_path / "potcars" / element / "POTCAR"
        potcar.parent.mkdir(parents=True)
        potcar.write_text(f"synthetic {element}\n ENMAX = 100;\n", encoding="utf-8")

    run_build(config_path, wait=False)
    return config_path, tmp_path / "work"


def _write_valid_step1(directory):
    shutil.copy2(MLAB_FIXTURES / "complete_vasp_651.mlab", directory / "ML_ABN")
    (directory / "ML_FFN").write_bytes(b"synthetic-step1-force-field\n")
    return 0


def _write_valid_step2(directory):
    assert (directory / "ML_AB").read_bytes() == (
        MLAB_FIXTURES / "complete_vasp_651.mlab"
    ).read_bytes()
    assert (directory / "ML_FF").read_bytes() == b"synthetic-step1-force-field\n"
    shutil.copy2(MLAB_FIXTURES / "complete_multi.mlab", directory / "ML_ABN")
    (directory / "ML_FFN").write_bytes(b"synthetic-step2-force-field\n")
    return 0


def _manifest(work_dir):
    result = read_manifest(work_dir, "init_mlff")
    assert result.kind == "current"
    assert result.manifest is not None
    return result.manifest


def _assert_no_seed_candidates(root):
    assert list(root.rglob("*.candidate")) == []


def test_workflow_runs_both_phases_through_one_adapter_and_publishes_final_seed(tmp_path):
    _config_path, work_dir = _prepare_workflow(tmp_path)
    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": _write_valid_step1, "step2": _write_valid_step2},
    )

    result = InitMlffWorkflow(work_dir).run(adapter)

    root = work_dir / "init_mlff"
    bottom = root / "bottom"
    top = root / "top"
    assert adapter.calls == [
        ("step1", "step-1", bottom, "step-1-running", "step-1-running"),
        ("step2", "step-2", top, "step-2-running", "step-2-running"),
    ]
    assert result.init_workflow["state"] == "complete"
    assert result.init_workflow["phases"]["bottom"]["state"] == "complete"
    assert result.init_workflow["phases"]["top"]["state"] == "complete"
    assert (bottom / "ML_ABN").read_bytes() == (
        MLAB_FIXTURES / "complete_vasp_651.mlab"
    ).read_bytes()
    assert (bottom / "ML_FFN").read_bytes() == b"synthetic-step1-force-field\n"
    assert (top / "ML_AB").read_bytes() == (bottom / "ML_ABN").read_bytes()
    assert (top / "ML_FF").read_bytes() == (bottom / "ML_FFN").read_bytes()
    assert (root / "ML_ABN").read_bytes() == (top / "ML_ABN").read_bytes()
    assert (root / "ML_FFN").read_bytes() == (top / "ML_FFN").read_bytes()
    assert result.mlff_seed == {
        "source": "init_mlff/ML_ABN",
        "configurations": 2,
        "digest_schema": "mlab-seed-v1",
        "seed_prefix_sha256": init_mlff_module.seed_prefix_identity(
            root / "ML_ABN"
        ).sha256,
        "ml_ab_sha256": hashlib.sha256((root / "ML_ABN").read_bytes()).hexdigest(),
        "ml_ff_sha256": hashlib.sha256((root / "ML_FFN").read_bytes()).hexdigest(),
    }
    _assert_no_seed_candidates(root)


def test_nonzero_step1_fails_closed_and_cannot_be_retried_implicitly(tmp_path):
    _config_path, work_dir = _prepare_workflow(tmp_path)

    def fail_step1(directory):
        _write_valid_step1(directory)
        (directory / "private-marker").write_text("do-not-leak", encoding="utf-8")
        return 7

    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": fail_step1, "step2": pytest.fail},
    )

    with pytest.raises(InitMlffWorkflowError) as exc_info:
        InitMlffWorkflow(work_dir).run(adapter)

    assert "step1" in str(exc_info.value)
    assert "exit code 7" in str(exc_info.value)
    assert "do-not-leak" not in str(exc_info.value)
    assert [call[0] for call in adapter.calls] == ["step1"]
    workflow = _manifest(work_dir).init_workflow
    assert workflow["state"] == "step-1-failed"
    assert workflow["phases"]["bottom"]["state"] == "step-1-failed"
    assert workflow["phases"]["top"]["state"] == "planned"
    root = work_dir / "init_mlff"
    assert not (root / "top" / "ML_AB").exists()
    assert not (root / "ML_ABN").exists()

    retry = SyntheticCalculationAdapter(
        work_dir,
        {"step1": pytest.fail, "step2": pytest.fail},
    )
    with pytest.raises(InitMlffWorkflowError, match="step-1-failed|state invariant"):
        InitMlffWorkflow(work_dir).run(retry)
    assert retry.calls == []


def test_adapter_exception_is_recorded_without_exposing_private_message(tmp_path):
    _config_path, work_dir = _prepare_workflow(tmp_path)

    def raise_step1(_directory):
        raise RuntimeError("private VASP output must not leak")

    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": raise_step1, "step2": pytest.fail},
    )

    with pytest.raises(InitMlffWorkflowError) as exc_info:
        InitMlffWorkflow(work_dir).run(adapter)

    message = str(exc_info.value)
    assert "step1 adapter invariant" in message
    assert "RuntimeError" in message
    assert "private VASP output" not in message
    assert _manifest(work_dir).init_workflow["state"] == "step-1-failed"


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("missing", "missing"),
        ("empty", "empty"),
        ("partial", "complete"),
        ("malformed", "malformed"),
        ("phase-mismatch", "phase structure"),
        ("missing-ff", "ML_FFN"),
        ("empty-ff", "ML_FFN"),
    ],
)
def test_zero_step1_rejects_untrusted_outputs_before_step2(
    tmp_path,
    case,
    expected,
):
    _config_path, work_dir = _prepare_workflow(tmp_path)

    def invalid_step1(directory):
        if case == "empty":
            (directory / "ML_ABN").write_bytes(b"")
        elif case == "partial":
            shutil.copy2(MLAB_FIXTURES / "tail_force_crop.mlab", directory / "ML_ABN")
        elif case == "malformed":
            shutil.copy2(MLAB_FIXTURES / "internal_corruption.mlab", directory / "ML_ABN")
        elif case == "phase-mismatch":
            shutil.copy2(MLAB_FIXTURES / "complete_vasp_641.mlab", directory / "ML_ABN")
        elif case in {"missing-ff", "empty-ff"}:
            shutil.copy2(
                MLAB_FIXTURES / "complete_vasp_651.mlab",
                directory / "ML_ABN",
            )
        if case not in {"missing-ff", "empty-ff"}:
            (directory / "ML_FFN").write_bytes(b"synthetic-force-field\n")
        elif case == "empty-ff":
            (directory / "ML_FFN").write_bytes(b"")
        return 0

    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": invalid_step1, "step2": pytest.fail},
    )

    with pytest.raises(InitMlffWorkflowError) as exc_info:
        InitMlffWorkflow(work_dir).run(adapter)

    message = str(exc_info.value)
    assert "step1 output invariant" in message
    assert expected in message
    assert [call[0] for call in adapter.calls] == ["step1"]
    assert _manifest(work_dir).init_workflow["state"] == "step-1-failed"
    root = work_dir / "init_mlff"
    assert not (root / "top" / "ML_AB").exists()
    assert not (root / "ML_ABN").exists()


def test_handoff_interruption_leaves_no_formal_or_partial_top_seed(
    tmp_path,
    monkeypatch,
):
    _config_path, work_dir = _prepare_workflow(tmp_path)
    real_publish = init_mlff_module.atomic_file_publish_no_replace
    calls = 0

    def fail_second_file(candidate, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic handoff interruption")
        return real_publish(candidate, destination)

    monkeypatch.setattr(
        init_mlff_module,
        "atomic_file_publish_no_replace",
        fail_second_file,
    )
    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": _write_valid_step1, "step2": pytest.fail},
    )

    with pytest.raises(InitMlffWorkflowError, match="step1 handoff invariant"):
        InitMlffWorkflow(work_dir).run(adapter)

    root = work_dir / "init_mlff"
    assert not (root / "top" / "ML_AB").exists()
    assert not (root / "top" / "ML_FF").exists()
    assert _manifest(work_dir).init_workflow["state"] == "step-1-failed"
    assert [call[0] for call in adapter.calls] == ["step1"]
    _assert_no_seed_candidates(root)


def test_step2_ready_manifest_failure_rolls_back_handoff_files(tmp_path, monkeypatch):
    _config_path, work_dir = _prepare_workflow(tmp_path)
    real_write_manifest = init_mlff_module.write_manifest
    failed = False

    def fail_ready_once(target_work_dir, manifest):
        nonlocal failed
        if manifest.init_workflow["state"] == "step-2-ready" and not failed:
            failed = True
            raise OSError("synthetic ready-state interruption")
        return real_write_manifest(target_work_dir, manifest)

    monkeypatch.setattr(init_mlff_module, "write_manifest", fail_ready_once)
    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": _write_valid_step1, "step2": pytest.fail},
    )

    with pytest.raises(InitMlffWorkflowError, match="step1 handoff invariant"):
        InitMlffWorkflow(work_dir).run(adapter)

    root = work_dir / "init_mlff"
    assert not (root / "top" / "ML_AB").exists()
    assert not (root / "top" / "ML_FF").exists()
    assert _manifest(work_dir).init_workflow["state"] == "step-1-failed"
    assert [call[0] for call in adapter.calls] == ["step1"]


def test_nonzero_step2_preserves_bottom_evidence_and_failed_top_directory(tmp_path):
    _config_path, work_dir = _prepare_workflow(tmp_path)

    def fail_step2(directory):
        (directory / "synthetic-output").write_text("kept", encoding="utf-8")
        return 9

    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": _write_valid_step1, "step2": fail_step2},
    )

    with pytest.raises(InitMlffWorkflowError, match="step2.*exit code 9"):
        InitMlffWorkflow(work_dir).run(adapter)

    root = work_dir / "init_mlff"
    workflow = _manifest(work_dir).init_workflow
    assert workflow["state"] == "step-2-failed"
    assert workflow["phases"]["bottom"]["state"] == "step-1-complete"
    assert workflow["phases"]["top"]["state"] == "step-2-failed"
    assert (root / "bottom" / "ML_ABN").is_file()
    assert (root / "top" / "ML_AB").is_file()
    assert (root / "top" / "synthetic-output").read_text(encoding="utf-8") == "kept"
    assert not (root / "ML_ABN").exists()
    assert not (root / "ML_FFN").exists()


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("missing", "missing"),
        ("malformed", "malformed"),
        ("no-new", "new top"),
        ("prefix-mismatch", "seed prefix"),
    ],
)
def test_zero_step2_rejects_invalid_final_outputs(tmp_path, case, expected):
    _config_path, work_dir = _prepare_workflow(tmp_path)

    def invalid_step2(directory):
        if case == "malformed":
            shutil.copy2(MLAB_FIXTURES / "internal_corruption.mlab", directory / "ML_ABN")
        elif case == "no-new":
            shutil.copy2(
                MLAB_FIXTURES / "complete_vasp_651.mlab",
                directory / "ML_ABN",
            )
        elif case == "prefix-mismatch":
            text = (MLAB_FIXTURES / "complete_multi.mlab").read_text(encoding="utf-8")
            text = text.replace("-1.250000000000000E+000", "-9.250000000000000E+000", 1)
            (directory / "ML_ABN").write_text(text, encoding="utf-8")
        if case != "missing":
            (directory / "ML_FFN").write_bytes(b"synthetic-step2-force-field\n")
        return 0

    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": _write_valid_step1, "step2": invalid_step2},
    )

    with pytest.raises(InitMlffWorkflowError) as exc_info:
        InitMlffWorkflow(work_dir).run(adapter)

    message = str(exc_info.value)
    assert "step2 output invariant" in message
    assert expected in message
    assert [call[0] for call in adapter.calls] == ["step1", "step2"]
    root = work_dir / "init_mlff"
    assert _manifest(work_dir).init_workflow["state"] == "step-2-failed"
    assert not (root / "ML_ABN").exists()
    assert not (root / "ML_FFN").exists()


def test_final_publication_interruption_rolls_back_root_seed(tmp_path, monkeypatch):
    _config_path, work_dir = _prepare_workflow(tmp_path)
    real_publish = init_mlff_module.atomic_file_publish_no_replace
    calls = 0

    def fail_final_second_file(candidate, destination):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("synthetic final publication interruption")
        return real_publish(candidate, destination)

    monkeypatch.setattr(
        init_mlff_module,
        "atomic_file_publish_no_replace",
        fail_final_second_file,
    )
    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": _write_valid_step1, "step2": _write_valid_step2},
    )

    with pytest.raises(InitMlffWorkflowError, match="step2 publication invariant"):
        InitMlffWorkflow(work_dir).run(adapter)

    root = work_dir / "init_mlff"
    assert not (root / "ML_ABN").exists()
    assert not (root / "ML_FFN").exists()
    assert _manifest(work_dir).init_workflow["state"] == "step-2-failed"
    assert (root / "top" / "ML_ABN").is_file()
    assert (root / "top" / "ML_FFN").is_file()
    _assert_no_seed_candidates(root)


def test_complete_manifest_failure_rolls_back_root_seed(tmp_path, monkeypatch):
    _config_path, work_dir = _prepare_workflow(tmp_path)
    real_write_manifest = init_mlff_module.write_manifest
    failed = False

    def fail_complete_once(target_work_dir, manifest):
        nonlocal failed
        if manifest.init_workflow["state"] == "complete" and not failed:
            failed = True
            raise OSError("synthetic complete-state interruption")
        return real_write_manifest(target_work_dir, manifest)

    monkeypatch.setattr(init_mlff_module, "write_manifest", fail_complete_once)
    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": _write_valid_step1, "step2": _write_valid_step2},
    )

    with pytest.raises(InitMlffWorkflowError, match="step2 publication invariant"):
        InitMlffWorkflow(work_dir).run(adapter)

    root = work_dir / "init_mlff"
    assert not (root / "ML_ABN").exists()
    assert not (root / "ML_FFN").exists()
    assert _manifest(work_dir).init_workflow["state"] == "step-2-failed"
    assert (root / "top" / "ML_ABN").is_file()
    assert (root / "top" / "ML_FFN").is_file()


def test_workflow_refuses_preexisting_publication_without_adapter(tmp_path):
    _config_path, work_dir = _prepare_workflow(tmp_path)
    root = work_dir / "init_mlff"
    (root / "ML_ABN").write_text("preexisting", encoding="utf-8")
    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": pytest.fail, "step2": pytest.fail},
    )

    with pytest.raises(InitMlffWorkflowError, match="preflight invariant"):
        InitMlffWorkflow(work_dir).run(adapter)

    assert adapter.calls == []
    assert (root / "ML_ABN").read_text(encoding="utf-8") == "preexisting"
    assert _manifest(work_dir).init_workflow["state"] == "step-1-ready"


def test_workflow_refuses_static_input_drift_without_adapter(tmp_path):
    _config_path, work_dir = _prepare_workflow(tmp_path)
    root = work_dir / "init_mlff"
    (root / "bottom" / "INCAR").write_text("changed", encoding="utf-8")
    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": pytest.fail, "step2": pytest.fail},
    )

    with pytest.raises(InitMlffWorkflowError, match="static input.*changed"):
        InitMlffWorkflow(work_dir).run(adapter)

    assert adapter.calls == []
    assert _manifest(work_dir).init_workflow["state"] == "step-1-ready"


def test_step1_rechecks_recorded_static_evidence_after_adapter_returns(tmp_path):
    _config_path, work_dir = _prepare_workflow(tmp_path)

    def mutate_step1_static(directory):
        _write_valid_step1(directory)
        with (directory / "INCAR").open("a", encoding="utf-8") as handle:
            handle.write("# changed during calculation\n")
        return 0

    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": mutate_step1_static, "step2": pytest.fail},
    )

    with pytest.raises(InitMlffWorkflowError, match="step1 static-input invariant"):
        InitMlffWorkflow(work_dir).run(adapter)

    assert [call[0] for call in adapter.calls] == ["step1"]
    assert _manifest(work_dir).init_workflow["state"] == "step-1-failed"


def test_step2_rechecks_recorded_static_evidence_after_adapter_returns(tmp_path):
    _config_path, work_dir = _prepare_workflow(tmp_path)

    def mutate_step2_static(directory):
        _write_valid_step2(directory)
        with (directory / "KPOINTS").open("a", encoding="utf-8") as handle:
            handle.write("# changed during calculation\n")
        return 0

    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": _write_valid_step1, "step2": mutate_step2_static},
    )

    with pytest.raises(InitMlffWorkflowError, match="step2 static-input invariant"):
        InitMlffWorkflow(work_dir).run(adapter)

    assert [call[0] for call in adapter.calls] == ["step1", "step2"]
    assert _manifest(work_dir).init_workflow["state"] == "step-2-failed"
    assert not (work_dir / "init_mlff" / "ML_ABN").exists()


@pytest.mark.parametrize(
    ("workflow_state", "bottom_state", "top_state"),
    [
        ("planned", "planned", "planned"),
        ("step-1-running", "step-1-running", "planned"),
        ("step-1-failed", "step-1-failed", "planned"),
    ],
)
def test_stage1_seed_gate_rejects_unpublished_automated_states_even_when_files_exist(
    tmp_path,
    workflow_state,
    bottom_state,
    top_state,
):
    config_path, work_dir = _prepare_workflow(tmp_path)
    manifest = _manifest(work_dir)
    workflow = manifest.init_workflow
    workflow["state"] = workflow_state
    workflow["phases"]["bottom"]["state"] = bottom_state
    workflow["phases"]["top"]["state"] = top_state
    write_manifest(work_dir, manifest)
    root = work_dir / "init_mlff"
    shutil.copy2(MLAB_FIXTURES / "complete_multi.mlab", root / "ML_ABN")
    (root / "ML_FFN").write_bytes(b"unpublished-force-field\n")
    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config_data["stage"] = 1
    config_path.write_text(yaml.safe_dump(config_data), encoding="utf-8")

    with pytest.raises(RuntimeError) as exc_info:
        run_build(config_path, wait=False)

    message = str(exc_info.value)
    assert f"state {workflow_state!r} is not complete" in message
    assert not (work_dir / "md").exists()


def test_stage1_consumes_the_hash_verified_published_seed_unchanged(tmp_path):
    config_path, work_dir = _prepare_workflow(tmp_path, do_relaxation=True)
    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": _write_valid_step1, "step2": _write_valid_step2},
    )
    InitMlffWorkflow(work_dir).run(adapter)
    root = work_dir / "init_mlff"
    published = validate_published_init_mlff_seed(work_dir)
    assert published is not None

    relaxation = work_dir / "rlx" / "0_0"
    shutil.copy2(relaxation / "POSCAR", relaxation / "CONTCAR")
    (relaxation / "OUTCAR").write_text(
        "reached required accuracy - stopping structural energy minimisation\n",
        encoding="utf-8",
    )
    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config_data["stage"] = 1
    config_path.write_text(yaml.safe_dump(config_data), encoding="utf-8")

    run_build(config_path, wait=False)

    md_dir = work_dir / "md" / "0_0"
    assert (md_dir / "ML_AB").read_bytes() == (root / "ML_ABN").read_bytes()
    assert (md_dir / "ML_FF").read_bytes() == (root / "ML_FFN").read_bytes()
    assert _manifest(work_dir).mlff_seed == read_manifest(work_dir, "md").manifest.mlff_seed


def test_published_seed_hash_evidence_rejects_post_publication_tampering(tmp_path):
    _config_path, work_dir = _prepare_workflow(tmp_path)
    adapter = SyntheticCalculationAdapter(
        work_dir,
        {"step1": _write_valid_step1, "step2": _write_valid_step2},
    )
    InitMlffWorkflow(work_dir).run(adapter)
    root = work_dir / "init_mlff"
    (root / "ML_FFN").write_bytes(b"tampered-force-field\n")

    with pytest.raises(InitMlffWorkflowError, match="ML_FFN hash changed"):
        validate_published_init_mlff_seed(work_dir)
