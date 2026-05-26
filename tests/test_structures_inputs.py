from ase import Atoms
from ase.io.vasp import read_vasp

from dpmoire_lite.structures import generate_stackings, rewrite_contcar_as_poscar


def test_generate_stackings_supports_rectangular_grid():
    assert generate_stackings((3, 2)) == [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)]


def test_rewrite_contcar_as_poscar_removes_velocity_block(tmp_path):
    atoms = Atoms("H2", positions=[[0, 0, 0], [0, 0, 0.74]], cell=[8, 8, 8], pbc=True)
    contcar = tmp_path / "CONTCAR"
    poscar = tmp_path / "POSCAR"
    atoms.write(contcar, format="vasp", direct=True)
    with contcar.open("a", encoding="utf-8") as handle:
        handle.write("\n  0.0 0.0 0.0\n  0.0 0.0 0.0\n")
    rewrite_contcar_as_poscar(contcar, poscar)
    text = poscar.read_text(encoding="utf-8")
    assert text.count("0.0 0.0 0.0") == 0
    loaded = read_vasp(poscar)
    assert len(loaded) == 2
