from pathlib import Path

from dpmoire_lite.slurm import SlurmJob, SlurmRunner


class QueueRunner(SlurmRunner):
    def __init__(self, *, n_nodes=1, auto_resub=False, state_sequences=None):
        super().__init__("submit.sh", n_nodes=n_nodes, auto_resub=auto_resub)
        self.state_sequences = state_sequences or {}
        self.submitted = []
        self.query_log = []
        self._next_id = 1

    def submit(self, work_dir, rel_path):
        job = SlurmJob(job_id=str(self._next_id), path=rel_path)
        self._next_id += 1
        self.submitted.append((rel_path, job.job_id))
        return job

    def query(self, job_ids):
        self.query_log.append(list(job_ids))
        states = {}
        for job_id in job_ids:
            sequence = self.state_sequences[job_id]
            state = sequence.pop(0)
            states[job_id] = state
        return states


def test_submit_many_wait_throttles_to_one_active_job():
    runner = QueueRunner(
        n_nodes=1,
        state_sequences={
            "1": ["RUNNING", "COMPLETED"],
            "2": ["COMPLETED"],
        },
    )
    items = [(Path("first"), "rlx/0_0"), (Path("second"), "rlx/1_0")]

    jobs = runner.submit_many(items, wait=True, poll_seconds=0)

    assert runner.submitted == [("rlx/0_0", "1"), ("rlx/1_0", "2")]
    assert runner.query_log == [["1"], ["1"], ["2"]]
    assert [(job.path, job.status) for job in jobs] == [
        ("rlx/0_0", "COMPLETED"),
        ("rlx/1_0", "COMPLETED"),
    ]


def test_submit_many_wait_does_not_resubmit_failed_job_when_auto_resub_disabled():
    runner = QueueRunner(
        n_nodes=1,
        auto_resub=False,
        state_sequences={"1": ["FAILED"]},
    )

    jobs = runner.submit_many([(Path("failed"), "rlx/0_0")], wait=True, poll_seconds=0)

    assert runner.submitted == [("rlx/0_0", "1")]
    assert [(job.path, job.status) for job in jobs] == [("rlx/0_0", "FAILED")]


def test_submit_many_wait_resubmits_failed_job_once_and_preserves_path():
    runner = QueueRunner(
        n_nodes=1,
        auto_resub=True,
        state_sequences={
            "1": ["CANCELLED"],
            "2": ["COMPLETED"],
        },
    )

    jobs = runner.submit_many([(Path("first"), "rlx/0_0")], wait=True, poll_seconds=0)

    assert runner.submitted == [
        ("rlx/0_0", "1"),
        ("rlx/0_0", "2"),
    ]
    assert [(job.path, job.status) for job in jobs] == [
        ("rlx/0_0", "CANCELLED"),
        ("rlx/0_0", "COMPLETED"),
    ]
