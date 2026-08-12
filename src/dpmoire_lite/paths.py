from pathlib import Path
from shutil import move


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


def backup_existing_directory(work_dir: Path, stage: str, target: Path, timestamp: str) -> Path | None:
    if stage not in VALID_STAGES:
        raise ValueError(f"Unknown stage: {stage}")

    work_dir = Path(work_dir)
    target = Path(target)
    if not target.exists():
        return None

    if not target.is_dir():
        raise NotADirectoryError(target)

    resolved_work_dir = work_dir.resolve()
    resolved_target = target.resolve()
    try:
        resolved_target.relative_to(resolved_work_dir)
    except ValueError as exc:
        raise ValueError("Backup target must be inside work_dir") from exc

    backup_root = (work_dir / "backups" / stage).resolve()
    backup = backup_root / f"{target.name}_{timestamp}"
    try:
        backup.resolve().relative_to(backup_root)
    except ValueError as exc:
        raise ValueError("Backup path must be inside backup root") from exc

    if backup.exists():
        raise FileExistsError(backup)

    backup.parent.mkdir(parents=True, exist_ok=True)
    move(str(target), str(backup))
    return backup
