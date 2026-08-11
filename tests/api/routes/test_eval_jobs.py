"""File-backed job safety tests for the product evaluation workflow."""

from __future__ import annotations

import json
from pathlib import Path

from lightrag.api import eval_jobs
from lightrag.api.eval_index import scan_runs
from memory_eval_tests.runner import RunParams, build_run_command


def test_run_command_uses_only_the_product_cli(tmp_path: Path) -> None:
    command = build_run_command(
        RunParams(dataset=tmp_path / "dataset", output_dir=tmp_path / "output")
    )
    assert command[:3] == [command[0], "-m", "memory_eval_tests.cli"]
    assert "--experiment" not in command
    assert "--ollama-url" not in command
    assert "--rag-api-url" not in command
    assert "--storage-dir" not in command


def test_legacy_job_parameters_do_not_block_resumption(tmp_path: Path) -> None:
    params = eval_jobs._params_from_json(
        {
            "dataset": str(tmp_path / "dataset"),
            "output_dir": str(tmp_path / "output"),
            "ollama_url": "http://obsolete.invalid",
            "rag_api_url": "http://obsolete.invalid",
            "storage_dir": str(tmp_path / "obsolete-storage"),
        }
    )

    assert params.dataset == tmp_path / "dataset"
    assert params.output_dir == tmp_path / "output"


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


def test_dataset_job_persists_requested_language(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(eval_jobs, "_dispatch", lambda *_args, **_kwargs: None)

    job = eval_jobs.start_dataset_job(
        runs_root=tmp_path,
        dataset_id=None,
        display_name="中文检索质量测评",
        tier="smoke",
        profile="rich",
        pages=2,
        formats=["docx"],
        modalities=["text"],
        language="zh",
    )

    assert job["params"]["language"] == "zh"
    assert job["params"]["display_name"] == "中文检索质量测评"
    assert job["display_name"] == "中文检索质量测评"
    assert job["dataset_id"] == job["id"]


def test_pending_run_is_visible_before_a_worker_starts(
    tmp_path: Path, monkeypatch
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "dataset_id": "sample",
                "pages": 2,
                "tier": "smoke",
                "profile": "rich",
                "formats": ["docx"],
            }
        ),
        encoding="utf-8",
    )
    (dataset / "oracle.json").write_text(
        json.dumps({"questions": [{"id": "Q-1"}]}), encoding="utf-8"
    )
    monkeypatch.setattr(eval_jobs, "_dispatch", lambda *_args, **_kwargs: None)

    job = eval_jobs.start_run_job(
        runs_root=tmp_path,
        params=RunParams(dataset=dataset, output_dir=tmp_path / "unused", label="queued"),
        supervise=False,
        supervision="auto",
        stale_minutes=60,
        max_restarts=0,
        poll_seconds=30,
    )

    envelope = json.loads((Path(job["output_dir"]) / "run.json").read_text())
    assert envelope["status"] == "queued"
    assert envelope["launch_params"]["case_ids"] == ["Q-1"]
    records = scan_runs(tmp_path, force=True)
    assert [(record["id"], record["status"]) for record in records] == [
        (Path(job["output_dir"]).name, "queued")
    ]


def test_dispatch_fills_each_configured_capacity_slot(
    tmp_path: Path, monkeypatch
) -> None:
    jobs = eval_jobs.jobs_root(tmp_path)
    for job_id in ("run-a", "run-b"):
        eval_jobs._write_job(
            jobs,
            {
                "id": job_id,
                "kind": "run",
                "status": "pending",
                "created_at": job_id,
                "params": {},
                "output_dir": str(tmp_path / job_id),
            },
        )
    started: list[str] = []
    monkeypatch.setenv("MEMORY_EVAL_MAX_ACTIVE_JOBS", "2")
    monkeypatch.setattr(eval_jobs, "_start_dispatch_loop", lambda *_args: None)
    monkeypatch.setattr(eval_jobs, "_params_from_json", lambda _payload: RunParams(tmp_path, tmp_path / "out"))
    monkeypatch.setattr(
        eval_jobs,
        "_claim_owner",
        lambda: {"owner_id": "worker", "lease_expires_at": "2999-01-01T00:00:00+00:00"},
    )
    monkeypatch.setattr(
        eval_jobs,
        "_spawn_run_job",
        lambda *, job_id, **_kwargs: started.append(job_id),
    )

    eval_jobs._dispatch(tmp_path, tmp_path / "datasets")
    assert started == ["run-a", "run-b"]


def test_dispatch_failure_marks_the_visible_queued_run_as_failed(
    tmp_path: Path, monkeypatch
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(json.dumps({"dataset_id": "sample"}))
    (dataset / "oracle.json").write_text(json.dumps({"questions": [{"id": "Q-1"}]}))
    monkeypatch.setattr(eval_jobs, "_start_dispatch_loop", lambda *_args: None)

    def fail_spawn(**_kwargs) -> None:
        raise RuntimeError("worker binary unavailable")

    monkeypatch.setattr(eval_jobs, "_spawn_run_job", fail_spawn)
    job = eval_jobs.start_run_job(
        runs_root=tmp_path,
        params=RunParams(dataset=dataset, output_dir=tmp_path / "unused"),
        supervise=False,
        supervision="auto",
        stale_minutes=60,
        max_restarts=0,
        poll_seconds=30,
    )

    assert job["status"] == "failed"
    envelope = json.loads((Path(job["output_dir"]) / "run.json").read_text())
    assert envelope["status"] == "failed"
    assert envelope["failure"]["phase"] == "dispatch"
