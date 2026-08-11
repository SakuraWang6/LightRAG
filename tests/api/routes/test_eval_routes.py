"""Product evaluation API contract tests."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_original_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
eval_routes = importlib.import_module("lightrag.api.routers.eval_routes")
utils_api = importlib.import_module("lightrag.api.utils_api")
sys.argv = _original_argv


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    root = tmp_path / "runs"
    root.mkdir()
    return root


@pytest.fixture
def datasets_root(tmp_path: Path) -> Path:
    root = tmp_path / "datasets"
    dataset = root / "sample"
    dataset.mkdir(parents=True)
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "dataset_id": "sample",
                "pages": 1,
                "tier": "smoke",
                "profile": "rich",
                "formats": ["docx"],
                "files": [],
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture
def client(runs_root: Path, datasets_root: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(utils_api, "auth_configured", False)
    app = FastAPI()
    app.include_router(
        eval_routes.create_eval_routes(
            None, runs_root=runs_root, datasets_root=datasets_root
        )
    )
    return TestClient(app)


def test_runs_endpoint_exposes_only_product_evaluation_shape(
    client: TestClient, runs_root: Path
) -> None:
    run = runs_root / "evaluation-one"
    run.mkdir()
    (run / "run.json").write_text(
        json.dumps(
            {
                "schema_version": "3.0",
                "run_id": "evaluation-one",
                "status": "complete",
                "label": "产品测评",
                "dataset": "sample",
                "evaluation": {
                    "id": "end_to_end_baseline",
                    "label": "端到端测评",
                    "description": "完整产品链路",
                },
                "environment": {},
                "baseline": {},
                "methods": [],
                "reports": {},
                "execution_manifest": {"dataset": {"dataset_id": "sample"}},
                "runtime_snapshot": {"status": "captured"},
            }
        ),
        encoding="utf-8",
    )

    response = client.get("/eval/runs")
    assert response.status_code == 200
    payload = response.json()["runs"]
    assert payload[0]["evaluation"] == "end_to_end_baseline"
    assert "kind" not in payload[0]
    assert "experiment" not in payload[0]


def test_create_job_rejects_removed_experiment_field(client: TestClient) -> None:
    response = client.post(
        "/eval/jobs",
        json={"kind": "run", "dataset": "sample", "experiment": "anything"},
    )
    assert response.status_code == 422


def test_create_job_rejects_infrastructure_parameters(client: TestClient) -> None:
    response = client.post(
        "/eval/jobs",
        json={
            "kind": "run",
            "dataset": "sample",
            "params": {"rag_api_url": "http://example.invalid"},
        },
    )
    assert response.status_code == 400
    assert "infrastructure parameters" in response.json()["detail"]
