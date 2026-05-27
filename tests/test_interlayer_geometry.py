from pathlib import Path

import numpy as np
import pytest
from ase import Atoms
from ase.io.vasp import write_vasp

from dpmoire_lite._find_homo_twist import adjust_atoms_d
from dpmoire_lite.structures import StructureHandler


REPO_ROOT = Path(__file__).resolve().parents[1]
MOTE2_EXAMPLE_INPUT = REPO_ROOT / "example" / "input"

PTO3_TOP_POSCAR = """null
  1.0
             4.812420000000             0.000000000000             0.000000000000
            -2.452125467093             4.140829258740             0.000000000000
             0.000000000000             0.000000000000            20.000000000000
  Pt  O
  2  6
Direct
    0.32356765174356     0.63968901760465     0.50000000000000
    0.64006871189284     0.32337570861795     0.50000000000000
    0.60172189548365     0.60136494885827     0.55449759481050
    0.36191446815267     0.36169977736434     0.44550240518965
    0.35086580844335     0.00481613232292     0.55173913461950
    0.61277055519304     0.95824859389986     0.44826086538050
    0.00481899098999     0.35065767181543     0.55173913461950
    0.95881737264624     0.61240705440717     0.44826086538050
"""

PTO3_BOT_POSCAR = """null
  1.0
             4.812419909773             0.000000000000             0.000000000000
            -2.406209954886             4.167677895541             0.000000000000
             0.000000000000             0.000000000000            25.000000000000
  Pt
  12
Direct
 -0.0000000000000607   0.3333333333332534   0.6147631215056000
  0.3333333333332433   1.0000000000000000   0.6147631215056000
  0.6666666666665680   0.6666666666667466   0.6147631215056000
 -0.0000000000000607   0.3333333333332534   0.3425315413361200
  0.3333333333332433   1.0000000000000000   0.3425315413361200
  0.6666666666665680   0.6666666666667466   0.3425315413361200
  0.0000000000000400   0.6666666666667466   0.5240192614492000
  0.3333333333332449   0.3333333333332534   0.5240192614492000
  0.6666666666665489   1.0000000000000000   0.5240192614492000
  0.0000000000000000   1.0000000000000000   0.4332754013928000
  0.3333333333333033   0.6666666666667466   0.4332754013928000
  0.6666666666664881   0.3333333333332534   0.4332754013928000
"""


def _write_pto3_on_pt_inputs(root: Path) -> Path:
    input_dir = root / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "top_layer.poscar").write_text(PTO3_TOP_POSCAR, encoding="utf-8")
    (input_dir / "bot_layer.poscar").write_text(PTO3_BOT_POSCAR, encoding="utf-8")
    return input_dir


def _write_tilted_cell_inputs(root: Path) -> Path:
    input_dir = root / "input"
    input_dir.mkdir(parents=True)
    top = Atoms(
        "H2",
        positions=[
            [0.0, 0.0, 9.0],
            [1.0, 0.0, 10.0],
        ],
        cell=[[4.0, 0.0, 0.2], [0.0, 4.0, 0.0], [0.0, 0.0, 20.0]],
        pbc=True,
    )
    bot = Atoms(
        "H2",
        positions=[
            [0.0, 0.0, 6.0],
            [1.0, 0.0, 7.0],
        ],
        cell=[[4.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 20.0]],
        pbc=True,
    )

    write_vasp(input_dir / "top_layer.poscar", top, direct=True)
    write_vasp(input_dir / "bot_layer.poscar", bot, direct=True)
    return input_dir


def _bounds(atoms: Atoms, indexes: list[int]) -> tuple[float, float]:
    z = atoms.positions[indexes, 2]
    return float(z.min()), float(z.max())


def _surface_gap(atoms: Atoms, top_indexes: list[int], bot_indexes: list[int]) -> float:
    top_min, _ = _bounds(atoms, top_indexes)
    _, bot_max = _bounds(atoms, bot_indexes)
    return top_min - bot_max


def _reference_gap(atoms: Atoms, top_indexes: list[int], bot_indexes: list[int], symbol: str = "Pt") -> float:
    symbols = atoms.get_chemical_symbols()
    top_ref = [idx for idx in top_indexes if symbols[idx] == symbol]
    bot_ref = [idx for idx in bot_indexes if symbols[idx] == symbol]
    return float(atoms.positions[top_ref, 2].mean() - atoms.positions[bot_ref, 2].mean())


def _thickness(atoms: Atoms, indexes: list[int]) -> float:
    z_min, z_max = _bounds(atoms, indexes)
    return z_max - z_min


def _handler(
    input_dir: Path,
    tmp_path: Path,
    *,
    d: float,
    d_mode: str = "surface_gap",
    d_reference: dict[str, str | tuple[str, ...]] | None = None,
) -> StructureHandler:
    return StructureHandler(input_dir, tmp_path / f"work-{d}-{d_mode}", (1, 1), d, d_mode, d_reference)


def test_surface_gap_mode_uses_boundary_gap_for_thick_layers(tmp_path):
    input_dir = _write_pto3_on_pt_inputs(tmp_path)
    handler = _handler(input_dir, tmp_path, d=7.3, d_mode="surface_gap")

    assert _surface_gap(handler.new_struct, handler.top_indexes, handler.bot_indexes) == pytest.approx(7.3)
    assert _thickness(handler.new_struct, handler.top_indexes) == pytest.approx(
        _thickness(handler.top_atoms, list(range(len(handler.top_atoms))))
    )
    assert _thickness(handler.new_struct, handler.bot_indexes) == pytest.approx(
        _thickness(handler.bot_atoms, list(range(len(handler.bot_atoms))))
    )


def test_surface_gap_mode_ignores_reference_selectors(tmp_path):
    input_dir = _write_pto3_on_pt_inputs(tmp_path)
    handler = _handler(
        input_dir,
        tmp_path,
        d=7.3,
        d_mode="surface_gap",
        d_reference={"top": ("Xx",), "bot": ("Pt",)},
    )

    assert _surface_gap(handler.new_struct, handler.top_indexes, handler.bot_indexes) == pytest.approx(7.3)


def test_reference_plane_gap_mode_uses_mote2_metal_plane_from_example(tmp_path):
    handler = _handler(
        MOTE2_EXAMPLE_INPUT,
        tmp_path,
        d=7.3,
        d_mode="reference_plane_gap",
        d_reference={"top": ("Mo",), "bot": ("Mo",)},
    )

    assert _reference_gap(handler.new_struct, handler.top_indexes, handler.bot_indexes, "Mo") == pytest.approx(7.3)
    assert _surface_gap(handler.new_struct, handler.top_indexes, handler.bot_indexes) != pytest.approx(7.3)
    assert _thickness(handler.new_struct, handler.top_indexes) == pytest.approx(
        _thickness(handler.top_atoms, list(range(len(handler.top_atoms))))
    )
    assert _thickness(handler.new_struct, handler.bot_indexes) == pytest.approx(
        _thickness(handler.bot_atoms, list(range(len(handler.bot_atoms))))
    )


def test_changing_d_rigidly_changes_only_the_selected_spacing(tmp_path):
    input_dir = _write_pto3_on_pt_inputs(tmp_path)
    handler_a = _handler(input_dir, tmp_path, d=4.0, d_mode="surface_gap")
    handler_b = _handler(input_dir, tmp_path, d=9.0, d_mode="surface_gap")

    assert _surface_gap(handler_a.new_struct, handler_a.top_indexes, handler_a.bot_indexes) == pytest.approx(4.0)
    assert _surface_gap(handler_b.new_struct, handler_b.top_indexes, handler_b.bot_indexes) == pytest.approx(9.0)
    assert _thickness(handler_a.new_struct, handler_a.top_indexes) == pytest.approx(
        _thickness(handler_b.new_struct, handler_b.top_indexes)
    )
    assert _thickness(handler_a.new_struct, handler_a.bot_indexes) == pytest.approx(
        _thickness(handler_b.new_struct, handler_b.bot_indexes)
    )


def test_layer_tags_survive_sort_and_supercell_for_constraints(tmp_path):
    input_dir = _write_pto3_on_pt_inputs(tmp_path)
    handler = _handler(input_dir, tmp_path, d=6.0)
    atoms = handler.shift_atoms(0, 0, c_constrain=True, sc=(2, 1))
    top_indexes, bot_indexes = handler.find_layer_idx(atoms)

    assert len(top_indexes) == 16
    assert len(bot_indexes) == 24
    constrained = atoms.constraints[0].index.tolist()
    assert constrained[0] in top_indexes
    assert constrained[1] in bot_indexes


def test_find_layer_idx_infers_layers_from_z_gap_when_tags_are_missing():
    handler = StructureHandler.__new__(StructureHandler)
    atoms = Atoms(
        "H4",
        positions=[
            [0.0, 0.0, 12.0],
            [1.0, 0.0, 13.0],
            [0.0, 0.0, 17.0],
            [1.0, 0.0, 18.0],
        ],
        cell=[4.0, 4.0, 20.0],
        pbc=True,
    )

    top_indexes, bot_indexes = handler.find_layer_idx(atoms)

    assert top_indexes == [2, 3]
    assert bot_indexes == [0, 1]


def test_rejects_cells_not_aligned_with_cartesian_z(tmp_path):
    input_dir = _write_tilted_cell_inputs(tmp_path)

    with pytest.raises(ValueError, match="slab cell"):
        _handler(input_dir, tmp_path, d=6.0)


def test_validation_twist_applies_surface_gap_and_preserves_thickness(tmp_path):
    input_dir = _write_pto3_on_pt_inputs(tmp_path)
    handler = _handler(input_dir, tmp_path, d=7.3, d_mode="surface_gap")

    angles, atoms_list = handler.make_twist_struct(4, 4, tmp_path / "validation")
    assert angles == ["7.34099"]
    atoms = atoms_list[0]
    top_indexes, bot_indexes = handler.find_layer_idx(atoms)

    assert _surface_gap(atoms, top_indexes, bot_indexes) == pytest.approx(7.3)
    assert _thickness(atoms, top_indexes) == pytest.approx(
        _thickness(handler.top_atoms, list(range(len(handler.top_atoms))))
    )
    assert _thickness(atoms, bot_indexes) == pytest.approx(
        _thickness(handler.bot_atoms, list(range(len(handler.bot_atoms))))
    )


def test_validation_twist_applies_reference_plane_gap(tmp_path):
    handler = _handler(
        MOTE2_EXAMPLE_INPUT,
        tmp_path,
        d=7.3,
        d_mode="reference_plane_gap",
        d_reference={"top": ("Mo",), "bot": ("Mo",)},
    )

    _angles, atoms_list = handler.make_twist_struct(4, 4, tmp_path / "validation-reference")
    atoms = atoms_list[0]
    top_indexes, bot_indexes = handler.find_layer_idx(atoms)

    assert _reference_gap(atoms, top_indexes, bot_indexes, "Mo") == pytest.approx(7.3)


def test_reference_plane_gap_reports_missing_reference_symbols(tmp_path):
    with pytest.raises(ValueError, match="top layer.*Pt.*available.*Mo.*Te"):
        _handler(
            MOTE2_EXAMPLE_INPUT,
            tmp_path,
            d=7.3,
            d_mode="reference_plane_gap",
            d_reference={"top": ("Pt",), "bot": ("Mo",)},
        )


def test_adjust_atoms_d_preserves_cells_while_setting_surface_gap():
    top = Atoms(
        "PtOH",
        positions=[
            [0.0, 0.0, 9.0],
            [1.0, 0.0, 10.0],
            [0.0, 1.0, 11.0],
        ],
        cell=[[4.0, 0.0, 0.0], [-2.0, 3.464101615, 0.0], [0.0, 0.0, 20.0]],
        pbc=True,
    )
    bot = Atoms(
        "Pt3",
        positions=[
            [0.0, 0.0, 6.0],
            [1.0, 0.0, 10.0],
            [0.0, 1.0, 16.0],
        ],
        cell=[[4.0, 0.0, 0.0], [-2.0, 3.464101615, 0.0], [0.0, 0.0, 25.0]],
        pbc=True,
    )
    top_cell = top.cell.array.copy()
    bot_cell = bot.cell.array.copy()

    adjusted_top, adjusted_bot = adjust_atoms_d(top, bot, 5.5)

    np.testing.assert_allclose(adjusted_top.cell.array, top_cell)
    np.testing.assert_allclose(adjusted_bot.cell.array, bot_cell)
    assert adjusted_top.positions[:, 2].min() - adjusted_bot.positions[:, 2].max() == pytest.approx(5.5)
