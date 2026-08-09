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


def test_max_active_jobs_limit(runs_root: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    _write_dataset(tmp_path, "rich-smoke-v1")
    client = _client(runs_root, tmp_path)
    started = eval_jobs._probe_process_start(os.getpid())
    monkeypatch.setattr(
        eval_jobs,
        "start_run_job",
        lambda **kwargs: {
            "id": "run-fake",
            "kind": "run",
            "status": "running",
            "pid": os.getpid(),
            "process_started_at": started,
            "output_dir": str(runs_root / "out"),
            "params": {},
        },
    )
    payload = {
        "kind": "run",
        "experiment": "context_size",
        "dataset": "rich-smoke-v1",
        "params": {},
    }

    monkeypatch.setenv("MEMORY_EVAL_MAX_ACTIVE_JOBS", "1")
    _live_job(runs_root, job_id="run-active", kind="run")
    blocked = client.post("/eval/jobs", json=payload)
    assert blocked.status_code == 409
    assert "active job limit reached" in blocked.json()["detail"]

    monkeypatch.delenv("MEMORY_EVAL_MAX_ACTIVE_JOBS")
    assert client.post("/eval/jobs", json=payload).status_code == 200
