"""Tests for the read-only evaluation console (index + routes)."""

from __future__ import annotations

import json
import importlib
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lightrag.api.eval_index import build_index, load_run, load_runs

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
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def runs_tree(tmp_path: Path) -> Path:
    runs = tmp_path / "runs"

    _write(
        runs / "offline" / "rich-smoke-v1" / "summary.json",
        {
            "dataset_id": "rich-smoke-v1",
            "engine": "native",
            "top_k": 5,
            "chunk_token_size": 800,
            "passed": False,
            "reports": {"integrity": "integrity.json"},
        },
    )
    _write(
        runs / "offline" / "rich-smoke-v1" / "retrieval_sidecar.json",
        {
            "backend": "sidecar",
            "mode": "sidecar",
            "top_k": 5,
            "cases": 34,
            "average_recall": 0.9852941176470589,
            "mrr": 0.6147058823529412,
            "results": [
                {
                    "question_id": "Q-FACT-00001",
                    "recall_at_k": 1.0,
                    "reciprocal_rank": 1.0,
                    "expected": ["FACT-00001"],
                    "ranked_hits": ["FACT-00001"],
                }
            ],
        },
    )
    _write(
        runs / "offline" / "rich-smoke-v1" / "report.md",
        "# Memory Evaluation Report\n\n| K | Recall |\n|---|---|\n| 5 | 0.985 |\n",
    )
    _write(
        runs / "evidence-selector-v1" / "evidence_selector_results.json",
        {
            "dataset": "memory_data_service/generated/rich-smoke-v1",
            "model": "qwen3:8b",
            "status": "complete",
            "methods": [
                {
                    "method": "select5",
                    "label": "Select5 (saved)",
                    "summary": {"cases": 36, "answer_accuracy": 0.8333, "groundedness": 0.75},
                }
            ],
        },
    )
    _write(
        runs / "online" / "rich-smoke-v1-local-qwen8b-kg" / "answer_mix_top5_ctx8192.json",
        {
            "mode": "mix",
            "top_k": 5,
            "cases": 36,
            "answer_accuracy": 0.8056,
            "groundedness": 0.75,
            "hallucination_rate": 0.25,
            "results": [
                {
                    "question_id": "Q-FACT-00001",
                    "exact_match": True,
                    "grounded": True,
                    "answer": "9021 QMU",
                    "expected": "9021 QMU",
                }
            ],
        },
    )
    # Raw LightRAG storage must be ignored by the scanner.
    _write(
        runs / "online" / "rich-smoke-v1-local-qwen8b-kg" / "rag_storage" / "kv_store_text_chunks.json",
        {"doc-1-chunk-000": {"tokens": 828, "content": "secret chunk body"}},
    )
    return runs


def _client(runs_root: Path, api_key: str | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(_eval_routes.create_eval_routes(api_key, runs_root=runs_root))
    return TestClient(app)


def test_build_index_normalizes_runs(runs_tree: Path) -> None:
    summary = build_index(
        runs_tree,
        runs_tree / ".eval_index.sqlite3",
        known_datasets=["rich-smoke-v1"],
    )
    assert summary["run_count"] == 3
    # summary/retrieval/md/experiment-results/answer; rag_storage ignored.
    assert summary["file_count"] == 5

    runs = load_runs(runs_tree / ".eval_index.sqlite3")
    by_id = {r["id"]: r for r in runs}
    assert set(by_id) == {"offline/rich-smoke-v1", "evidence-selector-v1", "online/rich-smoke-v1-local-qwen8b-kg"}
    assert by_id["offline/rich-smoke-v1"]["kind"] == "offline"
    assert by_id["evidence-selector-v1"]["kind"] == "experiment"
    assert by_id["online/rich-smoke-v1-local-qwen8b-kg"]["dataset"] == "rich-smoke-v1"
    # Headline picks retrieval recall + pass flag for offline runs.
    offline_headline = by_id["offline/rich-smoke-v1"]["headline"]
    assert offline_headline["passed"]["value"] is False
    assert offline_headline["average_recall"]["value"] == pytest.approx(0.9852941176470589)

    offline = load_run(runs_tree / ".eval_index.sqlite3", "offline/rich-smoke-v1")
    kinds = {a["kind"] for a in offline["artifacts"]}
    assert {"offline_summary", "retrieval", "markdown_report"} <= kinds
    assert {"dataset", "engine"} <= {c["key"] for c in offline["conditions"]}
    dataset_condition = next(c for c in offline["conditions"] if c["key"] == "dataset")
    assert dataset_condition["value"] == "rich-smoke-v1"
    report = next(a for a in offline["artifacts"] if a["kind"] == "markdown_report")
    assert "Memory Evaluation Report" in report["report_md"]
    assert "| 5 | 0.985 |" in report["report_md"]
    assert report["toc"][0] == {"level": 1, "title": "Memory Evaluation Report"}
    retrieval = next(a for a in offline["artifacts"] if a["kind"] == "retrieval")
    assert retrieval["table"]["rows"][0]["question_id"] == "Q-FACT-00001"

    experiment = load_run(runs_tree / ".eval_index.sqlite3", "evidence-selector-v1")
    method_table = next(a for a in experiment["artifacts"] if a["kind"] == "experiment")["table"]
    assert method_table["rows"][0]["answer_accuracy"] == pytest.approx(0.8333)
    assert method_table["columns"][0]["key"] == "method"
    # Experiments must not show ambiguous headline metric cards.
    assert experiment["headline"] == {}
    assert {"model", "methods"} <= {c["key"] for c in experiment["conditions"]}

    online = load_run(runs_tree / ".eval_index.sqlite3", "online/rich-smoke-v1-local-qwen8b-kg")
    answer = next(a for a in online["artifacts"] if a["kind"] == "answer")
    answer_accuracy = next(m for m in answer["metrics"] if m["key"] == "answer_accuracy")
    assert answer_accuracy["value"] == pytest.approx(0.8056)
    assert answer["table"]["rows"][0]["answer"] == "9021 QMU"


def test_routes_require_api_key(runs_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_tree, api_key="secret-key")
    assert client.get("/eval/runs").status_code == 403
    assert client.get("/eval/runs", headers={"X-API-Key": "wrong"}).status_code == 403
    response = client.get("/eval/runs", headers={"X-API-Key": "secret-key"})
    assert response.status_code == 200
    assert len(response.json()["runs"]) == 3


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
    detail = client.get("/eval/runs/offline/rich-smoke-v1", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["kind"] == "offline"
    assert client.get("/eval/runs/offline/does-not-exist", headers=headers).status_code == 404
    # Path traversal is treated as an unknown run id, never a filesystem path.
    assert client.get("/eval/runs/..%2F..%2Fetc%2Fpasswd", headers=headers).status_code == 404


def test_refresh_picks_up_new_files(runs_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_tree, api_key="secret-key")
    headers = {"X-API-Key": "secret-key"}
    assert len(client.get("/eval/runs", headers=headers).json()["runs"]) == 3
    _write(
        runs_tree / "oracle-upper-bound-v1" / "oracle_upper_bound_results.json",
        {
            "dataset": "memory_data_service/generated/rich-smoke-v1",
            "status": "complete",
            "arms": [{"arm": "oracle_text", "summary": {"answer_accuracy": 0.9722}}],
        },
    )
    refresh = client.post("/eval/refresh", headers=headers)
    assert refresh.status_code == 200
    assert refresh.json()["run_count"] == 4
    assert len(client.get("/eval/runs", headers=headers).json()["runs"]) == 4
