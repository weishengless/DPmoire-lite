import yaml

from dpmoire_lite.build import run_build
from dpmoire_lite.slurm import SlurmJob, parse_sbatch_output, parse_sacct_states


def write_minimal_inputs(root):
    input_dir = root / "input"
    scripts = root / "scripts"
    potcars = root / "potcars"
    input_dir.mkdir()
    scripts.mkdir()
    for element in ["H"]:
        (potcars / element).mkdir(parents=True, exist_ok=True)
        (potcars / element / "POTCAR").write_text(" ENMAX = 100; \n", encoding="utf-8")
    poscar = """H
1.0
  4.0 0.0 0.0
  0.0 4.0 0.0
  0.0 0.0 12.0
H
1
Direct
  0.0 0.0 0.25
"""
    (input_dir / "top_layer.poscar").write_text(poscar, encoding="utf-8")
    (input_dir / "bot_layer.poscar").write_text(poscar, encoding="utf-8")
    for name in ["init_INCAR", "rlx_INCAR", "MD_INCAR", "MD_monolayer_INCAR", "val_INCAR"]:
        (input_dir / name).write_text("ENCUT = 400\nML_RCUT1 = 6\nML_RCUT2 = 6\nLANGEVIN_GAMMA = 1\n", encoding="utf-8")
    (scripts / "DFT_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    return input_dir, scripts, potcars


def write_build_config(root, **overrides):
    input_dir, scripts, potcars = write_minimal_inputs(root)
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


def test_stage0_generates_init_and_rlx_dirs(tmp_path):
    config = write_build_config(tmp_path)
    run_build(config, wait=False)
    work = tmp_path / "work"
    assert (work / "init_mlff" / "POSCAR").exists()
    assert (work / "rlx" / "0_0" / "INCAR").exists()
    assert (work / "rlx" / "1_0" / "KPOINTS").exists()
    assert (work / "rlx" / "manifest.yaml").exists()


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
