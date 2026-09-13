from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import dpmoire_lite.slurm as slurm_module
from dpmoire_lite.build import _submit_dirs
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


def test_submit_removes_inherited_sbatch_wait_environment(monkeypatch, tmp_path):
    observed = {}

    def synthetic_run(command, *, cwd, check, text, capture_output, env):
        observed.update(
            command=command,
            cwd=cwd,
            check=check,
            text=text,
            capture_output=capture_output,
            env=env,
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Submitted batch job 12345\n",
        )

    monkeypatch.setenv("SBATCH_WAIT", "1")
    monkeypatch.setattr(slurm_module.subprocess, "run", synthetic_run)

    job = SlurmRunner("submit.sh", n_nodes=1, auto_resub=False).submit(
        tmp_path,
        "init_mlff",
    )

    assert job.job_id == "12345"
    assert "SBATCH_WAIT" not in observed["env"]


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


def test_submit_many_wait_treats_held_jobs_as_active():
    runner = QueueRunner(
        n_nodes=1,
        state_sequences={
            "1": ["REQUEUE_HOLD", "SPECIAL_EXIT", "COMPLETED"],
            "2": ["COMPLETED"],
        },
    )
    items = [(Path("held"), "rlx/0_0"), (Path("second"), "rlx/1_0")]

    jobs = runner.submit_many(items, wait=True, poll_seconds=0)

    assert runner.submitted == [("rlx/0_0", "1"), ("rlx/1_0", "2")]
    assert runner.query_log == [["1"], ["1"], ["1"], ["2"]]
    assert [(job.path, job.status) for job in jobs] == [
        ("rlx/0_0", "COMPLETED"),
        ("rlx/1_0", "COMPLETED"),
    ]


def test_submit_dirs_without_wait_submits_all_jobs_even_when_n_nodes_is_one(tmp_path):
    runner = QueueRunner(n_nodes=1)
    directories = [tmp_path / "first", tmp_path / "second"]
    for directory in directories:
        directory.mkdir()

    jobs = _submit_dirs(SimpleNamespace(work_dir=tmp_path), runner, directories, wait=False)

    assert runner.submitted == [("first", "1"), ("second", "2")]
    assert runner.query_log == []
    assert [(job.path, job.status) for job in jobs] == [
        ("first", "SUBMITTED"),
        ("second", "SUBMITTED"),
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


ARRAY_TEMPLATE = """#!/usr/bin/env bash
#SBATCH -J NbSe2
#SBATCH -p batch
#SBATCH -N 1 --ntasks-per-node=64
#SBATCH --time=2-0:0:0
#SBATCH -o out.%j
#SBATCH -e err.%j

export OMP_NUM_THREADS=1

mpirun /usr/local/bin/vasp_std >sout
"""


def test_render_array_script_injects_array_line_overrides_and_mapping():
    text = slurm_module.render_array_submission_script(
        ARRAY_TEMPLATE,
        ["0_0", "0_1", "0_2"],
    )

    assert "#SBATCH --array=0-2" in text
    assert "#SBATCH -o %x.%A.%a.out" in text
    assert "#SBATCH -e %x.%A.%a.err" in text
    assert 'ARRAY_FOLDERS=("0_0" "0_1" "0_2")' in text
    assert "SLURM_ARRAY_TASK_ID:?" in text
    assert 'echo "DPmoire-lite array task $TASK_ID ->' in text
    assert text.index("#SBATCH --array=0-2") > text.index("#SBATCH -e err.%j")
    assert text.index("ARRAY_FOLDERS") > text.index("#SBATCH -e err.%j")
    assert text.index('cd "$SCRIPT_DIR') < text.index("mpirun")
    assert text.rstrip().endswith("mpirun /usr/local/bin/vasp_std >sout")
    # user SBATCH lines are preserved verbatim ahead of the injected ones
    assert "#SBATCH -o out.%j" in text


def test_render_array_script_injects_throttle_when_configured():
    text = slurm_module.render_array_submission_script(
        ARRAY_TEMPLATE,
        ["0_0"],
        max_concurrent=4,
    )

    assert "#SBATCH --array=0-0%4" in text


def test_render_array_script_requires_sbatch_header():
    with pytest.raises(ValueError, match="SBATCH"):
        slurm_module.render_array_submission_script(
            "#!/usr/bin/env bash\nmpirun vasp\n", ["0_0"]
        )


def test_render_array_script_rejects_empty_folder_list():
    with pytest.raises(ValueError, match="folder"):
        slurm_module.render_array_submission_script(ARRAY_TEMPLATE,
        [],
    )
