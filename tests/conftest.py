from pathlib import Path

import pytest


SAMPLE_DIR = Path(r"E:\codespace\MLFF\03_constrained_shear_scan")


@pytest.fixture
def sample_dir():
    if not SAMPLE_DIR.exists():
        pytest.skip(f"Sample directory not available: {SAMPLE_DIR}")
    return SAMPLE_DIR
