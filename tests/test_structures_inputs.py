import tempfile

import pytest

from ase import Atoms
from ase.io.vasp import read_vasp

from dpmoire_lite import inputs
from dpmoire_lite.inputs import (
    VASP_POTCAR_LINK,
    needs_vdw_kernel,
    read_zval,
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


def test_structure_label_compatibility_read_uses_no_temporary_file(
    tmp_path, monkeypatch
):
    poscar = tmp_path / "legacy-label.poscar"
    poscar.write_text(
        "\n".join(
            [
                "legacy label",
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

    def fail_temporary_file(*_args, **_kwargs):
        raise AssertionError("compatibility parsing created a temporary file")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", fail_temporary_file)

    handler = object.__new__(StructureHandler)
    atoms = handler.read_atoms(poscar)

    assert atoms.get_chemical_symbols() == ["I"]
    assert sorted(path.name for path in tmp_path.iterdir()) == [poscar.name]


def test_needs_vdw_kernel_detects_nonlocal_vdw():
    assert needs_vdw_kernel("LUSE_VDW = .TRUE.\n") is True
    assert needs_vdw_kernel("GGA = PE\n") is False


def test_render_incar_uses_shared_document_analysis(tmp_path):
    template = tmp_path / "input" / "MD_INCAR"
    template.parent.mkdir()
    template.write_text("ISMEAR=-1\nISMEAR=0\nENCUT=400\n", encoding="utf-8")
    output = tmp_path / "stage0" / "INCAR"

    with pytest.raises(ValueError, match="ISMEAR"):
        inputs.render_incar(template, output, 600.0, 7.2, 7.2, ["Mo", "S"])

    assert not output.parent.exists()


def test_needs_vdw_kernel_detects_semicolon_and_lowercase_definition():
    assert needs_vdw_kernel("ENCUT=400; luse_vdw=.TRUE.\n") is True


def test_needs_vdw_kernel_rejects_conflicting_definitions():
    with pytest.raises(ValueError, match="LUSE_VDW"):
        needs_vdw_kernel("LUSE_VDW=T\nluse_vdw=F\n")


def test_replace_incar_values_updates_encut_rcut_and_langevin():
    template = "ENCUT=400\nML_RCUT1 = 1\nML_RCUT2 = 1\nLANGEVIN_GAMMA = 10 20 30\n"
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
    assert "LANGEVIN_GAMMA = 10 20 30" in rendered
    assert "LANGEVIN_GAMMA = 1 1 1" not in rendered


def test_copy_vdw_if_needed_uses_the_same_effective_luse_vdw(tmp_path):
    template = tmp_path / "MD_INCAR"
    template.write_text("ENCUT=400; luse_vdw=T\n", encoding="utf-8")
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    kernel = b"synthetic vdw kernel\n"
    (input_dir / "vdw_kernel.bindat").write_bytes(kernel)
    output_dir = tmp_path / "stage0"

    inputs.copy_vdw_if_needed(template, input_dir, output_dir)

    assert (output_dir / "vdw_kernel.bindat").read_bytes() == kernel


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


def test_resolve_potcar_dir_minimal_policy_uses_lowest_zval(tmp_path):
    potcars = tmp_path / "potcars"
    (potcars / "Mo").mkdir(parents=True)
    (potcars / "Mo_sv").mkdir(parents=True)
    (potcars / "Mo" / "POTCAR").write_text(" ENMAX = 224; ZVAL = 6; \n", encoding="utf-8")
    (potcars / "Mo_sv" / "POTCAR").write_text(" ENMAX = 242; ZVAL = 14; \n", encoding="utf-8")

    assert resolve_potcar_dir("Mo", potcars, potcar_policy="minimal") == potcars / "Mo"


def test_resolve_potcar_dir_minimal_policy_ignores_hydrogen_fractional_variants(tmp_path):
    potcars = tmp_path / "potcars"
    (potcars / "H").mkdir(parents=True)
    (potcars / "H.25").mkdir(parents=True)
    (potcars / "H" / "POTCAR").write_text(" ENMAX = 250; ZVAL = 1; \n", encoding="utf-8")
    (potcars / "H.25" / "POTCAR").write_text(" ENMAX = 250; ZVAL = 0.25; \n", encoding="utf-8")

    assert resolve_potcar_dir("H", potcars, potcar_policy="minimal") == potcars / "H"


def test_read_zval_accepts_vasp_mass_and_valenz_line(tmp_path):
    potcar = tmp_path / "POTCAR"
    potcar.write_text("   POMASS =   95.940; ZVAL   =    6.000    mass and valenz\n", encoding="utf-8")

    assert read_zval(potcar) == 6.0


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


def test_write_potcar_uses_minimal_policy(tmp_path):
    potcars = tmp_path / "potcars"
    mo_source = b"Mo minimal\n ENMAX = 224; ZVAL = 6; \n End of Dataset\n"
    mo_sv_source = b"Mo recommended\n ENMAX = 242; ZVAL = 14; \n End of Dataset\n"
    te_source = b"Te\n ENMAX = 174; ZVAL = 6; \n End of Dataset\n"
    for name, source in {"Mo": mo_source, "Mo_sv": mo_sv_source, "Te": te_source}.items():
        (potcars / name).mkdir(parents=True)
        (potcars / name / "POTCAR").write_bytes(source)

    write_potcar(["Mo", "Te"], potcars, tmp_path / "POTCAR", potcar_policy="minimal")

    assert (tmp_path / "POTCAR").read_bytes() == mo_source + te_source


def test_write_kpoints_uses_actual_cell_lengths(tmp_path):
    lat_vec = [[2.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 10.0]]

    write_kpoints(tmp_path, lat_vec=lat_vec, k_mesh=12)

    lines = (tmp_path / "KPOINTS").read_text(encoding="utf-8").splitlines()
    assert lines[3] == "6 4 1"


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
