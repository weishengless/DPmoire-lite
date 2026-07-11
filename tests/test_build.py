import math
import shutil

from ase import Atoms
from ase.io.vasp import read_vasp, write_vasp
import pytest
import yaml

import dpmoire_lite.build as build_module
from dpmoire_lite.build import _ordered_elements, build_stage0, build_stage1, build_stage_all, run_build
from dpmoire_lite.config import ConfigError, load_config
from dpmoire_lite.slurm import SlurmJob, parse_sbatch_output, parse_sacct_states


def assert_temporary_safety_error(error: ConfigError) -> None:
    message = str(error)
    assert "temporarily disabled" in message
    assert "Slurm terminal-state validation and failure propagation" in message
    assert "submit: false" in message
    assert "manually" in message


def write_minimal_inputs(root, *, top_a=4.0, bot_a=4.0):
    root.mkdir(parents=True, exist_ok=True)
    input_dir = root / "input"
    scripts = root / "scripts"
    potcars = root / "potcars"
    input_dir.mkdir()
    scripts.mkdir()
    for element in ["H"]:
        (potcars / element).mkdir(parents=True, exist_ok=True)
        (potcars / element / "POTCAR").write_text(" ENMAX = 100; \n", encoding="utf-8")
    top_poscar = f"""H
1.0
  {top_a} 0.0 0.0
  0.0 {top_a} 0.0
  0.0 0.0 12.0
H
1
Direct
  0.0 0.0 0.25
"""
    bot_poscar = f"""H
1.0
  {bot_a} 0.0 0.0
  0.0 {bot_a} 0.0
  0.0 0.0 12.0
H
1
Direct
  0.0 0.0 0.25
"""
    (input_dir / "top_layer.poscar").write_text(top_poscar, encoding="utf-8")
    (input_dir / "bot_layer.poscar").write_text(bot_poscar, encoding="utf-8")
    for name in ["init_INCAR", "rlx_INCAR", "MD_INCAR", "MD_monolayer_INCAR", "val_INCAR"]:
        (input_dir / name).write_text("ENCUT = 400\nML_RCUT1 = 6\nML_RCUT2 = 6\nLANGEVIN_GAMMA = 1\n", encoding="utf-8")
    (scripts / "DFT_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    return input_dir, scripts, potcars


def write_build_config(root, **overrides):
    input_kwargs = overrides.pop("input_kwargs", {})
    input_dir, scripts, potcars = write_minimal_inputs(root, **input_kwargs)
    data = {
        "dft_script": "DFT_script.sh",
        "potcar_dir": str(potcars),
        "script_dir": str(scripts),
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
        "n_sectors": [2, 1],
        "sc": [1, 1],
        "d": 4.0,
        "k_mesh": 20,
        "encut_factor": 1.5,
        "r_cut": -1,
        "symm_reduce": False,
        "twist_val": False,
        "min_val_n": 4,
        "max_val_n": 5,
        "include_monolayer_md": True,
    }
    data.update(overrides)
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def write_converged_relaxation(work, name="0_0", *, atoms=None, with_velocity_block=False):
    target = work / "rlx" / name
    target.mkdir(parents=True, exist_ok=True)
    atoms = atoms or Atoms("H", positions=[[0, 0, 0]], cell=[4, 5, 12], pbc=True)
    write_vasp(target / "CONTCAR", atoms=atoms, direct=True)
    if with_velocity_block:
        with (target / "CONTCAR").open("a", encoding="utf-8") as handle:
            handle.write("Cartesian\n")
            handle.write("  9.0 9.0 9.0\n")
    (target / "OUTCAR").write_text(
        "reached required accuracy - stopping structural energy minimisation\n",
        encoding="utf-8",
    )
    return target


def read_selective_dynamics_flags(poscar):
    lines = poscar.read_text(encoding="utf-8").splitlines()
    selective_idx = next(i for i, line in enumerate(lines) if line.strip().lower().startswith("selective"))
    counts = [int(value) for value in lines[6].split()]
    atom_count = sum(counts)
    first_atom_line = selective_idx + 2
    return [tuple(line.split()[-3:]) for line in lines[first_atom_line : first_atom_line + atom_count]]


def assert_one_atom_per_layer_keeps_only_c_motion(poscar, expected_atom_count):
    flags = read_selective_dynamics_flags(poscar)
    atoms = read_vasp(poscar)
    scaled_z = atoms.get_scaled_positions()[:, 2]
    constrained_layers = [z > 0.5 for flag, z in zip(flags, scaled_z) if flag == ("F", "F", "T")]

    assert len(flags) == expected_atom_count
    assert set(flags) <= {("F", "F", "T"), ("T", "T", "T")}
    assert len(constrained_layers) == 2
    assert constrained_layers.count(True) == 1
    assert constrained_layers.count(False) == 1


class FakeRunner:
    def __init__(self):
        self.events = []
        self.submit_paths = []
        self._next_id = 1

    def submit(self, work_dir, rel_path):
        self.events.append(("submit", rel_path))
        self.submit_paths.append(rel_path)
        job = SlurmJob(job_id=str(self._next_id), path=rel_path)
        self._next_id += 1
        return job

    def wait(self, jobs):
        paths = [job.path for job in jobs]
        self.events.append(("wait", paths))
        for job in jobs:
            work_dir = self.root / job.path
            if job.path == "init_mlff":
                if (work_dir / "ML_AB").exists():
                    (work_dir / "ML_ABN").write_text("step2-abn", encoding="utf-8")
                    (work_dir / "ML_FFN").write_text("step2-ffn", encoding="utf-8")
                else:
                    (work_dir / "ML_ABN").write_text("step1-abn", encoding="utf-8")
                    (work_dir / "ML_FFN").write_text("step1-ffn", encoding="utf-8")
            elif job.path.startswith("rlx/"):
                shutil.copy2(work_dir / "POSCAR", work_dir / "CONTCAR")
                (work_dir / "OUTCAR").write_text(
                    "reached required accuracy - stopping structural energy minimisation\n",
                    encoding="utf-8",
                )
            job.status = "COMPLETED"
        return jobs


def test_stage0_generates_init_and_rlx_dirs(tmp_path):
    config = write_build_config(tmp_path)
    run_build(config, wait=False)
    work = tmp_path / "work"
    assert (work / "init_mlff" / "POSCAR").exists()
    assert (work / "rlx" / "0_0" / "INCAR").exists()
    assert (work / "rlx" / "1_0" / "KPOINTS").exists()
    assert (work / "rlx" / "manifest.yaml").exists()


@pytest.mark.parametrize(("sc_rlx", "expected_rlx_mesh"), [(False, "5 5 1"), (True, "3 3 1")])
def test_stage0_kpoints_use_generated_poscar_cell_lengths(tmp_path, sc_rlx, expected_rlx_mesh):
    config = write_build_config(
        tmp_path,
        n_sectors=[1, 1],
        sc=[2, 2],
        sc_rlx=sc_rlx,
        input_kwargs={"top_a": 4.0, "bot_a": 4.0},
    )

    run_build(config, wait=False)

    init_lines = (tmp_path / "work" / "init_mlff" / "KPOINTS").read_text(encoding="utf-8").splitlines()
    rlx_lines = (tmp_path / "work" / "rlx" / "0_0" / "KPOINTS").read_text(encoding="utf-8").splitlines()
    assert init_lines[3] == "3 3 1"
    assert rlx_lines[3] == expected_rlx_mesh


def test_stage0_uses_minimal_potcar_policy(tmp_path):
    config = write_build_config(tmp_path, n_sectors=[1, 1], potcar_policy="minimal")
    input_dir = tmp_path / "input"
    potcars = tmp_path / "potcars"
    mo_poscar = """Mo
1.0
  3.0 0.0 0.0
  0.0 3.0 0.0
  0.0 0.0 12.0
Mo
1
Direct
  0.0 0.0 0.25
"""
    (input_dir / "top_layer.poscar").write_text(mo_poscar, encoding="utf-8")
    (input_dir / "bot_layer.poscar").write_text(mo_poscar, encoding="utf-8")
    (potcars / "Mo").mkdir(parents=True)
    (potcars / "Mo_sv").mkdir(parents=True)
    (potcars / "Mo" / "POTCAR").write_text("minimal-mo\n ENMAX = 224; ZVAL = 6; \n", encoding="utf-8")
    (potcars / "Mo_sv" / "POTCAR").write_text("recommended-mo\n ENMAX = 242; ZVAL = 14; \n", encoding="utf-8")

    run_build(config, wait=False)

    assert (tmp_path / "work" / "init_mlff" / "POTCAR").read_text(encoding="utf-8") == (
        "minimal-mo\n ENMAX = 224; ZVAL = 6; \n"
    )
    manifest = yaml.safe_load((tmp_path / "work" / "init_mlff" / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["config_summary"]["potcar_policy"] == "minimal"


@pytest.mark.parametrize(
    ("sc_rlx", "sc", "expected_atom_count"),
    [
        (True, [2, 1], 4),
        (False, [2, 1], 2),
    ],
)
def test_stage0_rlx_poscars_keep_in_plane_sliding_constraints(tmp_path, sc_rlx, sc, expected_atom_count):
    config = write_build_config(tmp_path, n_sectors=[1, 1], sc_rlx=sc_rlx, sc=sc)

    run_build(config, wait=False)

    assert_one_atom_per_layer_keeps_only_c_motion(tmp_path / "work" / "rlx" / "0_0" / "POSCAR", expected_atom_count)


def test_prepare_init_mlff_step2_renames_ml_files_and_replaces_poscar(tmp_path):
    from dpmoire_lite.build import prepare_init_mlff_step2

    input_dir, _, _ = write_minimal_inputs(tmp_path, top_a=3.0, bot_a=4.0)
    init_dir = tmp_path / "work" / "init_mlff"
    init_dir.mkdir(parents=True)
    (init_dir / "ML_ABN").write_text("abn-data", encoding="utf-8")
    (init_dir / "ML_FFN").write_text("ffn-data", encoding="utf-8")
    (init_dir / "POSCAR").write_text("old poscar\n", encoding="utf-8")

    prepare_init_mlff_step2(init_dir, input_dir, (2, 3))

    assert (init_dir / "ML_AB").read_text(encoding="utf-8") == "abn-data"
    assert (init_dir / "ML_FF").read_text(encoding="utf-8") == "ffn-data"
    assert not (init_dir / "ML_ABN").exists()
    assert not (init_dir / "ML_FFN").exists()
    atoms = read_vasp(init_dir / "POSCAR")
    assert len(atoms) == 6
    assert atoms.cell.lengths()[0] == pytest.approx(6.0)
    assert atoms.cell.lengths()[1] == pytest.approx(9.0)


def test_run_build_rejects_stage_all_without_submit_wait(tmp_path):
    config = write_build_config(tmp_path, stage="all", submit=False)

    with pytest.raises(ConfigError, match="stage: all"):
        run_build(config, wait=True)


@pytest.mark.parametrize(
    ("overrides", "wait"),
    [
        ({"stage": 0, "submit": True}, True),
        ({"stage": "all", "submit": False}, True),
        ({"stage": "all", "submit": True}, False),
    ],
)
def test_build_stage_all_validates_direct_calls(tmp_path, overrides, wait):
    config_path = write_build_config(tmp_path, **overrides)
    config = load_config(config_path)

    with pytest.raises(ConfigError, match="stage: all"):
        build_stage_all(config, wait=wait)


@pytest.mark.parametrize("stage", [0, 1])
def test_safety_gate_runs_before_work_dir_creation(monkeypatch, tmp_path, stage):
    config_path = write_build_config(tmp_path, stage=stage, submit=True)
    work_dir = tmp_path / "work"

    def fail_if_constructed(*_args, **_kwargs):
        raise AssertionError("SlurmRunner was constructed before the safety gate")

    monkeypatch.setattr(build_module, "SlurmRunner", fail_if_constructed)

    with pytest.raises(ConfigError) as exc_info:
        run_build(config_path, wait=True)

    assert_temporary_safety_error(exc_info.value)
    assert not work_dir.exists()


@pytest.mark.parametrize(("stage", "builder"), [(0, build_stage0), (1, build_stage1)])
def test_safety_gate_runs_before_runner_construction(monkeypatch, tmp_path, stage, builder):
    config_path = write_build_config(tmp_path, stage=stage, submit=True)
    config = load_config(config_path)
    runner_constructed = False

    def fail_if_constructed(*_args, **_kwargs):
        nonlocal runner_constructed
        runner_constructed = True
        raise AssertionError("SlurmRunner was constructed before the safety gate")

    monkeypatch.setattr(build_module, "SlurmRunner", fail_if_constructed)

    with pytest.raises(ConfigError) as exc_info:
        builder(config, wait=True)

    assert_temporary_safety_error(exc_info.value)
    assert runner_constructed is False


def test_stage1_generates_md_from_strict_relaxation_inputs_and_mlff(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=1,
        n_sectors=[1, 1],
        include_monolayer_md=False,
    )
    work = tmp_path / "work"
    write_converged_relaxation(work, with_velocity_block=True)
    init_mlff = work / "init_mlff"
    init_mlff.mkdir(parents=True)
    (init_mlff / "ML_ABN").write_text("abn", encoding="utf-8")
    (init_mlff / "ML_FFN").write_text("ffn", encoding="utf-8")

    run_build(config, wait=False)

    md_dir = work / "md" / "0_0"
    assert (md_dir / "INCAR").read_text(encoding="utf-8").startswith("ENCUT")
    assert (md_dir / "KPOINTS").exists()
    assert (md_dir / "POTCAR").exists()
    assert (md_dir / "DFT_script.sh").exists()
    assert (md_dir / "ML_AB").read_text(encoding="utf-8") == "abn"
    assert (md_dir / "ML_FF").read_text(encoding="utf-8") == "ffn"
    poscar_text = (md_dir / "POSCAR").read_text(encoding="utf-8")
    assert "9.0 9.0 9.0" not in poscar_text
    manifest = yaml.safe_load((work / "md" / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["stage"] == "md"
    assert manifest["directories"] == ["md/0_0"]
    assert manifest["stackings"] == [[0, 0]]


def test_stage1_fails_for_unconverged_relaxation(tmp_path):
    config = write_build_config(tmp_path, stage=1, n_sectors=[1, 1], vasp_ml=False)
    target = tmp_path / "work" / "rlx" / "0_0"
    target.mkdir(parents=True, exist_ok=True)
    write_vasp(target / "CONTCAR", atoms=Atoms("H", positions=[[0, 0, 0]], cell=[4, 5, 12], pbc=True))
    (target / "OUTCAR").write_text("not there yet\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="rlx/0_0: Relaxation did not converge"):
        run_build(config, wait=False)


def test_stage1_preflight_reports_all_failures_without_mutating_existing_md(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=1,
        n_sectors=[2, 1],
        vasp_ml=True,
        include_monolayer_md=False,
    )
    work = tmp_path / "work"
    missing_outcar = work / "rlx" / "0_0"
    missing_outcar.mkdir(parents=True, exist_ok=True)
    write_vasp(missing_outcar / "CONTCAR", atoms=Atoms("H", positions=[[0, 0, 0]], cell=[4, 5, 12], pbc=True))
    unconverged = work / "rlx" / "1_0"
    unconverged.mkdir(parents=True, exist_ok=True)
    write_vasp(unconverged / "CONTCAR", atoms=Atoms("H", positions=[[0, 0, 0]], cell=[4, 5, 12], pbc=True))
    (unconverged / "OUTCAR").write_text("not there yet\n", encoding="utf-8")
    existing_md = work / "md" / "0_0"
    existing_md.mkdir(parents=True, exist_ok=True)
    marker = existing_md / "marker.txt"
    marker.write_text("keep me here", encoding="utf-8")

    with pytest.raises(RuntimeError) as exc_info:
        run_build(config, wait=False)

    message = str(exc_info.value)
    assert "rlx/0_0: Missing OUTCAR" in message
    assert "rlx/1_0: Relaxation did not converge" in message
    assert "init_mlff/ML_ABN: missing required MLFF file" in message
    assert "init_mlff/ML_FFN: missing required MLFF file" in message
    assert existing_md.is_dir()
    assert marker.read_text(encoding="utf-8") == "keep me here"
    assert not any(path.name.startswith("0_0.") for path in (work / "md").iterdir())


def test_stage1_expands_primitive_relaxation_when_sc_rlx_is_false(tmp_path):
    config = write_build_config(
        tmp_path,
        stage=1,
        n_sectors=[1, 1],
        sc=[2, 3],
        sc_rlx=False,
        vasp_ml=False,
        include_monolayer_md=False,
    )
    work = tmp_path / "work"
    write_converged_relaxation(work, atoms=Atoms("H", positions=[[0, 0, 0]], cell=[4, 5, 12], pbc=True))

    run_build(config, wait=False)

    atoms = read_vasp(work / "md" / "0_0" / "POSCAR")
    assert len(atoms) == 6
    assert atoms.cell.lengths()[0] == pytest.approx(8.0)
    assert atoms.cell.lengths()[1] == pytest.approx(15.0)


def test_default_rcut_uses_input_layer_cells_and_ignores_supercell_scaling(tmp_path):
    expected = math.sqrt(5.0**2 + 1.0**2) * 1.1
    rendered_rcuts = []

    for sc in ([1, 1], [3, 2]):
        root = tmp_path / f"sc-{sc[0]}-{sc[1]}"
        config = write_build_config(
            root,
            input_kwargs={"top_a": 4.0, "bot_a": 5.0},
            do_relaxation=False,
            d=1.0,
            sc=sc,
        )
        run_build(config, wait=False)
        incar = (root / "work" / "init_mlff" / "INCAR").read_text(encoding="utf-8")
        rcut_line = next(line for line in incar.splitlines() if line.startswith("ML_RCUT1"))
        rendered_rcuts.append(float(rcut_line.split("=", 1)[1].strip()))

    assert rendered_rcuts == [expected, expected]


def test_ordered_elements_preserves_consecutive_symbol_groups():
    atoms = Atoms("MoSMo", positions=[[0, 0, 0], [0, 0, 1], [0, 0, 2]], cell=[4, 4, 12], pbc=True)

    assert _ordered_elements(atoms) == ["Mo", "S", "Mo"]


def test_fire_and_forget_stage0_remains_allowed(monkeypatch, tmp_path):
    fake_runner = FakeRunner()
    fake_runner.root = tmp_path / "work"
    monkeypatch.setattr(build_module, "SlurmRunner", lambda *_args: fake_runner)
    config = write_build_config(
        tmp_path,
        submit=True,
        do_relaxation=False,
        twist_val=False,
    )

    run_build(config, wait=False)

    assert fake_runner.events == [("submit", "init_mlff")]
    assert not (tmp_path / "work" / "init_mlff" / "ML_AB").exists()
    assert not (tmp_path / "work" / "init_mlff" / "ML_FF").exists()


def test_parse_sbatch_output_extracts_job_id():
    assert parse_sbatch_output("Submitted batch job 12345\n") == "12345"


def test_parse_sacct_states_maps_states():
    output = """JobID State
------------ ----------
12345 COMPLETED
12346 FAILED
12347 RUNNING
"""
    states = parse_sacct_states(output)
    assert states["12345"] == "COMPLETED"
    assert states["12346"] == "FAILED"
    assert states["12347"] == "RUNNING"


def test_slurm_job_records_path():
    job = SlurmJob(job_id="12345", path="rlx/0_0", status="SUBMITTED")
    assert job.path == "rlx/0_0"
