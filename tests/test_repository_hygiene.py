from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DATA_ROOT = REPO_ROOT / "tests" / "data"
PACKAGE_ROOT = REPO_ROOT / "src" / "dpmoire_lite"


def test_tests_data_contains_no_potcar_or_example_test_payload():
    assert TEST_DATA_ROOT.is_dir(), "tests/data must document sanitized fixture provenance"

    forbidden_paths = [
        path.relative_to(TEST_DATA_ROOT)
        for path in TEST_DATA_ROOT.rglob("*")
        if path.name.casefold() == "potcar"
        or "example-test" in {part.casefold() for part in path.parts}
    ]

    assert forbidden_paths == []


def test_bundled_package_tree_contains_no_potcar():
    potcar_paths = [
        path.relative_to(PACKAGE_ROOT)
        for path in PACKAGE_ROOT.rglob("*")
        if path.name.casefold() == "potcar"
    ]

    assert potcar_paths == []
