import builtins

from ase import Atoms
from ase.io.vasp import write_vasp
import pytest

from dpmoire_lite.structures import StructureHandler


def make_handler(tmp_path, n_sectors):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    cell = [[3.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 12.0]]
    top = Atoms("Mo", scaled_positions=[[0.0, 0.0, 0.5]], cell=cell, pbc=True)
    bot = Atoms("S", scaled_positions=[[0.0, 0.0, 0.5]], cell=cell, pbc=True)
    write_vasp(input_dir / "top_layer.poscar", top, direct=True)
    write_vasp(input_dir / "bot_layer.poscar", bot, direct=True)
    return StructureHandler(input_dir, tmp_path / "work", n_sectors, d=3.0)


def read_written_stackings(handler):
    path = handler.work_dir / "sym_reduced_stackings.txt"
    return [tuple(int(value) for value in line.split()) for line in path.read_text().splitlines()]


def test_find_sym_reduced_stackings_is_stable_and_unique(tmp_path):
    handler = make_handler(tmp_path, (2, 2))

    first = handler.find_sym_reduced_stackings()
    second = handler.find_sym_reduced_stackings()

    assert first == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert second == first
    assert len(first) == len(set(first))
    assert read_written_stackings(handler) == second


def test_find_sym_reduced_stackings_supports_rectangular_sectors(tmp_path):
    handler = make_handler(tmp_path, (3, 2))

    stackings = handler.find_sym_reduced_stackings()

    assert stackings == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert len(stackings) == len(set(stackings))
    assert read_written_stackings(handler) == stackings


def test_find_sym_reduced_stackings_reports_missing_optional_dependency(monkeypatch, tmp_path):
    handler = make_handler(tmp_path, (2, 2))
    real_import = builtins.__import__

    def import_without_symmetry_dependencies(name, *args, **kwargs):
        if name == "pymatgen" or name.startswith("pymatgen."):
            raise ModuleNotFoundError("synthetic missing pymatgen")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_symmetry_dependencies)

    with pytest.raises(RuntimeError, match="pymatgen.*spglib"):
        handler.find_sym_reduced_stackings()
