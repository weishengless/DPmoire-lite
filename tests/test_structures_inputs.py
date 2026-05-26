from ase import Atoms
from ase.io.vasp import read_vasp

from dpmoire_lite.structures import StructureHandler, generate_stackings, rewrite_contcar_as_poscar


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


def test_read_atoms_does_not_clobber_existing_normalized_sibling(tmp_path):
    poscar = tmp_path / "bad.poscar"
    poscar.write_text(
        "\n".join(
            [
                "bad label",
                "1.0",
                "3.0 0.0 0.0",
                "0.0 3.0 0.0",
                "0.0 0.0 3.0",
                "I1",
                "1",
                "Direct",
                "0.0 0.0 0.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    normalized_sibling = tmp_path / "bad.poscar.normalized"
    sentinel = "do not touch this file\n"
    normalized_sibling.write_text(sentinel, encoding="utf-8")

    handler = object.__new__(StructureHandler)
    atoms = handler.read_atoms(poscar)

    assert len(atoms) == 1
    assert atoms.get_chemical_symbols() == ["I"]
    assert normalized_sibling.read_text(encoding="utf-8") == sentinel
