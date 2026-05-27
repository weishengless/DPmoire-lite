# Interlayer Spacing Modes And Validation Geometry Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two explicit `d` interpretations, `surface_gap` and `reference_plane_gap`, and make both normal stacking and validation/twist preserve layer geometry.

**Architecture:** Extend config parsing with `d_mode` and optional `d_reference`, add shared layer-placement helpers in `src/dpmoire_lite/structures.py`, and route both normal stacking and validation/twist through the same spacing logic. Keep `d` as the numeric value and keep the command-line workflow unchanged.

**Tech Stack:** Python 3.10+, ASE `Atoms`, ASE `make_supercell`, ASE `sort`, ASE `stack`, `numpy`, `pyyaml`, `pytest`.

---

## Scope Check

This is one coherent geometry/config fix. The config change is necessary because `d` has two valid physical meanings, and the validation/twist fix is necessary because it currently mutates layer cells before applying twist supercells.

## File Structure

Modify these files:

- `src/dpmoire_lite/config.py`: add `d_mode`, optional `d_reference`, validation, and defaults.
- `src/dpmoire_lite/structures.py`: add layer tags, reference selectors, and shared spacing helpers; update stacking and validation construction.
- `src/dpmoire_lite/_find_homo_twist.py`: make legacy `adjust_atoms_d()` cell-preserving.
- `src/dpmoire_lite/build.py`: pass config spacing options into `StructureHandler`.
- `tests/test_config.py`: add config parsing tests for the new fields.
- `tests/test_interlayer_geometry.py`: add focused regression tests for both spacing modes, tags, and validation geometry.
- `README.md`: document `d`, `d_mode`, and `d_reference`.
- `README_CH.md`: document `d`, `d_mode`, and `d_reference`.
- `example/config.yaml`: add `d_mode` and commented `d_reference`.
- `src/dpmoire_lite/example/config.yaml`: add `d_mode` and commented `d_reference`.

---

### Task 1: Add Failing Config Tests

**Files:**
- Modify: `tests/test_config.py`

- [ ] **Step 1: Add tests for `d_mode` and `d_reference`**

Append these tests to `tests/test_config.py`:

```python
def test_d_mode_defaults_to_surface_gap(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(config_file)

    config = load_config(config_file)

    assert config.d_mode == "surface_gap"
    assert config.d_reference is None


@pytest.mark.parametrize("mode", ["surface_gap", "reference_plane_gap"])
def test_load_config_accepts_valid_d_modes(tmp_path, mode):
    config_file = tmp_path / "config.yaml"
    write_config(config_file, d_mode=mode)

    config = load_config(config_file)

    assert config.d_mode == mode


def test_load_config_accepts_reference_elements(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(
        config_file,
        d_mode="reference_plane_gap",
        d_reference={"top": ["Pt"], "bot": ["Pt", "Mo"]},
    )

    config = load_config(config_file)

    assert config.d_reference == {"top": ("Pt",), "bot": ("Pt", "Mo")}


def test_load_config_accepts_all_reference_keyword(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(
        config_file,
        d_mode="reference_plane_gap",
        d_reference={"top": "all", "bot": "all"},
    )

    config = load_config(config_file)

    assert config.d_reference == {"top": "all", "bot": "all"}


def test_load_config_rejects_invalid_d_mode(tmp_path):
    config_file = tmp_path / "config.yaml"
    write_config(config_file, d_mode="center_distance")

    with pytest.raises(ConfigError, match="d_mode"):
        load_config(config_file)
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py -v
```

Expected: FAIL because `DPmoireLiteConfig` has no `d_mode` or `d_reference` fields yet.

- [ ] **Step 3: Commit failing config tests**

Run:

```powershell
git add tests\test_config.py
git commit -m "test: cover d spacing mode config"
```

Expected: commit succeeds.

---

### Task 2: Implement Config Fields

**Files:**
- Modify: `src/dpmoire_lite/config.py`

- [ ] **Step 1: Add spacing mode constants near the other config constants**

Add:

```python
VALID_D_MODES = {"surface_gap", "reference_plane_gap"}
```

- [ ] **Step 2: Add normalizer helpers**

Add these helpers near the existing normalization helpers:

```python
def normalize_d_mode(value: Any) -> str:
    mode = str(value).strip()
    if mode not in VALID_D_MODES:
        allowed = ", ".join(sorted(VALID_D_MODES))
        raise ConfigError(f"d_mode must be one of: {allowed}")
    return mode


def normalize_reference_selector(value: Any, field: str) -> str | tuple[str, ...]:
    if value is None:
        return "all"
    if isinstance(value, str):
        text = value.strip()
        if text == "all":
            return "all"
        if not text:
            raise ConfigError(f"{field} cannot be empty")
        return (text,)
    if isinstance(value, (list, tuple)):
        items = tuple(str(item).strip() for item in value)
        if not items or any(not item for item in items):
            raise ConfigError(f"{field} must contain at least one non-empty element symbol")
        return items
    raise ConfigError(f"{field} must be 'all', an element symbol, or a list of element symbols")


def normalize_d_reference(value: Any) -> dict[str, str | tuple[str, ...]] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError("d_reference must be a mapping with optional top and bot keys")
    allowed = {"top", "bot"}
    extra = sorted(set(value) - allowed)
    if extra:
        raise ConfigError(f"d_reference has unknown keys: {', '.join(extra)}")
    return {
        "top": normalize_reference_selector(value.get("top"), "d_reference.top"),
        "bot": normalize_reference_selector(value.get("bot"), "d_reference.bot"),
    }
```

- [ ] **Step 3: Add fields to `DPmoireLiteConfig`**

Add these dataclass fields immediately after `d: float`:

```python
d_mode: str
d_reference: dict[str, str | tuple[str, ...]] | None
```

- [ ] **Step 4: Parse the fields in `load_config()`**

Where `DPmoireLiteConfig(...)` is constructed, set:

```python
d=float(_require(raw, "d")),
d_mode=normalize_d_mode(raw.get("d_mode", "surface_gap")),
d_reference=normalize_d_reference(raw.get("d_reference")),
```

Keep all existing fields unchanged.

- [ ] **Step 5: Run config tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit config implementation**

Run:

```powershell
git add src\dpmoire_lite\config.py
git commit -m "feat: add d spacing mode config"
```

Expected: commit succeeds.

---

### Task 3: Add Failing Geometry Regression Tests

**Files:**
- Create: `tests/test_interlayer_geometry.py`

- [ ] **Step 1: Create focused geometry tests**

Create `tests/test_interlayer_geometry.py`:

```python
from pathlib import Path

import numpy as np
import pytest
from ase import Atoms
from ase.io.vasp import write_vasp

from dpmoire_lite._find_homo_twist import adjust_atoms_d
from dpmoire_lite.structures import StructureHandler


def _write_thick_layer_inputs(root: Path) -> Path:
    input_dir = root / "input"
    input_dir.mkdir(parents=True)

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

    write_vasp(input_dir / "top_layer.poscar", top, direct=True)
    write_vasp(input_dir / "bot_layer.poscar", bot, direct=True)
    return input_dir


def _bounds(atoms, indexes):
    z = atoms.positions[list(indexes), 2]
    return float(z.min()), float(z.max())


def _surface_gap(atoms, top_indexes, bot_indexes):
    top_min, _ = _bounds(atoms, top_indexes)
    _, bot_max = _bounds(atoms, bot_indexes)
    return top_min - bot_max


def _reference_gap(atoms, top_indexes, bot_indexes, symbol="Pt"):
    symbols = atoms.get_chemical_symbols()
    top_ref = [idx for idx in top_indexes if symbols[idx] == symbol]
    bot_ref = [idx for idx in bot_indexes if symbols[idx] == symbol]
    return float(atoms.positions[top_ref, 2].mean() - atoms.positions[bot_ref, 2].mean())


def _thickness(atoms, indexes):
    z_min, z_max = _bounds(atoms, indexes)
    return z_max - z_min


def _handler(input_dir, tmp_path, *, d, d_mode="surface_gap", d_reference=None):
    return StructureHandler(input_dir, tmp_path / f"work-{d}-{d_mode}", (1, 1), d, d_mode, d_reference)


def test_surface_gap_mode_uses_boundary_gap_for_thick_layers(tmp_path):
    input_dir = _write_thick_layer_inputs(tmp_path)
    handler = _handler(input_dir, tmp_path, d=7.3, d_mode="surface_gap")

    assert _surface_gap(handler.new_struct, handler.top_indexes, handler.bot_indexes) == pytest.approx(7.3)
    assert _thickness(handler.new_struct, handler.top_indexes) == pytest.approx(2.0)
    assert _thickness(handler.new_struct, handler.bot_indexes) == pytest.approx(10.0)


def test_reference_plane_gap_mode_uses_selected_reference_atoms(tmp_path):
    input_dir = _write_thick_layer_inputs(tmp_path)
    handler = _handler(
        input_dir,
        tmp_path,
        d=7.3,
        d_mode="reference_plane_gap",
        d_reference={"top": ("Pt",), "bot": ("Pt",)},
    )

    assert _reference_gap(handler.new_struct, handler.top_indexes, handler.bot_indexes, "Pt") == pytest.approx(7.3)
    assert _thickness(handler.new_struct, handler.top_indexes) == pytest.approx(2.0)
    assert _thickness(handler.new_struct, handler.bot_indexes) == pytest.approx(10.0)


def test_changing_d_rigidly_changes_only_the_selected_spacing(tmp_path):
    input_dir = _write_thick_layer_inputs(tmp_path)
    handler_a = _handler(input_dir, tmp_path, d=4.0, d_mode="surface_gap")
    handler_b = _handler(input_dir, tmp_path, d=9.0, d_mode="surface_gap")

    assert _surface_gap(handler_a.new_struct, handler_a.top_indexes, handler_a.bot_indexes) == pytest.approx(4.0)
    assert _surface_gap(handler_b.new_struct, handler_b.top_indexes, handler_b.bot_indexes) == pytest.approx(9.0)
    assert _thickness(handler_a.new_struct, handler_a.top_indexes) == pytest.approx(_thickness(handler_b.new_struct, handler_b.top_indexes))
    assert _thickness(handler_a.new_struct, handler_a.bot_indexes) == pytest.approx(_thickness(handler_b.new_struct, handler_b.bot_indexes))


def test_layer_tags_survive_sort_and_supercell_for_constraints(tmp_path):
    input_dir = _write_thick_layer_inputs(tmp_path)
    handler = _handler(input_dir, tmp_path, d=6.0)
    atoms = handler.shift_atoms(0, 0, c_constrain=True, sc=(2, 1))
    top_indexes, bot_indexes = handler.find_layer_idx(atoms)

    assert len(top_indexes) == 6
    assert len(bot_indexes) == 6
    constrained = atoms.constraints[0].index.tolist()
    assert constrained[0] in top_indexes
    assert constrained[1] in bot_indexes


def test_validation_twist_applies_surface_gap_and_preserves_thickness(tmp_path):
    input_dir = _write_thick_layer_inputs(tmp_path)
    handler = _handler(input_dir, tmp_path, d=7.3, d_mode="surface_gap")

    angles, atoms_list = handler.make_twist_struct(4, 4, tmp_path / "validation")
    assert angles == ["7.34099"]
    atoms = atoms_list[0]
    top_indexes, bot_indexes = handler.find_layer_idx(atoms)

    assert _surface_gap(atoms, top_indexes, bot_indexes) == pytest.approx(7.3)
    assert _thickness(atoms, top_indexes) == pytest.approx(2.0)
    assert _thickness(atoms, bot_indexes) == pytest.approx(10.0)


def test_validation_twist_applies_reference_plane_gap(tmp_path):
    input_dir = _write_thick_layer_inputs(tmp_path)
    handler = _handler(
        input_dir,
        tmp_path,
        d=7.3,
        d_mode="reference_plane_gap",
        d_reference={"top": ("Pt",), "bot": ("Pt",)},
    )

    _angles, atoms_list = handler.make_twist_struct(4, 4, tmp_path / "validation-reference")
    atoms = atoms_list[0]
    top_indexes, bot_indexes = handler.find_layer_idx(atoms)

    assert _reference_gap(atoms, top_indexes, bot_indexes, "Pt") == pytest.approx(7.3)


def test_adjust_atoms_d_preserves_cells(tmp_path):
    input_dir = _write_thick_layer_inputs(tmp_path)
    handler = _handler(input_dir, tmp_path, d=7.3)
    top = handler.top_atoms.copy()
    bot = handler.bot_atoms.copy()
    top_cell = top.cell.array.copy()
    bot_cell = bot.cell.array.copy()

    adjusted_top, adjusted_bot = adjust_atoms_d(top, bot, 5.5)

    np.testing.assert_allclose(adjusted_top.cell.array, top_cell)
    np.testing.assert_allclose(adjusted_bot.cell.array, bot_cell)
    assert adjusted_top.positions[:, 2].min() - adjusted_bot.positions[:, 2].max() == pytest.approx(5.5)
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_interlayer_geometry.py -v
```

Expected: FAIL because `StructureHandler` does not accept `d_mode` or `d_reference`, and the legacy helper mutates cells.

- [ ] **Step 3: Commit failing geometry tests**

Run:

```powershell
git add tests\test_interlayer_geometry.py
git commit -m "test: reproduce spacing mode geometry bugs"
```

Expected: commit succeeds.

---

### Task 4: Add Layer Tags And Spacing Helpers

**Files:**
- Modify: `src/dpmoire_lite/structures.py`

- [ ] **Step 1: Add tag constants and helper functions after `supercell_matrix()`**

Insert:

```python
TOP_LAYER_TAG = 1
BOT_LAYER_TAG = 2


def layer_indices_from_tags(atoms: Atoms) -> tuple[list[int], list[int]] | None:
    tags = atoms.get_tags()
    top_idx = [idx for idx, tag in enumerate(tags) if tag == TOP_LAYER_TAG]
    bot_idx = [idx for idx, tag in enumerate(tags) if tag == BOT_LAYER_TAG]
    if top_idx and bot_idx and len(top_idx) + len(bot_idx) == len(atoms):
        return top_idx, bot_idx
    return None


def z_bounds(atoms: Atoms, indexes: list[int]) -> tuple[float, float]:
    z = atoms.positions[indexes, 2]
    return float(z.min()), float(z.max())


def z_thickness(atoms: Atoms, indexes: list[int]) -> float:
    z_min, z_max = z_bounds(atoms, indexes)
    return z_max - z_min


def reference_indexes(atoms: Atoms, indexes: list[int], selector: str | tuple[str, ...]) -> list[int]:
    if selector == "all":
        return list(indexes)
    allowed = set(selector)
    symbols = atoms.get_chemical_symbols()
    selected = [idx for idx in indexes if symbols[idx] in allowed]
    if not selected:
        raise ValueError(f"No atoms matched d_reference selector: {sorted(allowed)}")
    return selected


def mean_z(atoms: Atoms, indexes: list[int]) -> float:
    return float(atoms.positions[indexes, 2].mean())


def translate_indexes_z(atoms: Atoms, indexes: list[int], delta_z: float) -> None:
    positions = atoms.get_positions()
    positions[indexes, 2] += delta_z
    atoms.set_positions(positions)


def ensure_cell_contains_z_range(atoms: Atoms, indexes: list[int], vacuum_budget: float) -> None:
    z = atoms.positions[indexes, 2]
    z_min = float(z.min())
    z_max = float(z.max())
    current_c = float(np.linalg.norm(atoms.cell.array[2]))
    target_c = max(current_c, z_max - z_min + vacuum_budget)
    if target_c > current_c:
        new_cell = atoms.cell.array.copy()
        new_cell[2] *= target_c / current_c
        atoms.set_cell(new_cell, scale_atoms=False)
```

- [ ] **Step 2: Add `apply_interlayer_spacing()` helper**

Insert after the helpers above:

```python
def apply_interlayer_spacing(
    atoms: Atoms,
    top_idx: list[int],
    bot_idx: list[int],
    d: float,
    d_mode: str,
    d_reference: dict[str, str | tuple[str, ...]] | None,
) -> None:
    top_height = z_thickness(atoms, top_idx)
    bot_height = z_thickness(atoms, bot_idx)
    current_c = float(np.linalg.norm(atoms.cell.array[2]))
    vacuum_budget = max(current_c - top_height - bot_height, 0.0)

    top_selector = (d_reference or {}).get("top", "all")
    bot_selector = (d_reference or {}).get("bot", "all")
    top_ref = reference_indexes(atoms, top_idx, top_selector)
    bot_ref = reference_indexes(atoms, bot_idx, bot_selector)

    center_z = current_c / 2
    if d_mode == "surface_gap":
        top_anchor, _ = z_bounds(atoms, top_idx)
        _, bot_anchor = z_bounds(atoms, bot_idx)
    elif d_mode == "reference_plane_gap":
        top_anchor = mean_z(atoms, top_ref)
        bot_anchor = mean_z(atoms, bot_ref)
    else:
        raise ValueError(f"Unsupported d_mode: {d_mode}")

    translate_indexes_z(atoms, bot_idx, center_z - d / 2 - bot_anchor)
    translate_indexes_z(atoms, top_idx, center_z + d / 2 - top_anchor)
    ensure_cell_contains_z_range(atoms, top_idx + bot_idx, vacuum_budget)
```

- [ ] **Step 3: Run import check**

Run:

```powershell
.\.venv\Scripts\python.exe -m py_compile src\dpmoire_lite\structures.py
```

Expected: PASS with no output.

- [ ] **Step 4: Commit helpers**

Run:

```powershell
git add src\dpmoire_lite\structures.py
git commit -m "fix: add spacing mode geometry helpers"
```

Expected: commit succeeds.

---

### Task 5: Wire Spacing Modes Into StructureHandler

**Files:**
- Modify: `src/dpmoire_lite/structures.py`
- Modify: `src/dpmoire_lite/build.py`

- [ ] **Step 1: Extend `StructureHandler.__init__()`**

Change the signature and assignments to:

```python
def __init__(
    self,
    input_dir: Path,
    work_dir: Path,
    n_sectors: tuple[int, int],
    d: float,
    d_mode: str = "surface_gap",
    d_reference: dict[str, str | tuple[str, ...]] | None = None,
):
    self.input_dir = Path(input_dir)
    self.work_dir = Path(work_dir)
    self.n_sectors = n_sectors
    self.d = d
    self.d_mode = d_mode
    self.d_reference = d_reference
```

Keep the existing remaining initialization lines after these assignments.

- [ ] **Step 2: Update `find_layer_idx()` to prefer tags**

Replace `find_layer_idx()` with:

```python
def find_layer_idx(self, atoms: Atoms) -> tuple[list[int], list[int]]:
    tagged = layer_indices_from_tags(atoms)
    if tagged is not None:
        return tagged

    cell_mat = atoms.get_cell().array
    frac_mat = np.linalg.inv(cell_mat)
    frac_pos = np.dot(atoms.get_positions(), frac_mat)
    top_idx = []
    bot_idx = []
    for idx, pos in enumerate(frac_pos):
        if pos[2] > 0.5:
            top_idx.append(idx)
        else:
            bot_idx.append(idx)
    return top_idx, bot_idx
```

- [ ] **Step 3: Replace placement in `build_new_struct()`**

Replace the body of `build_new_struct()` with:

```python
def build_new_struct(self, d: float) -> tuple[Atoms, list[int], list[int]]:
    top_cell_mat = self.top_atoms.get_cell().array
    top_cell_len = self.top_atoms.get_cell().lengths()
    bot_cell_mat = self.bot_atoms.get_cell().array
    bot_cell_len = self.bot_atoms.get_cell().lengths()
    fractional_pos_top = np.dot(self.top_atoms.get_positions(), np.linalg.inv(top_cell_mat))
    fractional_pos_bot = np.dot(self.bot_atoms.get_positions(), np.linalg.inv(bot_cell_mat))
    new_cell_mat = np.array([top_cell_mat[k] * (1 + bot_cell_len[k] / top_cell_len[k]) / 2 for k in range(3)])

    top_atoms = Atoms(
        positions=np.dot(fractional_pos_top, new_cell_mat),
        symbols=self.top_atoms.get_chemical_symbols(),
        cell=new_cell_mat,
        pbc=[True, True, True],
    )
    bot_atoms = Atoms(
        positions=np.dot(fractional_pos_bot, new_cell_mat),
        symbols=self.bot_atoms.get_chemical_symbols(),
        cell=new_cell_mat,
        pbc=[True, True, True],
    )
    top_atoms.set_tags([TOP_LAYER_TAG] * len(top_atoms))
    bot_atoms.set_tags([BOT_LAYER_TAG] * len(bot_atoms))

    atoms = top_atoms + bot_atoms
    top_idx = list(range(len(top_atoms)))
    bot_idx = list(range(len(top_atoms), len(atoms)))
    apply_interlayer_spacing(atoms, top_idx, bot_idx, d, self.d_mode, self.d_reference)

    atoms = sort(atoms)
    top_idx, bot_idx = self.find_layer_idx(atoms)
    return atoms, top_idx, bot_idx
```

- [ ] **Step 4: Pass spacing config from build stages**

In `src/dpmoire_lite/build.py`, replace every `StructureHandler(config.input_dir, config.work_dir, config.n_sectors, config.d)` call with:

```python
StructureHandler(
    config.input_dir,
    config.work_dir,
    config.n_sectors,
    config.d,
    config.d_mode,
    config.d_reference,
)
```

- [ ] **Step 5: Include spacing mode in generated manifests**

In `src/dpmoire_lite/build.py`, update `_config_summary()` so generated manifests record the selected spacing semantics:

```python
def _config_summary(config: DPmoireLiteConfig) -> dict[str, object]:
    d_reference = None
    if config.d_reference is not None:
        d_reference = {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in config.d_reference.items()
        }
    return {
        "stage": config.stage,
        "n_sectors": list(config.n_sectors),
        "sc": list(config.sc),
        "d": config.d,
        "d_mode": config.d_mode,
        "d_reference": d_reference,
        "k_mesh": config.k_mesh,
        "encut_factor": config.encut_factor,
        "r_cut": config.r_cut,
    }
```

- [ ] **Step 6: Run normal geometry tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_interlayer_geometry.py::test_surface_gap_mode_uses_boundary_gap_for_thick_layers tests\test_interlayer_geometry.py::test_reference_plane_gap_mode_uses_selected_reference_atoms tests\test_interlayer_geometry.py::test_changing_d_rigidly_changes_only_the_selected_spacing tests\test_interlayer_geometry.py::test_layer_tags_survive_sort_and_supercell_for_constraints -v
```

Expected: PASS.

- [ ] **Step 7: Commit structure handler wiring**

Run:

```powershell
git add src\dpmoire_lite\structures.py src\dpmoire_lite\build.py
git commit -m "fix: apply d modes to stacking geometry"
```

Expected: commit succeeds.

---

### Task 6: Fix Validation/Twist Geometry

**Files:**
- Modify: `src/dpmoire_lite/structures.py`
- Modify: `src/dpmoire_lite/_find_homo_twist.py`

- [ ] **Step 1: Update `make_twist_struct()`**

Replace `make_twist_struct()` in `src/dpmoire_lite/structures.py` with:

```python
def make_twist_struct(self, N_min: int, N_max: int, out_dir: Path | str):
    from ase.build import stack

    from ._find_homo_twist import search_twist

    angle_list, mat_list = search_twist(N_min, N_max)
    out_atoms_list = []
    base_out_dir = Path(out_dir)
    for idx, mat in enumerate(mat_list):
        top_sc = make_supercell(copy.deepcopy(self.top_atoms), P=mat[0])
        bot_sc = make_supercell(copy.deepcopy(self.bot_atoms), P=mat[1])
        out_atoms = stack(bot_sc, top_sc, maxstrain=None, reorder=False)

        bot_idx = list(range(len(bot_sc)))
        top_idx = list(range(len(bot_sc), len(out_atoms)))
        out_atoms.set_tags([BOT_LAYER_TAG] * len(bot_idx) + [TOP_LAYER_TAG] * len(top_idx))
        apply_interlayer_spacing(out_atoms, top_idx, bot_idx, self.d, self.d_mode, self.d_reference)

        out_atoms = sort(out_atoms)
        out_atoms_list.append(out_atoms)
        target_dir = base_out_dir / angle_list[idx]
        target_dir.mkdir(parents=True, exist_ok=True)
        write_vasp(target_dir / "POSCAR", out_atoms)
    return angle_list, out_atoms_list
```

- [ ] **Step 2: Rewrite legacy `adjust_atoms_d()` as cell-preserving**

Replace `adjust_atoms_d()` in `src/dpmoire_lite/_find_homo_twist.py` with:

```python
def adjust_atoms_d(top_atoms: Atoms, bot_atoms: Atoms, d: float):
    top_pos = top_atoms.get_positions()
    bot_pos = bot_atoms.get_positions()
    top_pos[:, 2] += d / 2 - float(top_pos[:, 2].min())
    bot_pos[:, 2] += -d / 2 - float(bot_pos[:, 2].max())
    top_atoms.set_positions(top_pos)
    bot_atoms.set_positions(bot_pos)
    return top_atoms, bot_atoms
```

- [ ] **Step 3: Run validation geometry tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_interlayer_geometry.py::test_validation_twist_applies_surface_gap_and_preserves_thickness tests\test_interlayer_geometry.py::test_validation_twist_applies_reference_plane_gap tests\test_interlayer_geometry.py::test_adjust_atoms_d_preserves_cells -v
```

Expected: PASS.

- [ ] **Step 4: Run stage-all validation flow test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_build.py::test_stage_all_waits_init_step2_and_rlx_before_generating_md -v
```

Expected: PASS.

- [ ] **Step 5: Commit validation fix**

Run:

```powershell
git add src\dpmoire_lite\structures.py src\dpmoire_lite\_find_homo_twist.py
git commit -m "fix: apply d modes to validation geometry"
```

Expected: commit succeeds.

---

### Task 7: Update Documentation And Examples

**Files:**
- Modify: `README.md`
- Modify: `README_CH.md`
- Modify: `example/config.yaml`
- Modify: `src/dpmoire_lite/example/config.yaml`

- [ ] **Step 1: Update README tables**

In `README.md`, document:

```markdown
| `d` | number | Distance value interpreted according to `d_mode`. |
| `d_mode` | `surface_gap` or `reference_plane_gap` | `surface_gap` sets `min_z(top) - max_z(bot) = d`; `reference_plane_gap` sets the selected reference-plane mean-z distance to `d`. Defaults to `surface_gap`. |
| `d_reference` | mapping, optional | Reference atom selectors for `reference_plane_gap`, for example `{top: [Pt], bot: [Pt]}`. Omit or use `all` to average all atoms in that layer. |
```

In `README_CH.md`, document the same meaning in Chinese:

```markdown
| `d` | 数值 | 距离数值，具体物理含义由 `d_mode` 决定。 |
| `d_mode` | `surface_gap` 或 `reference_plane_gap` | `surface_gap` 表示 `min_z(top) - max_z(bot) = d`；`reference_plane_gap` 表示所选参考原子的平均 z 坐标差为 `d`。默认值为 `surface_gap`。 |
| `d_reference` | 映射，可选 | `reference_plane_gap` 使用的参考原子选择器，例如 `{top: [Pt], bot: [Pt]}`。省略或使用 `all` 表示该层所有原子。 |
```

- [ ] **Step 2: Update both config examples**

In `example/config.yaml` and `src/dpmoire_lite/example/config.yaml`, replace the existing `d` comment block with:

```yaml
d: 7.3                  #Distance value interpreted by d_mode.
d_mode: surface_gap     #surface_gap: min_z(top)-max_z(bot); reference_plane_gap: selected mean-z distance.
#d_reference:           #Optional, only used by reference_plane_gap.
#  top: [Pt]
#  bot: [Pt]
```

- [ ] **Step 3: Commit docs**

Run:

```powershell
git add README.md README_CH.md example\config.yaml src\dpmoire_lite\example\config.yaml
git commit -m "docs: explain d spacing modes"
```

Expected: commit succeeds.

---

### Task 8: Full Verification

**Files:**
- No edits.

- [ ] **Step 1: Run focused config and geometry tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py tests\test_interlayer_geometry.py -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run build regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_build.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Run full suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Expected: all tests PASS.

- [ ] **Step 4: Run direct Pt/O/Pt reproducer checks**

Use the supplied Pt/O/Pt POSCARs in a temporary `input` directory. Expected output:

```text
surface_gap normal stacking = 7.300000
surface_gap validation = 7.300000
reference_plane_gap normal stacking = 7.300000
reference_plane_gap validation = 7.300000
top thickness preserved = true
bottom thickness preserved = true
```

- [ ] **Step 5: Inspect final git state**

Run:

```powershell
git status --short
git log --oneline -6
```

Expected: working tree is clean after commits, and latest commits correspond to config tests, config implementation, geometry tests, stacking fix, validation fix, and docs.

## Self-Review

- Spec coverage: tasks cover both spacing modes, optional element references, normal stacking, validation/twist, legacy helper behavior, docs, and verification.
- Placeholder scan: no placeholder markers or open implementation holes are present.
- Type consistency: `d_mode`, `d_reference`, `surface_gap`, and `reference_plane_gap` are used consistently across tests, config, implementation snippets, and docs.
