from dataclasses import asdict
from copy import deepcopy

import pytest
import yaml

import dpmoire_lite.atomic_io as atomic_io
from dpmoire_lite.manifest import Manifest, read_manifest, write_manifest
from dpmoire_lite.paths import manifest_path


def _write_yaml(work_dir, stage, data):
    path = manifest_path(work_dir, stage)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _valid_init_workflow():
    static_inputs = {
        name: {"size": index + 1, "sha256": f"{index + 1:x}" * 64}
        for index, name in enumerate(("POSCAR", "POTCAR", "INCAR", "KPOINTS", "sub"))
    }
    return {
        "schema": "dpmoire-lite.init-workflow.v1",
        "mode": "single-job",
        "state": "step-1-ready",
        "submit_source": {"name": "sub", "size": 7, "sha256": "a" * 64},
        "phases": {
            "bottom": {
                "role": "step-1",
                "state": "step-1-ready",
                "directory": "init_mlff/bottom",
                "incar_template": {
                    "name": "init_bottom_INCAR",
                    "size": 8,
                    "sha256": "b" * 64,
                },
                "static_inputs": static_inputs,
            },
            "top": {
                "role": "step-2",
                "state": "planned",
                "directory": "init_mlff/top",
                "incar_template": {
                    "name": "init_top_INCAR",
                    "size": 9,
                    "sha256": "c" * 64,
                },
                "static_inputs": static_inputs,
            },
        },
    }


def _valid_published_init_seed():
    return {
        "source": "init_mlff/ML_ABN",
        "configurations": 2,
        "digest_schema": "mlab-seed-v1",
        "seed_prefix_sha256": "d" * 64,
        "ml_ab_sha256": "e" * 64,
        "ml_ff_sha256": "f" * 64,
    }


def test_read_manifest_reports_missing_separately(tmp_path):
    work_dir = tmp_path / "work"

    result = read_manifest(work_dir, "rlx")

    assert result.kind == "missing"
    assert result.manifest is None
    assert result.raw_data is None


def test_legacy_read_does_not_rewrite_source_manifest(tmp_path):
    work_dir = tmp_path / "work"
    legacy = {
        "stage": "rlx",
        "generated_at": "2026-07-11T12:00:00",
        "directories": ["rlx/0_0"],
        "stackings": [[0, 0]],
    }
    path = _write_yaml(work_dir, "rlx", legacy)
    original_bytes = path.read_bytes()

    result = read_manifest(work_dir, "rlx")

    assert result.kind == "legacy"
    assert result.manifest is None
    assert result.raw_data == legacy
    assert path.read_bytes() == original_bytes


def test_read_manifest_rejects_invalid_v2_schema(tmp_path):
    work_dir = tmp_path / "work"
    _write_yaml(
        work_dir,
        "rlx",
        {"schema_version": 2, "stage": "rlx", "directories": "rlx/0_0"},
    )

    with pytest.raises(ValueError, match="generated_at|directories"):
        read_manifest(work_dir, "rlx")


def test_manifest_v2_round_trip_preserves_extension_sections(tmp_path):
    work_dir = tmp_path / "work"
    manifest = Manifest(
        schema_version=2,
        stage="rlx",
        generated_at="2026-07-11T12:00:00",
        directories=["rlx/0_0"],
        stackings=[[0, 0]],
        structure_provenance={"sc_rlx": False, "input_sha256": {"top": "abc"}},
        grid_shift_anchors={"0_0": [0.0, 0.0]},
        mlff_seed={"algorithm": "mlab-seed-v1", "sha256": "def"},
        partial=[{"path": "rlx/0_0", "reason": "truncated tail"}],
        collect={
            "status": "degraded",
            "transaction_id": "collect-test-1",
            "output_sha256": "123",
            "previous_sha256": "456",
            "backup": "backups/collect/rlx_data.collect-test-1.extxyz",
        },
    )

    write_manifest(work_dir, manifest)
    result = read_manifest(work_dir, "rlx")

    assert result.kind == "current"
    assert result.manifest is not None
    assert asdict(result.manifest) == asdict(manifest)


def test_init_workflow_v1_round_trip_preserves_bounded_evidence(tmp_path):
    work_dir = tmp_path / "work"
    manifest = Manifest(
        stage="init_mlff",
        generated_at="2026-08-11T12:00:00",
        directories=["init_mlff/bottom", "init_mlff/top"],
        init_workflow=_valid_init_workflow(),
    )

    write_manifest(work_dir, manifest)
    result = read_manifest(work_dir, "init_mlff")

    assert result.kind == "current"
    assert result.manifest is not None
    assert result.manifest.init_workflow == manifest.init_workflow


@pytest.mark.parametrize(
    ("workflow_state", "bottom_state", "top_state"),
    [
        ("step-1-running", "step-1-running", "planned"),
        ("step-1-failed", "step-1-failed", "planned"),
        ("step-1-complete", "step-1-complete", "planned"),
        ("step-2-ready", "step-1-complete", "step-2-ready"),
        ("step-2-running", "step-1-complete", "step-2-running"),
        ("step-2-failed", "step-1-complete", "step-2-failed"),
        ("complete", "complete", "complete"),
    ],
)
def test_init_workflow_v1_round_trip_accepts_transactional_lifecycle_states(
    tmp_path,
    workflow_state,
    bottom_state,
    top_state,
):
    work_dir = tmp_path / "work"
    workflow = deepcopy(_valid_init_workflow())
    workflow["state"] = workflow_state
    workflow["phases"]["bottom"]["state"] = bottom_state
    workflow["phases"]["top"]["state"] = top_state
    manifest = Manifest(
        stage="init_mlff",
        generated_at="2026-08-11T12:00:00",
        directories=["init_mlff/bottom", "init_mlff/top"],
        mlff_seed=(
            _valid_published_init_seed() if workflow_state == "complete" else {}
        ),
        init_workflow=workflow,
    )

    write_manifest(work_dir, manifest)

    result = read_manifest(work_dir, "init_mlff")
    assert result.kind == "current"
    assert result.manifest is not None
    assert result.manifest.init_workflow == workflow


def test_init_workflow_v1_rejects_inconsistent_transactional_phase_states(tmp_path):
    work_dir = tmp_path / "work"
    workflow = deepcopy(_valid_init_workflow())
    workflow["state"] = "step-2-ready"
    workflow["phases"]["bottom"]["state"] = "step-1-running"
    workflow["phases"]["top"]["state"] = "step-2-ready"
    manifest = Manifest(
        stage="init_mlff",
        generated_at="2026-08-11T12:00:00",
        directories=["init_mlff/bottom", "init_mlff/top"],
        init_workflow=workflow,
    )

    with pytest.raises(ValueError, match="requires bottom/top states"):
        write_manifest(work_dir, manifest)


def test_complete_init_workflow_requires_bounded_published_seed_evidence(tmp_path):
    work_dir = tmp_path / "work"
    workflow = deepcopy(_valid_init_workflow())
    workflow["state"] = "complete"
    workflow["phases"]["bottom"]["state"] = "complete"
    workflow["phases"]["top"]["state"] = "complete"
    manifest = Manifest(
        stage="init_mlff",
        generated_at="2026-08-11T12:00:00",
        directories=["init_mlff/bottom", "init_mlff/top"],
        init_workflow=workflow,
    )

    with pytest.raises(ValueError, match="mlff_seed.*fields mismatch"):
        write_manifest(work_dir, manifest)


@pytest.mark.parametrize(
    ("field_path", "value", "match"),
    [
        (("schema",), "dpmoire-lite.init-workflow.v2", "schema"),
        (("state",), "running", "state"),
        (("phases", "bottom", "role"), "step-2", "role"),
        (("phases", "bottom", "directory"), "../outside", "safe relative path"),
        (
            ("phases", "bottom", "static_inputs", "POSCAR", "sha256"),
            "not-a-digest",
            "SHA-256",
        ),
    ],
)
def test_read_manifest_rejects_invalid_init_workflow_v1(
    tmp_path,
    field_path,
    value,
    match,
):
    work_dir = tmp_path / "work"
    data = asdict(
        Manifest(
            stage="init_mlff",
            generated_at="2026-08-11T12:00:00",
            directories=["init_mlff/bottom", "init_mlff/top"],
            init_workflow=_valid_init_workflow(),
        )
    )
    target = data["init_workflow"]
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = value
    _write_yaml(work_dir, "init_mlff", data)

    with pytest.raises(ValueError, match=match):
        read_manifest(work_dir, "init_mlff")


def test_read_manifest_never_creates_a_file(tmp_path):
    work_dir = tmp_path / "work"
    path = manifest_path(work_dir, "md")

    result = read_manifest(work_dir, "md")

    assert result.kind == "missing"
    assert not path.exists()


def test_write_manifest_uses_atomic_publisher(tmp_path, monkeypatch):
    observed = {}

    def fake_publish(destination, text, **kwargs):
        observed["destination"] = destination
        observed["text"] = text
        observed["kwargs"] = kwargs
        return atomic_io.AtomicPublishResult(destination=destination, sha256=None)

    monkeypatch.setattr(atomic_io, "atomic_text_publish", fake_publish)

    work_dir = tmp_path / "work"
    write_manifest(
        work_dir,
        Manifest(stage="rlx", generated_at="2026-07-11T12:00:00"),
    )

    assert observed["destination"] == manifest_path(work_dir, "rlx")
    assert "schema_version: 2" in observed["text"]
    assert observed["kwargs"]["encoding"] == "utf-8"


def test_write_manifest_failure_preserves_previous_manifest(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    destination = manifest_path(work_dir, "rlx")
    destination.parent.mkdir(parents=True)
    previous = b"previous manifest bytes\n"
    destination.write_bytes(previous)

    def fail_publish(*_args, **_kwargs):
        raise OSError("manifest candidate publication failed")

    monkeypatch.setattr(atomic_io, "atomic_text_publish", fail_publish)

    with pytest.raises(OSError, match="manifest candidate publication failed"):
        write_manifest(
            work_dir,
            Manifest(stage="rlx", generated_at="2026-07-11T12:00:00"),
        )

    assert destination.read_bytes() == previous


def test_manifest_v2_rejects_stage_path_escape(tmp_path):
    work_dir = tmp_path / "work"

    with pytest.raises(ValueError, match="directories|work_dir"):
        write_manifest(
            work_dir,
            Manifest(
                stage="rlx",
                generated_at="2026-07-11T12:00:00",
                directories=["rlx/../../outside"],
            ),
        )


def test_manifest_v2_rejects_absolute_directory_outside_workdir(tmp_path):
    work_dir = tmp_path / "work"
    outside = (tmp_path / "outside" / "0_0").resolve()

    with pytest.raises(ValueError, match="directories|work_dir"):
        write_manifest(
            work_dir,
            Manifest(
                stage="rlx",
                generated_at="2026-07-11T12:00:00",
                directories=[str(outside)],
            ),
        )


def test_manifest_directory_accepts_normalized_relative_path(tmp_path):
    work_dir = tmp_path / "work"
    write_manifest(
        work_dir,
        Manifest(
            stage="rlx",
            generated_at="2026-07-11T12:00:00",
            directories=[r"rlx\0_0"],
        ),
    )

    raw = yaml.safe_load(manifest_path(work_dir, "rlx").read_text(encoding="utf-8"))
    result = read_manifest(work_dir, "rlx")

    assert raw["directories"] == ["rlx/0_0"]
    assert result.manifest is not None
    assert result.manifest.directories == ["rlx/0_0"]


def test_manifest_directory_rejects_parent_traversal(tmp_path):
    work_dir = tmp_path / "work"

    with pytest.raises(ValueError, match="directories"):
        write_manifest(
            work_dir,
            Manifest(
                stage="rlx",
                generated_at="2026-07-11T12:00:00",
                directories=["rlx/../rlx/0_0"],
            ),
        )


def test_manifest_directory_rejects_symlink_escape_when_resolvable(tmp_path):
    work_dir = tmp_path / "work"
    outside = tmp_path / "outside"
    work_dir.mkdir()
    outside.mkdir()
    link = work_dir / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(ValueError, match="directories|work_dir"):
        write_manifest(
            work_dir,
            Manifest(
                stage="rlx",
                generated_at="2026-07-11T12:00:00",
                directories=["link/0_0"],
            ),
        )


def test_manifest_output_path_must_match_stage_contract(tmp_path):
    with pytest.raises(ValueError, match="output|stage"):
        write_manifest(
            tmp_path / "work",
            Manifest(
                stage="md",
                generated_at="2026-07-11T12:00:00",
                collect={"output": "rlx_data.extxyz"},
            ),
        )


def test_manifest_stage_must_match_manifest_location(tmp_path):
    work_dir = tmp_path / "work"
    _write_yaml(
        work_dir,
        "rlx",
        {
            "schema_version": 2,
            "stage": "md",
            "generated_at": "2026-07-11T12:00:00",
            "directories": [],
            "backups": [],
            "jobs": [],
            "collect": {},
            "skipped": [],
            "failed": [],
            "stackings": [],
            "angles": [],
            "config_summary": {},
            "structure_provenance": {},
            "grid_shift_anchors": {},
            "mlff_seed": {},
            "partial": [],
        },
    )

    with pytest.raises(ValueError, match="stage|manifest"):
        read_manifest(work_dir, "rlx")
