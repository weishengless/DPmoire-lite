from pathlib import Path


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
