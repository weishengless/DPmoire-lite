from pathlib import Path

import pytest


try:
    from dpmoire_lite.mlab import MlabParseError, parse_mlab
except ImportError:
    class MlabParseError(ValueError):
        pass

    def parse_mlab(_path):
        raise MlabParseError("structured parser is not implemented")


FIXTURE_ROOT = Path(__file__).parent / "data" / "mlab"
INVENTORY = FIXTURE_ROOT / "README.md"
FIXTURE_NAMES = (
    "complete_multi.mlab",
    "tail_position_crop.mlab",
    "tail_force_crop.mlab",
    "tail_stress_crop.mlab",
    "first_incomplete.mlab",
    "internal_corruption.mlab",
    "format_variant_a.mlab",
    "format_variant_b.mlab",
    "complete_vasp_641.mlab",
    "complete_vasp_651.mlab",
)


def test_mlab_fixture_inventory_has_provenance_entry():
    text = INVENTORY.read_text(encoding="utf-8")

    for name in FIXTURE_NAMES:
        assert f"`{name}`" in text
    assert "Source type:" in text
    assert "Reason for cropping:" in text
    assert "Expected parser behavior:" in text
    assert "Redistribution confirmation:" in text


def test_mlab_fixture_inventory_records_vasp_641_and_651():
    text = INVENTORY.read_text(encoding="utf-8")

    assert "`complete_vasp_641.mlab`" in text
    assert "VASP 6.4.1" in text
    assert "`complete_vasp_651.mlab`" in text
    assert "VASP 6.5.1" in text


def test_mlab_fixtures_contain_no_private_path_or_potcar_marker():
    fixture_files = sorted(FIXTURE_ROOT.glob("*.mlab"))
    assert fixture_files

    forbidden_markers = (
        "POTCAR",
        "potpaw",
        "WAVECAR",
        "CHGCAR",
        "C:\\Users\\",
        "E:\\",
        "/home/",
        "SBATCH",
        "SLURM",
        "hostname",
        "account",
        "partition",
    )
    for path in fixture_files:
        payload = path.read_text(encoding="utf-8")
        for marker in forbidden_markers:
            assert marker not in payload, f"{marker!r} found in {path.name}"


def _write_variant(tmp_path: Path, source_name: str, old: str, new: str) -> Path:
    source = (FIXTURE_ROOT / source_name).read_text(encoding="utf-8")
    assert old in source
    target = tmp_path / source_name
    target.write_text(source.replace(old, new, 1), encoding="utf-8")
    return target


def test_parse_complete_mlab_returns_declared_configurations():
    result = parse_mlab(FIXTURE_ROOT / "complete_multi.mlab")

    assert result.status == "complete"
    assert result.declared_count == 2
    assert len(result.configurations) == 2


def test_parse_mlab_preserves_element_and_atom_order():
    result = parse_mlab(FIXTURE_ROOT / "complete_multi.mlab")

    configuration = result.configurations[0]
    assert configuration.elements == ("O", "Pt")
    assert configuration.counts == (1, 1)
    assert configuration.n_atoms == 2
    assert configuration.positions[0] == pytest.approx((0.0, 0.0, 1.0))
    assert configuration.positions[1] == pytest.approx((2.0, 2.0, 2.0))


def test_parse_mlab_returns_cartesian_positions():
    result = parse_mlab(FIXTURE_ROOT / "complete_multi.mlab")

    assert all(
        actual == pytest.approx(expected)
        for actual, expected in zip(
            result.configurations[0].positions,
            ((0.0, 0.0, 1.0), (2.0, 2.0, 2.0)),
        )
    )
    assert all(
        actual == pytest.approx(expected)
        for actual, expected in zip(
            result.configurations[0].lattice,
            ((4.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 12.0)),
        )
    )


def test_parse_mlab_preserves_raw_kbar_stress_order():
    result = parse_mlab(FIXTURE_ROOT / "complete_multi.mlab")

    assert result.configurations[0].stress_kbar == pytest.approx(
        (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    )


def test_parse_mlab_rejects_type_count_sum_mismatch(tmp_path):
    path = _write_variant(
        tmp_path,
        "complete_vasp_651.mlab",
        "     O      1\n     Pt     1\n",
        "     O      2\n     Pt     1\n",
    )

    with pytest.raises(MlabParseError, match="atom type counts"):
        parse_mlab(path)


def test_parse_mlab_rejects_position_or_force_shape_mismatch(tmp_path):
    position_path = _write_variant(
        tmp_path,
        "complete_vasp_651.mlab",
        "   2.000000000000000E+000  2.000000000000000E+000  2.000000000000000E+000\n==================================================\n     Total energy",
        "   2.000000000000000E+000  2.000000000000000E+000\n==================================================\n     Total energy",
    )
    force_path = _write_variant(
        tmp_path,
        "complete_vasp_651.mlab",
        "  -1.000000000000000E-001  0.000000000000000E+000  0.000000000000000E+000\n==================================================\n     Stress",
        "  -1.000000000000000E-001  0.000000000000000E+000\n==================================================\n     Stress",
    )

    for path in (position_path, force_path):
        with pytest.raises(MlabParseError, match="shape"):
            parse_mlab(path)


def test_parse_mlab_rejects_nan_and_infinity(tmp_path):
    for token in ("nan", "inf", "-inf"):
        path = _write_variant(
            tmp_path,
            "complete_vasp_651.mlab",
            "  -1.250000000000000E+000\n==================================================\n     Forces",
            f"  {token}\n==================================================\n     Forces",
        )

        with pytest.raises(MlabParseError, match="finite"):
            parse_mlab(path)


def test_initial_seed_requires_header_count_equal_complete_count(tmp_path):
    path = _write_variant(
        tmp_path,
        "complete_multi.mlab",
        "     The number of configurations\n--------------------------------------------------\n         2\n**************************************************",
        "     The number of configurations\n--------------------------------------------------\n         3\n**************************************************",
    )

    with pytest.raises(MlabParseError, match="configuration count"):
        parse_mlab(path)


def test_vasp_641_fixture_parses_canonical_fields():
    result = parse_mlab(FIXTURE_ROOT / "complete_vasp_641.mlab")

    configuration = result.configurations[0]
    assert result.status == "complete"
    assert result.declared_count == 1
    assert configuration.elements == ("C",)
    assert configuration.counts == (1,)
    assert configuration.n_atoms == 1
    assert all(
        actual == pytest.approx(expected)
        for actual, expected in zip(
            configuration.lattice,
            ((4.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 12.0)),
        )
    )
    assert configuration.positions[0] == pytest.approx((0.0, 0.0, 1.0))
    assert configuration.energy == pytest.approx(-1.0)
    assert configuration.forces[0] == pytest.approx((0.0, 0.0, 0.0))
    assert configuration.stress_kbar == pytest.approx((1.0, 2.0, 3.0, 4.0, 5.0, 6.0))


def test_vasp_651_fixture_parses_canonical_fields():
    result = parse_mlab(FIXTURE_ROOT / "complete_vasp_651.mlab")

    configuration = result.configurations[0]
    assert result.status == "complete"
    assert result.declared_count == 1
    assert configuration.elements == ("O", "Pt")
    assert configuration.counts == (1, 1)
    assert configuration.n_atoms == 2
    assert configuration.energy == pytest.approx(-1.25)
    assert all(
        actual == pytest.approx(expected)
        for actual, expected in zip(
            configuration.forces,
            ((0.1, 0.0, 0.0), (-0.1, 0.0, 0.0)),
        )
    )
    assert configuration.stress_kbar == pytest.approx((1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
