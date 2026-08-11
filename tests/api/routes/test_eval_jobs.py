"""File-backed job safety tests for the product evaluation workflow."""

from __future__ import annotations

import json
from pathlib import Path

from lightrag.api import eval_jobs
from memory_eval_tests.runner import RunParams, build_run_command


def test_run_command_uses_only_the_product_cli(tmp_path: Path) -> None:
    command = build_run_command(
        RunParams(dataset=tmp_path / "dataset", output_dir=tmp_path / "output")
    )
    assert command[:3] == [command[0], "-m", "memory_eval_tests.cli"]
    assert "--experiment" not in command


def test_job_file_is_written_atomically(tmp_path: Path) -> None:
    jobs = eval_jobs.jobs_root(tmp_path)
    job = {
        "id": "evaluation-1",
        "kind": "run",
        "evaluation": "end_to_end",
        "output_dir": str(tmp_path / "evaluation-1"),
        "status": "pending",
    }
    eval_jobs._write_job(jobs, job)

    path = jobs / "evaluation-1" / "job.json"
    assert json.loads(path.read_text(encoding="utf-8")) == job
    assert not (jobs / "evaluation-1" / "job.json.tmp").exists()
