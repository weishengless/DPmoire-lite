from pathlib import Path


STAGE_OUTPUTS = {
    "rlx": "rlx_data.extxyz",
    "md": "MD_data.extxyz",
    "validation": "valid.extxyz",
}

VALID_STAGES = {"init_mlff", *STAGE_OUTPUTS}


def stage_dir(work_dir: Path, stage: str) -> Path:
    if stage not in VALID_STAGES:
        raise ValueError(f"Unknown stage: {stage}")
    return Path(work_dir) / stage


def manifest_path(work_dir: Path, stage: str) -> Path:
    return stage_dir(work_dir, stage) / "manifest.yaml"


def relative_to_workdir(work_dir: Path, path: Path) -> str:
    return Path(path).resolve().relative_to(Path(work_dir).resolve()).as_posix()
