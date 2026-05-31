from __future__ import annotations

import math
import re
import shutil
from importlib import resources
from pathlib import Path
from typing import Iterable

import numpy as np
from ase import Atoms
from ase.build import make_supercell, sort
from ase.io.vasp import read_vasp, write_vasp

from .structures import supercell_matrix


VASP_POTCAR_LINK = {
    "Li": "Li_sv",
    "Na": "Na_pv",
    "K": "K_sv",
    "Ca": "Ca_sv",
    "Sc": "Sc_sv",
    "Ti": "Ti_sv",
    "V": "V_sv",
    "Cr": "Cr_pv",
    "Mn": "Mn_pv",
    "Ga": "Ga_d",
    "Ge": "Ge_d",
    "Rb": "Rb_sv",
    "Sr": "Sr_sv",
    "Y": "Y_sv",
    "Zr": "Zr_sv",
    "Nb": "Nb_sv",
    "Mo": "Mo_sv",
    "Tc": "Tc_pv",
    "Ru": "Ru_pv",
    "Rh": "Rh_pv",
    "In": "In_d",
    "Sn": "Sn_d",
    "Cs": "Cs_sv",
    "Ba": "Ba_sv",
    "Pr": "Pr_3",
    "Nd": "Nd_3",
    "Pm": "Pm_3",
    "Sm": "Sm_3",
    "Eu": "Eu_2",
    "Gd": "Gd_3",
    "Tb": "Tb_3",
    "Dy": "Dy_3",
    "Ho": "Ho_3",
    "Er": "Er_3",
    "Tm": "Tm_3",
    "Yb": "Yb_2",
    "Lu": "Lu_3",
    "Hf": "Hf_pv",
    "Ta": "Ta_pv",
    "Tl": "Tl_d",
    "Pb": "Pb_d",
    "Bi": "Bi_d",
    "Po": "Po_d",
    "W": "W_sv",
    "Fr": "Fr_sv",
    "Ra": "Ra_sv",
}


MINIMAL_VALENCE_EXCLUDED_SUFFIXES = ("_AE", "_GW", "_h", "_s")
VALID_POTCAR_POLICIES = {"recommend", "minimal"}


def needs_vdw_kernel(incar_text: str) -> bool:
    for raw_line in incar_text.splitlines():
        line = raw_line.split("#", 1)[0].split("!", 1)[0].strip()
        if not line:
            continue
        parts = line.replace("=", " = ").split()
        if not parts or parts[0].upper() != "LUSE_VDW":
            continue
        value = "".join(parts[2:] if len(parts) > 1 and parts[1] == "=" else parts[1:]).upper()
        return value in {".TRUE.", "TRUE", "T", ".T."}
    return False


def resolve_potcar_dir(element: str, potcar_dir: Path, potcar_policy: str = "recommend") -> Path:
    potcar_dir = Path(potcar_dir)
    if potcar_policy == "minimal":
        return resolve_minimal_valence_potcar_dir(element, potcar_dir)
    if potcar_policy != "recommend":
        allowed = ", ".join(sorted(VALID_POTCAR_POLICIES))
        raise ValueError(f"potcar_policy must be one of: {allowed}")
    candidates = []
    if element in VASP_POTCAR_LINK:
        candidates.append(VASP_POTCAR_LINK[element])
    candidates.append(element)
    for candidate in candidates:
        path = potcar_dir / candidate
        if (path / "POTCAR").exists():
            return path
    raise FileNotFoundError(f"No POTCAR found for {element} in {potcar_dir}. Tried: {', '.join(candidates)}")


def resolve_minimal_valence_potcar_dir(element: str, potcar_dir: Path) -> Path:
    candidates = []
    for path in Path(potcar_dir).iterdir():
        if not path.is_dir() or not _is_regular_potcar_variant(element, path.name):
            continue
        potcar = path / "POTCAR"
        if not potcar.exists():
            continue
        candidates.append((read_zval(potcar), 0 if path.name == element else 1, path.name, path))
    if not candidates:
        raise FileNotFoundError(f"No regular POTCAR candidates found for {element} in {potcar_dir}")
    return sorted(candidates)[0][3]


def _is_regular_potcar_variant(element: str, name: str) -> bool:
    if name != element and not name.startswith(f"{element}_"):
        return False
    return not name.endswith(MINIMAL_VALENCE_EXCLUDED_SUFFIXES)


def read_enmax(potcar_file: Path) -> float:
    text = Path(potcar_file).read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"\bENMAX\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*;", text)
    if match is None:
        raise ValueError(f"ENMAX not found in {potcar_file}")
    return float(match.group(1))


def read_zval(potcar_file: Path) -> float:
    text = Path(potcar_file).read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"\bZVAL\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\b", text)
    if match is None:
        raise ValueError(f"ZVAL not found in {potcar_file}")
    return float(match.group(1))


def replace_incar_values(incar_text: str, encut: float, rcut1: float, rcut2: float, elements: list[str]) -> str:
    output = []
    for line in incar_text.splitlines():
        words = line.split()
        if not words:
            output.append(line)
            continue
        key = words[0].upper()
        if key == "ENCUT":
            output.append(f"ENCUT = {encut}")
        elif key == "ML_RCUT1":
            output.append(f"ML_RCUT1 = {rcut1}")
        elif key == "ML_RCUT2":
            output.append(f"ML_RCUT2 = {rcut2}")
        elif key == "LANGEVIN_GAMMA":
            output.append("LANGEVIN_GAMMA = " + " ".join(["1"] * len(elements)))
        else:
            output.append(line)
    return "\n".join(output) + "\n"


def get_ordered_elements(atoms: Atoms) -> list[str]:
    elements = []
    previous = None
    for symbol in atoms.get_chemical_symbols():
        if symbol != previous:
            elements.append(symbol)
        previous = symbol
    return elements


def write_potcar(
    elements: Iterable[str],
    potcar_dir: Path,
    output_file: Path,
    potcar_policy: str = "recommend",
) -> float:
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    max_enmax = 0.0
    with output_file.open("wb") as output:
        for element in elements:
            source = resolve_potcar_dir(element, Path(potcar_dir), potcar_policy=potcar_policy) / "POTCAR"
            max_enmax = max(max_enmax, read_enmax(source))
            output.write(source.read_bytes())
    return max_enmax


def write_kpoints(output_dir: Path, lat_vec, k_mesh: int, k_scale: tuple[int, int] | None = None) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    lat_vec = np.asarray(lat_vec, dtype=float)
    lengths = np.linalg.norm(lat_vec[:2], axis=1)
    kx = max(1, math.ceil(k_mesh / lengths[0]))
    ky = max(1, math.ceil(k_mesh / lengths[1]))
    (output_dir / "KPOINTS").write_text(
        "\n".join(["Automatic mesh", "0", "Gamma", f"{kx} {ky} 1", "0 0 0"]) + "\n",
        encoding="utf-8",
    )


def render_incar(template_file: Path, output_file: Path, encut: float, rcut1: float, rcut2: float, elements: list[str]) -> None:
    text = Path(template_file).read_text(encoding="utf-8")
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(replace_incar_values(text, encut, rcut1, rcut2, elements), encoding="utf-8")


def copy_vdw_if_needed(template_file: Path, input_dir: Path, output_dir: Path) -> None:
    text = Path(template_file).read_text(encoding="utf-8")
    if not needs_vdw_kernel(text):
        return
    source = Path(input_dir) / "vdw_kernel.bindat"
    if not source.exists():
        raise FileNotFoundError(f"vdw_kernel.bindat required by {template_file} but not found in {input_dir}")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output_dir / "vdw_kernel.bindat")


def copy_submit_script(script_dir: Path, dft_script: str, output_dir: Path) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(script_dir) / dft_script, output_dir / dft_script)


def write_supercell_poscar(input_file: Path, output_file: Path, sc: tuple[int, int]) -> None:
    atoms = read_vasp(input_file)
    atoms_sc = sort(make_supercell(prim=atoms, P=supercell_matrix(sc)))
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    write_vasp(output_file, atoms=atoms_sc)


def stage_mlff_files(init_dir: Path, output_dir: Path) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(init_dir) / "ML_ABN", output_dir / "ML_AB")
    shutil.copy2(Path(init_dir) / "ML_FFN", output_dir / "ML_FF")


def copy_example(target_dir: Path) -> None:
    target_dir = Path(target_dir)
    if target_dir.exists():
        raise FileExistsError(f"Target example directory already exists: {target_dir}")
    source = resources.files("dpmoire_lite") / "example"
    if not source.is_dir():
        raise FileNotFoundError("Bundled example template not found in dpmoire_lite package data")

    target_dir.mkdir(parents=True)
    for item in source.iterdir():
        if item.name == "__init__.py":
            continue
        _copy_traversable(item, target_dir / item.name)


def _copy_traversable(source, target: Path) -> None:
    if source.is_dir():
        target.mkdir()
        for item in source.iterdir():
            _copy_traversable(item, target / item.name)
        return
    target.write_bytes(source.read_bytes())
