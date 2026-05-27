from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path


ACTIVE_STATES = {
    "PENDING",
    "RUNNING",
    "CONFIGURING",
    "COMPLETING",
    "SUSPENDED",
    "REQUEUED",
    "RESIZING",
}
FAILURE_STATES = {
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
}


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

    def submit_many(
        self,
        items: list[tuple[Path, str]],
        wait: bool = False,
        poll_seconds: int = 30,
    ) -> list[SlurmJob]:
        limit = max(1, int(self.n_nodes))
        if not wait:
            # Without a wait loop there is no safe point to submit the rest while
            # preserving the configured active-job cap.
            return [self.submit(work_dir, rel_path) for work_dir, rel_path in items[:limit]]

        pending = list(enumerate(items))
        active: dict[str, tuple[SlurmJob, int]] = {}
        returned: list[SlurmJob] = []
        resubmitted: set[int] = set()

        def fill_active() -> None:
            while pending and len(active) < limit:
                item_index, (work_dir, rel_path) = pending.pop(0)
                job = self.submit(work_dir, rel_path)
                returned.append(job)
                active[job.job_id] = (job, item_index)

        fill_active()
        while active:
            states = self.query(list(active))
            for job_id, state in states.items():
                if job_id not in active or state in ACTIVE_STATES:
                    continue
                job, item_index = active.pop(job_id)
                job.status = state
                if state in FAILURE_STATES and self.auto_resub and item_index not in resubmitted:
                    resubmitted.add(item_index)
                    work_dir, rel_path = items[item_index]
                    retry = self.submit(work_dir, rel_path)
                    returned.append(retry)
                    active[retry.job_id] = (retry, item_index)
            fill_active()
            if active:
                time.sleep(poll_seconds)
        return returned

    def wait(self, jobs: list[SlurmJob], poll_seconds: int = 30) -> list[SlurmJob]:
        remaining = {job.job_id: job for job in jobs}
        while remaining:
            states = self.query(list(remaining))
            for job_id, state in states.items():
                if job_id not in remaining:
                    continue
                if state in ACTIVE_STATES:
                    continue
                remaining[job_id].status = state
                del remaining[job_id]
            if remaining:
                time.sleep(poll_seconds)
        return jobs
