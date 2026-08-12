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


def test_runs_endpoint_uses_dataset_metadata_captured_by_the_run(
    client: TestClient, runs_root: Path
) -> None:
    run = runs_root / "evaluation-snapshot"
    run.mkdir()
    (run / "run.json").write_text(
        json.dumps(
            {
                "schema_version": "3.0",
                "run_id": "evaluation-snapshot",
                "status": "complete",
                "label": "快照测评",
                "dataset": "sample",
                "evaluation": {"id": "end_to_end_baseline", "label": "端到端测评"},
                "environment": {},
                "baseline": {},
                "methods": [],
                "reports": {},
                "execution_manifest": {
                    "dataset": {
                        "dataset_id": "sample",
                        "pages": 99,
                        "tier": "archived",
                        "profile": "frozen",
                        "formats": ["pdf"],
                    }
                },
                "runtime_snapshot": {"status": "captured"},
            }
        ),
        encoding="utf-8",
    )

    payload = client.get("/eval/runs").json()["runs"]
    snapshot = next(item for item in payload if item["id"] == "evaluation-snapshot")
    conditions = {item["key"]: item["value"] for item in snapshot["conditions"]}
    assert conditions["pages"] == "99"
    assert conditions["tier"] == "archived"
    assert conditions["formats"] == "pdf"


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


def _selectable_model_capability() -> dict[str, object]:
    return {
        "provider": "ollama",
        "default_model": "qwen3:8b",
        "parser_engines": ["native"],
        "default_parser_engine": "native",
        "models": ["qwen3:8b"],
        "embedding_filtered": [],
        "selectable_models": ["qwen3:8b"],
        "model_selection": "selectable",
        "configuration_error": None,
    }


def test_create_job_accepts_boolean_vlm(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        eval_routes,
        "_evaluation_model_capability",
        _selectable_model_capability,
    )
    captured: dict[str, object] = {}

    def start_run_job(**kwargs):
        captured.update(kwargs)
        return {"id": "run-job", "status": "pending"}

    monkeypatch.setattr(eval_routes.eval_jobs, "start_run_job", start_run_job)
    response = client.post(
        "/eval/jobs",
        json={"kind": "run", "dataset": "sample", "params": {"vlm": True}},
    )
    assert response.status_code == 200
    assert captured["params"].vlm is True


def test_create_job_defaults_vlm_to_auto(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        eval_routes,
        "_evaluation_model_capability",
        _selectable_model_capability,
    )
    captured: dict[str, object] = {}

    def start_run_job(**kwargs):
        captured.update(kwargs)
        return {"id": "run-job", "status": "pending"}

    monkeypatch.setattr(eval_routes.eval_jobs, "start_run_job", start_run_job)
    response = client.post(
        "/eval/jobs", json={"kind": "run", "dataset": "sample"}
    )
    assert response.status_code == 200
    assert captured["params"].vlm is None


def test_create_job_rejects_non_boolean_vlm(client: TestClient) -> None:
    response = client.post(
        "/eval/jobs",
        json={
            "kind": "run",
            "dataset": "sample",
            "params": {"vlm": "yes"},
        },
    )
    assert response.status_code == 400
    assert "vlm" in response.json()["detail"]


def test_dataset_job_uses_a_business_name_not_a_user_supplied_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def start_dataset_job(**kwargs):
        captured.update(kwargs)
        return {"id": "dataset-job", "status": "pending"}

    monkeypatch.setattr(eval_routes.eval_jobs, "start_dataset_job", start_dataset_job)
    response = client.post(
        "/eval/jobs",
        json={
            "kind": "dataset",
            "dataset_create": {
                "display_name": "中文检索质量测评",
                "language": "zh",
            },
        },
    )

    assert response.status_code == 200
    assert captured["dataset_id"] is None
    assert captured["display_name"] == "中文检索质量测评"
    assert captured["language"] == "zh"


def test_dataset_job_accepts_legacy_title_during_webui_rollout(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def start_dataset_job(**kwargs):
        captured.update(kwargs)
        return {"id": "dataset-job", "status": "pending"}

    monkeypatch.setattr(eval_routes.eval_jobs, "start_dataset_job", start_dataset_job)
    response = client.post(
        "/eval/jobs",
        json={"kind": "dataset", "dataset_create": {"title": "旧版浏览器名称"}},
    )

    assert response.status_code == 200
    assert captured["display_name"] == "旧版浏览器名称"
