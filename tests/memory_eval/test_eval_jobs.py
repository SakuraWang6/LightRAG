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


def test_cancel_terminates_supervisor_child_group(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / ".supervise-child.json").write_text(
        '{"pid": 4567, "pgid": 4567, "process_started_at": 123}\n'
    )
    current_started = eval_jobs._probe_process_start(os.getpid())
    job = {
        "id": "run-live", "kind": "run", "pid": os.getpid(),
        "process_started_at": current_started,
        "output_dir": str(run_dir), "status": "running",
    }
    _job_dir(tmp_path, job)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        eval_jobs,
        "_probe_process_start",
        lambda pid: 123 if pid == 4567 else current_started,
    )
    monkeypatch.setattr(eval_jobs, "_terminate_process_tree", lambda pid, extra_pids=None: captured.update(pid=pid, extra=extra_pids))
    result = eval_jobs.cancel_job(runs_root=tmp_path, datasets_root=tmp_path / "generated", job_id="run-live")
    assert result and result["status"] == "cancelling"
    assert captured == {"pid": os.getpid(), "extra": [4567]}


def test_tracked_child_group_refuses_unverified_or_reused_pid(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    job = {"output_dir": str(run_dir)}
    state = run_dir / ".supervise-child.json"
    state.write_text('{"pid": 4567, "pgid": 4567}\n', encoding="utf-8")
    assert eval_jobs._tracked_child_pids(job) == []
    state.write_text('{"pid": 4567, "pgid": 4567, "process_started_at": 123}\n', encoding="utf-8")
    monkeypatch.setattr(eval_jobs, "_probe_process_start", lambda _pid: 456)
    assert eval_jobs._tracked_child_pids(job) == []


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
    assert stored["claim"]["owner_id"]
    assert stored["claim"]["pid"] == os.getpid()
    assert stored["lease_expires_at"] == stored["claim"]["lease_expires_at"]
    assert stored["events_path"].endswith("events.jsonl")
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
    assert job["events_path"].endswith("events.jsonl")


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
        label=None,
        model=None,
        mode=None,
        top_k=5,
        chunk_top_k=None,
        num_ctx=None,
        num_predict=None,
        max_total_tokens=None,
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


def test_list_jobs_queue_position_and_active_count(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(eval_jobs, "_probe_process_start", lambda pid: 12345)
    root = eval_jobs.jobs_root(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    for job in (
        {
            "id": "run-a",
            "kind": "run",
            "output_dir": str(tmp_path / "a"),
            "status": "pending",
            "created_at": "2026-08-10T00:00:00+00:00",
        },
        {
            "id": "run-b",
            "kind": "run",
            "output_dir": str(tmp_path / "b"),
            "status": "pending",
            "created_at": "2026-08-10T00:01:00+00:00",
        },
        {
            "id": "run-c",
            "kind": "run",
            "output_dir": str(tmp_path / "c"),
            "status": "running",
            "pid": os.getpid(),
            "process_started_at": 12345,
            "created_at": "2026-08-10T00:02:00+00:00",
        },
        {
            "id": "run-d",
            "kind": "run",
            "output_dir": str(tmp_path / "d"),
            "status": "canceled",
            "created_at": "2026-08-10T00:03:00+00:00",
        },
    ):
        eval_jobs._write_job(root, job)
    jobs = eval_jobs.list_jobs(runs_root=tmp_path, datasets_root=tmp_path / "g")
    by_id = {job["id"]: job for job in jobs}
    assert by_id["run-c"]["active_count"] == 1
    assert by_id["run-a"]["queue_position"] == 1
    assert by_id["run-b"]["queue_position"] == 2
    assert by_id["run-c"]["queue_position"] is None
    assert by_id["run-d"]["queue_position"] is None


def test_resume_pending_jobs_dispatches_after_server_restart(
    tmp_path: Path, monkeypatch
) -> None:
    jobs = eval_jobs.jobs_root(tmp_path)
    jobs.mkdir(parents=True, exist_ok=True)
    pending = {
        "id": "run-pending",
        "kind": "run",
        "output_dir": str(tmp_path / "out"),
        "status": "pending",
        "created_at": "2026-08-10T00:00:00+00:00",
        "params": {"experiment": "context_size", "dataset": "dataset", "extra": []},
    }
    eval_jobs._write_job(jobs, pending)
    monkeypatch.setattr(eval_jobs, "_start_dispatch_loop", lambda *args: None)
    monkeypatch.setattr(eval_jobs, "_params_from_json", lambda payload: object())

    def fake_spawn(**kwargs):
        job = eval_jobs._read_job(jobs, kwargs["job_id"])
        assert job is not None
        job["status"] = "running"
        job["pid"] = 1
        eval_jobs._write_job(jobs, job)
        return job

    monkeypatch.setattr(eval_jobs, "_spawn_run_job", fake_spawn)
    eval_jobs.resume_pending_jobs(runs_root=tmp_path, datasets_root=tmp_path / "datasets")

    restarted = eval_jobs._read_job(jobs, "run-pending")
    assert restarted is not None
    assert restarted["status"] == "running"


def test_delete_job_removes_audit_dir(tmp_path: Path) -> None:
    root = eval_jobs.jobs_root(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    eval_jobs._write_job(
        root,
        {
            "id": "run-x",
            "kind": "run",
            "output_dir": str(tmp_path / "x"),
            "status": "canceled",
        },
    )
    assert (root / "run-x").exists()
    assert eval_jobs.delete_job(runs_root=tmp_path, job_id="run-x") is True
    assert not (root / "run-x").exists()
    assert eval_jobs.delete_job(runs_root=tmp_path, job_id="../../etc") is False


def test_job_id_validation_blocks_traversal(tmp_path: Path) -> None:
    assert (
        eval_jobs.get_job(
            runs_root=tmp_path,
            datasets_root=tmp_path / "generated",
            job_id="../../etc",
        )
        is None
    )
    assert (
        eval_jobs.cancel_job(
            runs_root=tmp_path,
            datasets_root=tmp_path / "generated",
            job_id="..",
        )
        is None
    )


def test_dataset_job_exit_code_drives_status(tmp_path: Path) -> None:
    generated = tmp_path / "generated"
    manifest = generated / "d1" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    base = {
        "id": "dataset-x",
        "kind": "dataset",
        "dataset_id": "d1",
        "output_dir": str(tmp_path),
        "pid": 2**31 - 1,
        "process_started_at": None,
        "status": "running",
    }
    failed = dict(base, exit_code=1)
    assert (
        eval_jobs._derive_status(failed, runs_root=tmp_path, datasets_root=generated)
        == "failed"
    )
    succeeded = dict(base, exit_code=0)
    assert (
        eval_jobs._derive_status(succeeded, runs_root=tmp_path, datasets_root=generated)
        == "complete"
    )


def test_expired_claim_is_recovered_for_another_worker(tmp_path: Path) -> None:
    root = eval_jobs.jobs_root(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    job = {
        "id": "run-claimed",
        "kind": "run",
        "output_dir": str(tmp_path / "out"),
        "status": "claiming",
        "claim": {
            "owner_id": "dead-worker",
            "pid": 999_999,
            "process_started_at": 1,
            "lease_expires_at": "2020-01-01T00:00:00+00:00",
        },
    }
    eval_jobs._write_job(root, job)

    refreshed = eval_jobs._refresh_job(
        job,
        runs_root=tmp_path,
        datasets_root=tmp_path / "generated",
        recover_expired_claim=True,
    )
    assert refreshed["status"] == "pending"
    assert "claim" not in refreshed
    assert refreshed["lease_expires_at"] is None
    assert refreshed["recovered_at"]


def test_spawn_refuses_job_claimed_by_another_worker(tmp_path: Path) -> None:
    root = eval_jobs.jobs_root(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    job = {
        "id": "run-claimed",
        "kind": "run",
        "output_dir": str(tmp_path / "out"),
        "status": "claiming",
        "claim": {"owner_id": "worker-a"},
        "params": {"experiment": "context_size", "dataset": "dataset", "extra": []},
    }
    eval_jobs._write_job(root, job)
    with pytest.raises(RuntimeError, match="no longer claimed"):
        eval_jobs._spawn_run_job(
            job_id="run-claimed",
            runs_root=tmp_path,
            datasets_root=tmp_path / "generated",
            params=RunParams(
                experiment="context_size", dataset=Path("dataset"), output_dir=tmp_path / "out"
            ),
            supervise=False,
            supervision="none",
            stale_minutes=60,
            max_restarts=0,
            poll_seconds=30,
            owner_id="worker-b",
        )
