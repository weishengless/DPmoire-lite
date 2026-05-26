import math

from ase import Atoms
from ase.io.vasp import read_vasp, write_vasp
import pytest
import yaml

from dpmoire_lite.build import _ordered_elements, run_build
from dpmoire_lite.slurm import SlurmJob, parse_sbatch_output, parse_sacct_states


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


def test_stage0_generates_init_and_rlx_dirs(tmp_path):
    config = write_build_config(tmp_path)
    run_build(config, wait=False)
    work = tmp_path / "work"
    assert (work / "init_mlff" / "POSCAR").exists()
    assert (work / "rlx" / "0_0" / "INCAR").exists()
    assert (work / "rlx" / "1_0" / "KPOINTS").exists()
    assert (work / "rlx" / "manifest.yaml").exists()


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

    with pytest.raises(ValueError, match="Relaxation did not converge"):
        run_build(config, wait=False)


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
