from ase import Atoms
from ase.io.vasp import read_vasp

from dpmoire_lite import inputs
from dpmoire_lite.inputs import (
    VASP_POTCAR_LINK,
    needs_vdw_kernel,
    replace_incar_values,
    resolve_potcar_dir,
    write_kpoints,
    write_potcar,
)
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


def test_needs_vdw_kernel_detects_nonlocal_vdw():
    assert needs_vdw_kernel("LUSE_VDW = .TRUE.\n") is True
    assert needs_vdw_kernel("GGA = PE\n") is False


def test_replace_incar_values_updates_encut_rcut_and_langevin():
    template = "ENCUT = 400\nML_RCUT1 = 1\nML_RCUT2 = 1\nLANGEVIN_GAMMA = 1\n"
    rendered = replace_incar_values(
        template,
        encut=520.0,
        rcut1=7.1,
        rcut2=7.1,
        elements=["Mo", "S", "I"],
    )
    assert "ENCUT = 520.0" in rendered
    assert "ML_RCUT1 = 7.1" in rendered
    assert "ML_RCUT2 = 7.1" in rendered
    assert "LANGEVIN_GAMMA = 1 1 1" in rendered


def test_resolve_potcar_dir_uses_strict_mapping(tmp_path):
    potcars = tmp_path / "potcars"
    (potcars / "Na_pv").mkdir(parents=True)
    (potcars / "Na_pv" / "POTCAR").write_text(" ENMAX = 200; \n", encoding="utf-8")
    assert resolve_potcar_dir("Na", potcars) == potcars / "Na_pv"


def test_recommended_potcar_link_matches_vasp_potpaw64_suffix_recommendations():
    expected = {
        "Ba": "Ba_sv",
        "Bi": "Bi_d",
        "Ca": "Ca_sv",
        "Cr": "Cr_pv",
        "Cs": "Cs_sv",
        "Dy": "Dy_3",
        "Er": "Er_3",
        "Eu": "Eu_2",
        "Fr": "Fr_sv",
        "Ga": "Ga_d",
        "Gd": "Gd_3",
        "Ge": "Ge_d",
        "Hf": "Hf_pv",
        "Ho": "Ho_3",
        "In": "In_d",
        "K": "K_sv",
        "Li": "Li_sv",
        "Lu": "Lu_3",
        "Mn": "Mn_pv",
        "Mo": "Mo_sv",
        "Na": "Na_pv",
        "Nb": "Nb_sv",
        "Nd": "Nd_3",
        "Pb": "Pb_d",
        "Pm": "Pm_3",
        "Po": "Po_d",
        "Pr": "Pr_3",
        "Ra": "Ra_sv",
        "Rb": "Rb_sv",
        "Rh": "Rh_pv",
        "Ru": "Ru_pv",
        "Sc": "Sc_sv",
        "Sm": "Sm_3",
        "Sn": "Sn_d",
        "Sr": "Sr_sv",
        "Ta": "Ta_pv",
        "Tb": "Tb_3",
        "Tc": "Tc_pv",
        "Ti": "Ti_sv",
        "Tl": "Tl_d",
        "Tm": "Tm_3",
        "V": "V_sv",
        "W": "W_sv",
        "Y": "Y_sv",
        "Yb": "Yb_2",
        "Zr": "Zr_sv",
    }

    assert VASP_POTCAR_LINK == expected


def test_resolve_potcar_dir_prefers_mo_sv(tmp_path):
    potcars = tmp_path / "potcars"
    (potcars / "Mo").mkdir(parents=True)
    (potcars / "Mo_sv").mkdir(parents=True)
    (potcars / "Mo" / "POTCAR").write_text(" ENMAX = 224; \n", encoding="utf-8")
    (potcars / "Mo_sv" / "POTCAR").write_text(" ENMAX = 242; \n", encoding="utf-8")

    assert resolve_potcar_dir("Mo", potcars) == potcars / "Mo_sv"


def test_write_potcar_concatenates_source_bytes_without_extra_newlines(tmp_path):
    potcars = tmp_path / "potcars"
    h_source = b"H potential\n ENMAX = 10; \n End of Dataset\n"
    he_source = b"He potential\n ENMAX = 20; \n End of Dataset\n"
    (potcars / "H").mkdir(parents=True)
    (potcars / "He").mkdir(parents=True)
    (potcars / "H" / "POTCAR").write_bytes(h_source)
    (potcars / "He" / "POTCAR").write_bytes(he_source)

    max_enmax = write_potcar(["H", "He"], potcars, tmp_path / "POTCAR")

    assert max_enmax == 20
    assert (tmp_path / "POTCAR").read_bytes() == h_source + he_source


def test_write_kpoints_uses_rectangular_scale_as_supercell_length(tmp_path):
    lat_vec = [[2.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 10.0]]

    write_kpoints(tmp_path, lat_vec=lat_vec, k_mesh=12, k_scale=(2, 3))

    lines = (tmp_path / "KPOINTS").read_text(encoding="utf-8").splitlines()
    assert lines[3] == "4 2 1"


def test_copy_example_uses_bundled_template_independent_of_source_checkout(tmp_path, monkeypatch):
    installed_module = tmp_path / "site-packages" / "dpmoire_lite" / "inputs.py"
    installed_module.parent.mkdir(parents=True)
    installed_module.write_text("# installed module placeholder\n", encoding="utf-8")
    monkeypatch.setattr(inputs, "__file__", str(installed_module))
    monkeypatch.chdir(tmp_path)

    target_dir = tmp_path / "copied-example"
    inputs.copy_example(target_dir)

    assert (target_dir / "config.yaml").is_file()
    assert (target_dir / "input" / "top_layer.poscar").is_file()
    assert (target_dir / "input" / "bot_layer.poscar").is_file()
    assert (target_dir / "scripts" / "sub").is_file()
