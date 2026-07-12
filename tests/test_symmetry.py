import builtins

from ase import Atoms
from ase.io.vasp import write_vasp
import pytest

import dpmoire_lite.build as build_module
from dpmoire_lite.build import run_build
from dpmoire_lite.manifest import read_manifest
from dpmoire_lite.structures import StructureHandler

from test_build import write_build_config


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
    handler.write_sym_reduced_stackings(second)

    assert first == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert second == first
    assert len(first) == len(set(first))
    assert read_written_stackings(handler) == second


def test_find_sym_reduced_stackings_supports_rectangular_sectors(tmp_path):
    handler = make_handler(tmp_path, (3, 2))

    stackings = handler.find_sym_reduced_stackings()
    handler.write_sym_reduced_stackings(stackings)

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


def test_symmetry_selection_failure_precedes_workdir_creation(monkeypatch, tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        init_mlff=False,
        do_relaxation=True,
        twist_val=False,
        symm_reduce=True,
        n_sectors=[1, 1],
        vasp_ml=False,
    )
    work = tmp_path / "work"

    def fail_selection(_self):
        raise RuntimeError("synthetic symmetry selection failure")

    monkeypatch.setattr(
        build_module.StructureHandler,
        "find_sym_reduced_stackings",
        fail_selection,
    )

    with pytest.raises(RuntimeError, match="synthetic symmetry selection failure"):
        run_build(config, wait=False)

    assert not work.exists()


def test_twist_validation_selection_is_prepared_without_a_temporary_workdir(
    monkeypatch, tmp_path
):
    config = write_build_config(
        tmp_path,
        stage=0,
        init_mlff=False,
        do_relaxation=False,
        twist_val=True,
        symm_reduce=False,
        n_sectors=[1, 1],
        vasp_ml=False,
    )
    work = tmp_path / "work"
    observed_extra_args = []

    def prepare_twist(self, _n_min, _n_max, *extra_args):
        observed_extra_args.append(extra_args)
        assert extra_args == ()
        assert not work.exists()
        return ["synthetic-angle"], [self.new_struct.copy()]

    monkeypatch.setattr(
        build_module.StructureHandler,
        "make_twist_struct",
        prepare_twist,
    )

    run_build(config, wait=False)

    assert observed_extra_args == [()]
    assert (work / "validation" / "synthetic-angle" / "POSCAR").is_file()


def test_symmetry_auxiliary_file_written_only_after_preflight(monkeypatch, tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        init_mlff=False,
        do_relaxation=True,
        twist_val=False,
        symm_reduce=True,
        n_sectors=[1, 1],
        vasp_ml=False,
    )
    artifact = tmp_path / "work" / "sym_reduced_stackings.txt"
    preflight_called = False
    real_preflight = build_module.preflight_stage0

    def observe_preflight(config):
        nonlocal preflight_called
        preflight_called = True
        assert not artifact.exists()
        return real_preflight(config)

    monkeypatch.setattr(build_module, "preflight_stage0", observe_preflight)
    monkeypatch.setattr(
        build_module.StructureHandler,
        "find_sym_reduced_stackings",
        lambda self: [(0, 0)],
    )

    run_build(config, wait=False)

    assert preflight_called
    assert artifact.is_file()
    assert [tuple(map(int, artifact.read_text(encoding="utf-8").split()))] == [(0, 0)]


def test_manifest_stackings_equal_generated_and_auxiliary_stackings(monkeypatch, tmp_path):
    config = write_build_config(
        tmp_path,
        stage=0,
        init_mlff=False,
        do_relaxation=True,
        twist_val=False,
        symm_reduce=True,
        n_sectors=[2, 1],
        vasp_ml=False,
    )
    selected = [(0, 0), (1, 0)]
    monkeypatch.setattr(
        build_module.StructureHandler,
        "find_sym_reduced_stackings",
        lambda self: selected,
    )

    run_build(config, wait=False)

    work = tmp_path / "work"
    manifest = read_manifest(work, "rlx").manifest
    assert manifest is not None
    assert manifest.stackings == [list(stacking) for stacking in selected]
    auxiliary = [
        tuple(int(value) for value in line.split())
        for line in (work / "sym_reduced_stackings.txt").read_text(encoding="utf-8").splitlines()
    ]
    assert auxiliary == selected
