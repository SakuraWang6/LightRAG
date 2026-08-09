"""Unit tests for the file-backed evaluation job manager."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from lightrag.api import eval_jobs
from memory_eval_tests.experiments.supervise import RunParams, params_from_args

pytestmark = pytest.mark.offline


def _job_dir(tmp_path: Path, job: dict) -> Path:
    root = eval_jobs.jobs_root(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    eval_jobs._write_job(root, job)
    return root


def test_probe_process_start_returns_identifier() -> None:
    started = eval_jobs._probe_process_start(os.getpid())
    # Linux (/proc) and macOS (ps) expose it; sandboxes may deny both.
    assert started is None or isinstance(started, int)


def test_job_liveness_dead_and_alive_and_reused(monkeypatch) -> None:
    monkeypatch.setattr(eval_jobs, "_probe_process_start", lambda pid: 12345)
    assert eval_jobs.job_liveness({"pid": 2_147_483_647}) == "dead"
    assert (
        eval_jobs.job_liveness({"pid": os.getpid(), "process_started_at": 12345})
        == "alive"
    )
    assert (
        eval_jobs.job_liveness({"pid": os.getpid(), "process_started_at": 99999})
        == "reused"
    )


def test_cancel_refuses_reused_pid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(eval_jobs, "_probe_process_start", lambda pid: 12345)
    job = {
        "id": "run-stale",
        "kind": "run",
        "pid": os.getpid(),
        "process_started_at": 99999,
        "output_dir": str(tmp_path / "run"),
        "status": "running",
    }
    _job_dir(tmp_path, job)

    def boom(*args, **kwargs):
        raise AssertionError("killpg must not run for a reused pid")

    monkeypatch.setattr(eval_jobs, "_terminate_process_tree", boom)
    result = eval_jobs.cancel_job(
        runs_root=tmp_path,
        datasets_root=tmp_path / "generated",
        job_id="run-stale",
    )
    assert result["status"] == "stale"


def test_start_run_job_does_not_persist_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict = {}
    monkeypatch.setattr(eval_jobs, "_probe_process_start", lambda pid: 777)

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            self.pid = 4242

    monkeypatch.setattr(eval_jobs.subprocess, "Popen", FakeProc)
    params = RunParams(
        experiment="context_size",
        dataset=Path("memory_data_service/generated/rich-smoke-v1"),
        output_dir=Path("out"),
        api_key="super-secret",
        access_token="super-token",
    )
    job = eval_jobs.start_run_job(
        runs_root=tmp_path,
        params=params,
        supervise=True,
        supervision="heartbeat",
        stale_minutes=60,
        max_restarts=3,
        poll_seconds=30,
    )
    assert job["params"]["experiment"] == "context_size"
    assert "api_key" not in job["params"]
    assert "access_token" not in job["params"]
    stored = json.loads(
        (eval_jobs.jobs_root(tmp_path) / job["id"] / "job.json").read_text(
            encoding="utf-8"
        )
    )
    assert "api_key" not in stored["params"]
    assert "super-secret" not in json.dumps(stored)
    assert captured["cmd"][1:4] == [
        "-m",
        "memory_eval_tests.experiments.supervise",
        "--experiment",
    ]


def test_dataset_job_rejects_oversized_pages(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exceeds default cap"):
        eval_jobs.start_dataset_job(
            runs_root=tmp_path,
            datasets_root=tmp_path / "generated",
            dataset_id="big",
            tier="stress",
            profile="rich",
            pages=2000,
            formats=["docx"],
            modalities=["text"],
        )


def test_dataset_job_builds_cli_command(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(eval_jobs, "_probe_process_start", lambda pid: 778)

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["new_session"] = kwargs.get("start_new_session")
            self.pid = 4243

    monkeypatch.setattr(eval_jobs.subprocess, "Popen", FakeProc)
    job = eval_jobs.start_dataset_job(
        runs_root=tmp_path,
        datasets_root=tmp_path / "generated",
        dataset_id="smoke-1",
        tier="smoke",
        profile="rich",
        pages=12,
        formats=["docx"],
        modalities=["text", "tables"],
        force=True,
    )
    assert captured["new_session"] is True
    cmd = captured["cmd"]
    assert cmd[cmd.index("--dataset-id") + 1] == "smoke-1"
    assert "--force" in cmd
    assert cmd[cmd.index("--pages") + 1] == "12"
    assert (tmp_path / ".jobs" / job["id"] / "run.log").parent.is_dir()


def test_unique_run_dirs_do_not_collide(tmp_path: Path) -> None:
    first = eval_jobs._unique_run_dir(tmp_path, "exp")
    second = eval_jobs._unique_run_dir(tmp_path, "exp")
    assert first != second
    assert first.parent == tmp_path


def test_serializer_consistency_cli_vs_api(tmp_path: Path) -> None:
    from memory_eval_tests.experiments import supervise

    namespace = argparse.Namespace(
        experiment="context_size",
        dataset=Path("memory_data_service/generated/rich-smoke-v1"),
        output_dir=Path("memory_eval_tests/runs/x"),
        run_id=None,
        model=None,
        mode=None,
        top_k=5,
        chunk_top_k=None,
        num_ctx=None,
        num_predict=None,
        temperature=None,
        ollama_url="http://127.0.0.1:11434",
        rag_api_url="http://127.0.0.1:9621",
        api_key=None,
        access_token=None,
        runs_root=None,
        storage_dir=None,
        engine=None,
        max_cases=7,
        skip_kg=False,
        extra=[],
    )
    from_cli = supervise.build_run_command(params_from_args(namespace))
    from_api = supervise.build_run_command(
        RunParams(
            experiment="context_size",
            dataset=Path("memory_data_service/generated/rich-smoke-v1"),
            output_dir=Path("memory_eval_tests/runs/x"),
            top_k=5,
            max_cases=7,
        )
    )
    assert from_cli == from_api
