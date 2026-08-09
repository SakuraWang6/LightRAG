"""Tests for the envelope-based evaluation console (index + routes)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ``lightrag.api.*`` computes module-level state at import time via argparse;
# the guard below mirrors tests/api/routes/test_ollama_input_limits.py.
_original_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
_eval_routes = importlib.import_module("lightrag.api.routers.eval_routes")
_utils_api = importlib.import_module("lightrag.api.utils_api")
sys.argv = _original_argv


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _experiment_envelope(run_id: str = "context-selection-v1") -> dict:
    return {
        "schema_version": "1.0",
        "kind": "experiment",
        "run_id": run_id,
        "created_at": "2026-08-09T00:00:00+00:00",
        "status": "complete",
        "experiment": {
            "id": "context_selection",
            "label": "上下文选择消融",
            "description": "8 种上下文构造方法对比。",
        },
        "environment": {
            "rag_api_url": "http://127.0.0.1:9621",
            "ollama_url": "http://127.0.0.1:11434",
            "storage_dir": "memory_eval_tests/runs/context-selection-v1/rag_storage",
        },
        "baseline": {
            "dataset": "rich-smoke-v1",
            "pages": 12,
            "tier": "smoke",
            "profile": "rich",
            "model": "qwen3:8b",
            "mode": "mix",
            "top_k": 20,
            "num_ctx": 16384,
            "kg": True,
        },
        "variables": [
            {
                "axis": "selection_method",
                "label": "选择方法",
                "arms": [{"arm": "select5", "label": "Select Top-5"}],
            }
        ],
        "methods": [
            {
                "method": "select5",
                "label": "Select Top-5",
                "params": {"candidate_k": 20, "selected_limit": 5},
                "summary": {
                    "cases": 1,
                    "answer_accuracy": 0.8333,
                    "groundedness": 0.75,
                    "hallucination_rate": 0.25,
                    "by_question_type": {},
                },
                "results": [
                    {
                        "question_id": "Q-FACT-00001",
                        "question_group": "FACT",
                        "answer": "9021 QMU",
                        "expected": "9021 QMU",
                        "exact_match": True,
                        "grounded": True,
                    }
                ],
            }
        ],
        "reports": {"report.md": "report.md"},
    }


def _offline_envelope() -> dict:
    return {
        "schema_version": "1.0",
        "kind": "offline",
        "run_id": "rich-smoke-v1",
        "created_at": "2026-08-09T01:00:00+00:00",
        "status": "passed",
        "experiment": {"id": "offline_audit", "label": "离线审计", "description": "无 LLM 审计。"},
        "environment": {},
        "baseline": {"dataset": "rich-smoke-v1", "engine": "native", "top_k": 5},
        "variables": [],
        "methods": [
            {
                "method": "layout",
                "label": "版式审计",
                "params": {},
                "summary": {"passed": False, "position_coverage": 0.0},
                "results": [],
            },
            {
                "method": "offline_summary",
                "label": "离线审计汇总",
                "params": {},
                "summary": {"passed": True, "chunk_sidecar_coverage": 1.0, "cases": 36},
                "results": [],
            }
        ],
        "reports": {},
    }


@pytest.fixture
def runs_tree(tmp_path: Path) -> Path:
    runs = tmp_path / "runs"
    _write(runs / "context-selection-v1" / "run.json", _experiment_envelope())
    _write(
        runs / "context-selection-v1" / "report.md",
        "# 上下文选择消融\n\n| 方法 | Accuracy |\n|---|---:|\n| Select5 | 0.8333 |\n",
    )
    _write(runs / "offline" / "rich-smoke-v1" / "run.json", _offline_envelope())
    # A manifest so dataset conditions resolve.
    _write(
        tmp_path / "memory_data_service" / "generated" / "rich-smoke-v1" / "manifest.json",
        {"dataset_id": "rich-smoke-v1", "pages": 12, "tier": "smoke", "profile": "rich"},
    )
    return runs


def _client(runs_root: Path, api_key: str | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(_eval_routes.create_eval_routes(api_key, runs_root=runs_root))
    return TestClient(app)


pytestmark = pytest.mark.offline

def test_scan_and_load_envelope(runs_tree: Path) -> None:
    from lightrag.api.eval_index import load_run, scan_runs

    runs = scan_runs(runs_tree)
    assert len(runs) == 2
    experiment = next(run for run in runs if run["kind"] == "experiment")
    assert experiment["label"] == "上下文选择消融"
    assert experiment["description"]
    assert experiment["headline"] == {}
    condition_keys = {c["key"] for c in experiment["conditions"]}
    assert {"dataset", "pages", "tier", "model", "num_ctx", "methods"} <= condition_keys

    detail = load_run(runs_tree, "context-selection-v1")
    assert detail["id"] == "context-selection-v1"
    method_artifact = next(a for a in detail["artifacts"] if a["kind"] == "experiment")
    assert method_artifact["table"]["rows"][0]["answer_accuracy"] == pytest.approx(0.8333)
    # Legacy key ``hallucination_rate`` is normalized to ``ungrounded_rate``
    # when the console reads the envelope.
    assert method_artifact["table"]["rows"][0]["ungrounded_rate"] == pytest.approx(0.25)
    assert method_artifact["meta"]["cases"]["rows"][0]["question_id"] == "Q-FACT-00001"
    report = next(a for a in detail["artifacts"] if a["kind"] == "markdown_report")
    assert report["toc"][0]["title"] == "上下文选择消融"
    assert "| Select5 | 0.8333 |" in report["report_md"]

    offline = load_run(runs_tree, "rich-smoke-v1")
    assert offline["kind"] == "offline"
    assert offline["failed_checks"] == ["版式审计"]
    summary = next(a for a in offline["artifacts"] if a["kind"] == "summary")
    assert {m["key"] for m in summary["metrics"]} >= {"passed", "chunk_sidecar_coverage"}


def test_progress_visible(runs_tree: Path) -> None:
    _write(
        runs_tree / "context-selection-v1" / "progress.json",
        {"status": "running", "phase": "question Q-FACT-00005", "done": 5, "total": 36},
    )
    from lightrag.api.eval_index import scan_runs

    run = next(r for r in scan_runs(runs_tree) if r["id"] == "context-selection-v1")
    assert run["progress"]["status"] == "running"
    assert run["progress"]["done"] == 5


def test_routes_require_api_key(runs_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_tree, api_key="secret-key")
    assert client.get("/eval/runs").status_code == 403
    assert client.get("/eval/runs", headers={"X-API-Key": "wrong"}).status_code == 403
    response = client.get("/eval/runs", headers={"X-API-Key": "secret-key"})
    assert response.status_code == 200
    assert len(response.json()["runs"]) == 2


def test_routes_open_when_no_auth(runs_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_tree, api_key=None)
    response = client.get("/eval/runs")
    assert response.status_code == 200
    assert response.json()["runs"][0]["id"]


def test_run_detail_and_not_found(runs_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_tree, api_key="secret-key")
    headers = {"X-API-Key": "secret-key"}
    detail = client.get("/eval/runs/context-selection-v1", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["kind"] == "experiment"
    assert client.get("/eval/runs/does-not-exist", headers=headers).status_code == 404
    assert client.get("/eval/runs/..%2F..%2Fetc%2Fpasswd", headers=headers).status_code == 404


def test_refresh_returns_count(runs_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_tree, api_key="secret-key")
    headers = {"X-API-Key": "secret-key"}
    response = client.post("/eval/refresh", headers=headers)
    assert response.status_code == 200
    assert response.json()["run_count"] == 2


def test_status_reports_framework_version(runs_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_tree, api_key="secret-key")
    response = client.get("/eval/status", headers={"X-API-Key": "secret-key"})
    assert response.status_code == 200
    assert response.json()["eval_framework_version"]


def test_list_runs_pagination(runs_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_tree, api_key="secret-key")
    headers = {"X-API-Key": "secret-key"}
    response = client.get("/eval/runs?limit=1&offset=1", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["limit"] == 1
    assert payload["offset"] == 1
    assert len(payload["runs"]) == 1


def test_eval_routes_degrade_gracefully_when_package_missing(
    runs_tree: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_tree, api_key="secret-key")
    headers = {"X-API-Key": "secret-key"}
    monkeypatch.setattr(_eval_routes, "_EVAL_AVAILABLE", False)
    response = client.get("/eval/runs", headers=headers)
    assert response.status_code == 503
    monkeypatch.setattr(_eval_routes, "_EVAL_AVAILABLE", True)
    assert client.get("/eval/runs", headers=headers).status_code == 200


def test_scan_cache_refreshes_and_ignores_parsed_inputs(runs_tree: Path) -> None:
    from lightrag.api.eval_index import clear_scan_cache, scan_runs

    clear_scan_cache()
    assert len(scan_runs(runs_tree)) == 2
    # A new envelope appears without an explicit invalidation call.
    _write(
        runs_tree / "online" / "new-run" / "run.json",
        {
            "schema_version": "1.0",
            "kind": "online",
            "run_id": "new-run",
            "created_at": "2026-08-09T02:00:00+00:00",
            "status": "complete",
            "experiment": {"id": "x", "label": "New Run", "description": ""},
            "environment": {},
            "baseline": {"dataset": "rich-smoke-v1"},
            "variables": [],
            "methods": [],
            "reports": {},
        },
    )
    assert len(scan_runs(runs_tree)) == 3
    # Anything under inputs/__parsed__ must never be indexed as a run.
    _write(
        runs_tree / "online" / "new-run" / "inputs" / "__parsed__" / "run.json",
        {
            "schema_version": "1.0",
            "kind": "online",
            "run_id": "should-not-appear",
            "created_at": "2026-08-09T03:00:00+00:00",
            "status": "complete",
            "experiment": {"id": "y", "label": "Ghost", "description": ""},
            "environment": {},
            "baseline": {},
            "variables": [],
            "methods": [],
            "reports": {},
        },
    )
    ids = {run["id"] for run in scan_runs(runs_tree)}
    assert "should-not-appear" not in ids
    assert "new-run" in ids
