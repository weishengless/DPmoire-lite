from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class SlurmJob:
    job_id: str
    path: str
    status: str = "SUBMITTED"

    def as_dict(self):
        return asdict(self)


def parse_sbatch_output(output: str) -> str:
    words = output.strip().split()
    if not words:
        raise ValueError("Empty sbatch output")
    return words[-1]


def parse_sacct_states(output: str) -> dict[str, str]:
    states: dict[str, str] = {}
    for line in output.splitlines():
        words = line.split()
        if len(words) < 2:
            continue
        if words[0].lower() == "jobid" or set(words[0]) == {"-"}:
            continue
        states[words[0]] = words[1]
    return states


class SlurmRunner:
    def __init__(self, script_name: str, n_nodes: int, auto_resub: bool):
        self.script_name = script_name
        self.n_nodes = n_nodes
        self.auto_resub = auto_resub

    def submit(self, work_dir: Path, rel_path: str) -> SlurmJob:
        result = subprocess.run(
            ["sbatch", self.script_name],
            cwd=str(work_dir),
            check=True,
            text=True,
            capture_output=True,
        )
        return SlurmJob(job_id=parse_sbatch_output(result.stdout), path=rel_path)

    def query(self, job_ids: list[str]) -> dict[str, str]:
        if not job_ids:
            return {}
        result = subprocess.run(
            ["sacct", "-j", ",".join(job_ids), "--format", "JobID,State"],
            check=True,
            text=True,
            capture_output=True,
        )
        return parse_sacct_states(result.stdout)

    def wait(self, jobs: list[SlurmJob], poll_seconds: int = 30) -> list[SlurmJob]:
        remaining = {job.job_id: job for job in jobs}
        while remaining:
            states = self.query(list(remaining))
            for job_id, state in states.items():
                if job_id not in remaining:
                    continue
                if state in {"PENDING", "RUNNING"}:
                    continue
                remaining[job_id].status = state
                del remaining[job_id]
            if remaining:
                time.sleep(poll_seconds)
        return jobs
