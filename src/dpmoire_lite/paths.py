from pathlib import Path
from shutil import move


VALID_STAGES = {"init_mlff", "rlx", "md", "validation"}


def stage_dir(work_dir: Path, stage: str) -> Path:
    if stage not in VALID_STAGES:
        raise ValueError(f"Unknown stage: {stage}")
    return Path(work_dir) / stage


def manifest_path(work_dir: Path, stage: str) -> Path:
    return stage_dir(work_dir, stage) / "manifest.yaml"


def relative_to_workdir(work_dir: Path, path: Path) -> str:
    return Path(path).resolve().relative_to(Path(work_dir).resolve()).as_posix()


def backup_existing_directory(work_dir: Path, stage: str, target: Path, timestamp: str) -> Path | None:
    target = Path(target)
    if not target.exists():
        return None

    backup = Path(work_dir) / "backups" / stage / f"{target.name}_{timestamp}"
    if backup.exists():
        raise FileExistsError(backup)

    backup.parent.mkdir(parents=True, exist_ok=True)
    move(str(target), str(backup))
    return backup
