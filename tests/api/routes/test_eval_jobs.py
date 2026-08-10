"""Route-level tests for the eval workbench (jobs, datasets, templates)."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ``lightrag.api.*`` computes module-level state at import time via argparse.
_original_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
_eval_routes = importlib.import_module("lightrag.api.routers.eval_routes")
_utils_api = importlib.import_module("lightrag.api.utils_api")
from lightrag.api import eval_jobs

sys.argv = _original_argv

pytestmark = pytest.mark.offline


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    root = tmp_path / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _client(runs_root: Path, datasets_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(
        _eval_routes.create_eval_routes(
            None, runs_root=runs_root, datasets_root=datasets_root
        )
    )
    return TestClient(app)


def _write_dataset(datasets_root: Path, dataset_id: str) -> None:
    dataset = datasets_root / dataset_id
    dataset.mkdir(parents=True)
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "dataset_id": dataset_id,
                "tier": "smoke",
                "pages": 1,
                "profile": "basic",
                "formats": ["docx"],
                "modalities": ["text"],
                "title": dataset_id,
                "files": [],
            }
        ),
        encoding="utf-8",
    )


def _live_job(
    runs_root: Path, *, job_id: str, dataset_id: str | None = None, kind: str = "run"
) -> None:
    root = eval_jobs.jobs_root(runs_root)
    root.mkdir(parents=True, exist_ok=True)
    started = eval_jobs._probe_process_start(os.getpid())
    job = {
        "id": job_id,
        "kind": kind,
        "dataset_id": dataset_id,
        "output_dir": str(runs_root / "out"),
        "pid": os.getpid(),
        "process_started_at": started,
        "status": "running",
        "started_at": "2026-08-10T00:00:00+00:00",
    }
    eval_jobs._write_job(root, job)


def test_experiments_include_env_ready(
    runs_root: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    monkeypatch.delenv("LIGHTRAG_PROJECT_OPENAI_API_KEY", raising=False)
    client = _client(runs_root, tmp_path)
    payload = client.get("/eval/experiments").json()["experiments"]
    by_id = {item["id"]: item for item in payload}
    assert by_id["frozen_prompt_llm_eval"]["env_ready"] is False
    assert by_id["context_size"]["env_ready"] is True
    assert by_id["scale"]["extra_schema"] == {"stage": "str"}


def test_run_job_rejects_infra_and_unknown_params(
    runs_root: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    _write_dataset(tmp_path, "rich-smoke-v1")
    client = _client(runs_root, tmp_path)
    infra = client.post(
        "/eval/jobs",
        json={
            "kind": "run",
            "experiment": "context_size",
            "dataset": "rich-smoke-v1",
            "params": {"rag_api_url": "http://evil"},
        },
    )
    assert infra.status_code == 400
    unknown = client.post(
        "/eval/jobs",
        json={
            "kind": "run",
            "experiment": "context_size",
            "dataset": "rich-smoke-v1",
            "params": {"mystery": 1},
        },
    )
    assert unknown.status_code == 400


def test_run_job_requires_env_ready_experiments(
    runs_root: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    monkeypatch.delenv("LIGHTRAG_PROJECT_OPENAI_API_KEY", raising=False)
    _write_dataset(tmp_path, "rich-smoke-v1")
    client = _client(runs_root, tmp_path)
    response = client.post(
        "/eval/jobs",
        json={
            "kind": "run",
            "experiment": "frozen_prompt_llm_eval",
            "dataset": "rich-smoke-v1",
            "params": {},
        },
    )
    assert response.status_code == 400
    assert "LIGHTRAG_PROJECT_OPENAI_API_KEY" in response.json()["detail"]


def test_run_job_starts_and_dataset_delete_is_guarded(
    runs_root: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    _write_dataset(tmp_path, "rich-smoke-v1")
    client = _client(runs_root, tmp_path)
    started = eval_jobs._probe_process_start(os.getpid())

    def fake_start(**kwargs):
        return {
            "id": "run-abc",
            "kind": "run",
            "status": "running",
            "pid": os.getpid(),
            "process_started_at": started,
            "output_dir": str(runs_root / "out"),
            "params": {},
        }

    monkeypatch.setattr(eval_jobs, "start_run_job", fake_start)
    response = client.post(
        "/eval/jobs",
        json={
            "kind": "run",
            "experiment": "context_size",
            "dataset": "rich-smoke-v1",
            "params": {"top_k": 5},
        },
    )
    assert response.status_code == 200
    assert response.json()["id"] == "run-abc"

    _live_job(
        runs_root, job_id="dataset-abc", dataset_id="rich-smoke-v1", kind="dataset"
    )
    deleted = client.delete("/eval/datasets/rich-smoke-v1")
    assert deleted.status_code == 409
    assert "dataset-abc" in deleted.json()["detail"]


def test_templates_sanitize_and_crud(
    runs_root: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_root, tmp_path)
    bad = client.post(
        "/eval/templates",
        json={
            "name": "../evil",
            "experiment": "context_size",
            "dataset": "rich-smoke-v1",
            "params": {},
        },
    )
    assert bad.status_code == 400
    saved = client.post(
        "/eval/templates",
        json={
            "name": "smoke-1",
            "experiment": "context_size",
            "dataset": "rich-smoke-v1",
            "params": {"top_k": 5},
            "supervise": True,
        },
    )
    assert saved.status_code == 200
    items = client.get("/eval/templates").json()["templates"]
    assert items[0]["name"] == "smoke-1"
    assert (runs_root / "templates.json").exists()
    deleted = client.delete("/eval/templates?name=smoke-1")
    assert deleted.status_code == 200
    assert client.get("/eval/templates").json()["templates"] == []


def test_delete_rejects_encoded_path_traversal(
    runs_root: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_root, tmp_path)
    victim = tmp_path.parent / "victim-dir"
    victim.mkdir(exist_ok=True)
    (victim / "keep.txt").write_text("keep", encoding="utf-8")

    assert client.delete("/eval/datasets/%2e%2e").status_code == 400
    assert client.get("/eval/datasets/%2e%2e").status_code == 400
    assert (victim / "keep.txt").exists()
    assert tmp_path.exists()

    _write_dataset(tmp_path, "rich-smoke-v1")
    assert client.delete("/eval/datasets/rich-smoke-v1").status_code == 200
    assert not (tmp_path / "rich-smoke-v1").exists()


def _write_run_envelope(runs_root: Path, run_id: str) -> Path:
    run_dir = runs_root / "online" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "online",
                "run_id": run_id,
                "created_at": "2026-08-10T00:00:00+00:00",
                "status": "complete",
                "experiment": {"id": "x", "label": run_id, "description": ""},
                "environment": {},
                "baseline": {"dataset": "rich-smoke-v1"},
                "variables": [],
                "methods": [],
                "reports": {},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "report.md").write_text("# report", encoding="utf-8")
    return run_dir


def test_delete_run_without_job(runs_root: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_root, tmp_path)
    run_dir = _write_run_envelope(runs_root, "legacy-run")
    assert client.delete("/eval/runs/legacy-run").status_code == 200
    assert not run_dir.exists()
    assert client.delete("/eval/runs/legacy-run").status_code == 404


def test_delete_run_cancels_active_job_and_waits(
    runs_root: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_root, tmp_path)
    run_dir = _write_run_envelope(runs_root, "active-run")
    started = eval_jobs._probe_process_start(os.getpid())
    job = {
        "id": "run-active",
        "kind": "run",
        "output_dir": str(run_dir),
        "pid": os.getpid(),
        "process_started_at": started,
        "status": "running",
        "created_at": "2026-08-10T00:00:00+00:00",
    }
    eval_jobs._write_job(eval_jobs.jobs_root(runs_root), job)
    monkeypatch.setattr(
        eval_jobs,
        "cancel_job",
        lambda **kwargs: {**job, "status": "canceled"},
    )
    monkeypatch.setattr(eval_jobs, "wait_job_exit", lambda job, timeout=35: True)
    response = client.delete("/eval/runs/active-run")
    assert response.status_code == 200
    assert not run_dir.exists()
    assert not (eval_jobs.jobs_root(runs_root) / "run-active").exists()


def test_delete_run_refuses_when_process_still_exiting(
    runs_root: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_root, tmp_path)
    run_dir = _write_run_envelope(runs_root, "exiting-run")
    started = eval_jobs._probe_process_start(os.getpid())
    job = {
        "id": "run-exiting",
        "kind": "run",
        "output_dir": str(run_dir),
        "pid": os.getpid(),
        "process_started_at": started,
        "status": "running",
        "created_at": "2026-08-10T00:00:00+00:00",
    }
    eval_jobs._write_job(eval_jobs.jobs_root(runs_root), job)
    monkeypatch.setattr(
        eval_jobs,
        "cancel_job",
        lambda **kwargs: {**job, "status": "canceled"},
    )
    monkeypatch.setattr(eval_jobs, "wait_job_exit", lambda job, timeout=35: False)
    response = client.delete("/eval/runs/exiting-run")
    assert response.status_code == 409
    assert run_dir.exists()


def test_delete_run_removes_pending_job_record(
    runs_root: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_root, tmp_path)
    run_dir = _write_run_envelope(runs_root, "pending-run")
    job = {
        "id": "run-pending",
        "kind": "run",
        "output_dir": str(run_dir),
        "status": "pending",
        "created_at": "2026-08-10T00:00:00+00:00",
    }
    eval_jobs._write_job(eval_jobs.jobs_root(runs_root), job)
    monkeypatch.setattr(
        eval_jobs,
        "cancel_job",
        lambda **kwargs: {**job, "status": "canceled"},
    )
    response = client.delete("/eval/runs/pending-run")
    assert response.status_code == 200
    assert not run_dir.exists()
    assert not (eval_jobs.jobs_root(runs_root) / "run-pending").exists()


def test_job_validation_gaps(runs_root: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    _write_dataset(tmp_path, "rich-smoke-v1")
    client = _client(runs_root, tmp_path)
    base = {
        "kind": "run",
        "experiment": "context_size",
        "dataset": "rich-smoke-v1",
        "params": {},
    }
    assert (
        client.post("/eval/jobs", json={**base, "output_dir": "/tmp/evil"}).status_code
        == 422
    )
    assert (
        client.post("/eval/jobs", json={**base, "dataset": "../x"}).status_code == 400
    )
    assert (
        client.post("/eval/jobs", json={**base, "supervision": "bogus"}).status_code
        == 422
    )
    assert (
        client.post("/eval/jobs", json={**base, "stale_minutes": 0}).status_code == 422
    )


def test_models_endpoint_filters_embeddings(
    runs_root: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_root, tmp_path)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"models":[{"name":"qwen3:8b"},{"name":"bge-m3:latest"}]}'

    monkeypatch.setattr(
        _eval_routes.urllib.request, "urlopen", lambda *a, **k: FakeResponse()
    )
    payload = client.get("/eval/models").json()
    assert payload["models"] == ["qwen3:8b"]
    assert payload["embedding_filtered"] == ["bge-m3:latest"]

    monkeypatch.setattr(
        _eval_routes.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
    )
    assert client.get("/eval/models").json()["models"] == []


def test_jobs_queue_when_max_active_reached(
    runs_root: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    _write_dataset(tmp_path, "rich-smoke-v1")
    client = _client(runs_root, tmp_path)
    spawned: dict = {}

    def fake_spawn(**kwargs):
        spawned.update(kwargs)
        job_path = eval_jobs.jobs_root(runs_root) / kwargs["job_id"] / "job.json"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["status"] = "running"
        job["pid"] = os.getpid()
        job_path.write_text(json.dumps(job), encoding="utf-8")
        return {
            "id": kwargs["job_id"],
            "kind": "run",
            "status": "running",
            "output_dir": str(runs_root / "out"),
            "params": job.get("params", {}),
        }

    monkeypatch.setattr(eval_jobs, "_spawn_run_job", fake_spawn)
    payload = {
        "kind": "run",
        "experiment": "context_size",
        "dataset": "rich-smoke-v1",
        "params": {},
    }

    monkeypatch.setenv("MEMORY_EVAL_MAX_ACTIVE_JOBS", "1")
    # A running job occupies the single slot; a new job must queue, not 409.
    _live_job(runs_root, job_id="run-active", kind="run")
    queued = client.post("/eval/jobs", json=payload)
    assert queued.status_code == 200
    assert queued.json()["status"] == "pending"
    assert spawned == {}

    # Free the slot; dispatch promotes the oldest pending job.
    active_job = json.loads(
        (eval_jobs.jobs_root(runs_root) / "run-active" / "job.json").read_text(
            encoding="utf-8"
        )
    )
    active_job["pid"] = 2_147_483_647  # dead process -> no longer active
    (eval_jobs.jobs_root(runs_root) / "run-active" / "job.json").write_text(
        json.dumps(active_job), encoding="utf-8"
    )
    eval_jobs._dispatch(runs_root, tmp_path)
    assert spawned.get("job_id") == queued.json()["id"]

    # The promoted job occupies the slot; once it is dead, a new job starts
    # immediately instead of waiting in the queue.
    promoted_job_path = (
        eval_jobs.jobs_root(runs_root) / queued.json()["id"] / "job.json"
    )
    promoted = json.loads(promoted_job_path.read_text(encoding="utf-8"))
    promoted["pid"] = 2_147_483_647
    promoted_job_path.write_text(json.dumps(promoted), encoding="utf-8")
    spawned.clear()
    immediate = client.post("/eval/jobs", json=payload)
    assert immediate.status_code == 200
    assert immediate.json()["status"] == "running"
    assert spawned.get("job_id") == immediate.json()["id"]
