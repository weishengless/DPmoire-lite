from dpmoire_lite.slurm import SlurmJob, parse_sbatch_output, parse_sacct_states


def test_parse_sbatch_output_extracts_job_id():
    assert parse_sbatch_output("Submitted batch job 12345\n") == "12345"


def test_parse_sacct_states_maps_states():
    output = """JobID State
------------ ----------
12345 COMPLETED
12346 FAILED
12347 RUNNING
"""
    states = parse_sacct_states(output)
    assert states["12345"] == "COMPLETED"
    assert states["12346"] == "FAILED"
    assert states["12347"] == "RUNNING"


def test_slurm_job_records_path():
    job = SlurmJob(job_id="12345", path="rlx/0_0", status="SUBMITTED")
    assert job.path == "rlx/0_0"
