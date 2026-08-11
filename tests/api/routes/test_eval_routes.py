"""Tests for the envelope-based evaluation console (index + routes)."""

from __future__ import annotations

import importlib
import io
import json
import sys
import zipfile
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
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


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
        "experiment": {
            "id": "offline_audit",
            "label": "离线审计",
            "description": "无 LLM 审计。",
        },
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
            },
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
        tmp_path
        / "memory_data_service"
        / "generated"
        / "rich-smoke-v1"
        / "manifest.json",
        {
            "dataset_id": "rich-smoke-v1",
            "pages": 12,
            "tier": "smoke",
            "profile": "rich",
        },
    )
    return runs


def _client(
    runs_root: Path, api_key: str | None = None, datasets_root: Path | None = None
) -> TestClient:
    app = FastAPI()
    app.include_router(
        _eval_routes.create_eval_routes(
            api_key, runs_root=runs_root, datasets_root=datasets_root
        )
    )
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
    assert method_artifact["table"]["rows"][0]["answer_accuracy"] == pytest.approx(
        0.8333
    )
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
    assert {m["key"] for m in summary["metrics"]} >= {
        "passed",
        "chunk_sidecar_coverage",
    }


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


def test_webui_only_advertises_and_launches_isolated_end_to_end(runs_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_tree)
    experiments = client.get("/eval/experiments")
    assert experiments.status_code == 200
    items = {item["id"]: item for item in experiments.json()["experiments"]}
    assert items["end_to_end_baseline"]["webui_launchable"] is True
    assert items["context_size"]["webui_launchable"] is False
    blocked = client.post(
        "/eval/jobs",
        json={
            "kind": "run",
            "experiment": "context_size",
            "dataset": "rich-smoke-v1",
        },
    )
    assert blocked.status_code == 400
    assert "not available in the WebUI" in blocked.json()["detail"]


def test_create_evaluation_passes_custom_name_and_runtime_parameters(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    datasets = tmp_path / "datasets"
    _write(datasets / "sample" / "manifest.json", {"dataset_id": "sample"})
    captured: dict = {}

    def fake_start_run_job(**kwargs):
        captured.update(kwargs)
        return {"id": "run-job", "kind": "run", "status": "pending"}

    monkeypatch.setattr(_eval_routes.eval_jobs, "start_run_job", fake_start_run_job)
    client = _client(tmp_path / "runs", datasets_root=datasets)
    response = client.post(
        "/eval/jobs",
        json={
            "kind": "run",
            "name": "合同文档测评",
            "experiment": "end_to_end_baseline",
            "dataset": "sample",
            "params": {
                "top_k": 8,
                "chunk_top_k": 6,
                "num_ctx": 32768,
                "max_total_tokens": 4096,
                "num_predict": 256,
                "max_cases": 3,
                "engine": "native",
                "kg": False,
            },
        },
    )
    assert response.status_code == 200, response.text
    params = captured["params"]
    assert params.label == "合同文档测评"
    assert params.max_total_tokens == 4096
    assert params.top_k == 8
    assert params.engine == "native"
    assert params.skip_kg is True


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
    assert (
        client.get("/eval/runs/..%2F..%2Fetc%2Fpasswd", headers=headers).status_code
        == 404
    )


def test_refresh_returns_count(runs_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_tree, api_key="secret-key")
    headers = {"X-API-Key": "secret-key"}
    response = client.post("/eval/refresh", headers=headers)
    assert response.status_code == 200
    assert response.json()["run_count"] == 2


def test_import_generated_scenario_rewrites_machine_local_paths(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    scenario = {
        "dataset_id": "portable-smoke",
        "tier": "smoke",
        "pages": 1,
        "profile": "basic",
        "formats": ["docx"],
        "modalities": ["text"],
        "title": "Portable smoke",
        "files": [
            {
                "name": "portable.docx",
                "format": "docx",
                "path": "/another-machine/portable.docx",
                "status": "created",
            }
        ],
    }
    oracle = {"dataset_id": "portable-smoke", "facts": [], "questions": []}
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("portable/manifest.json", json.dumps(scenario))
        archive.writestr("portable/oracle.json", json.dumps(oracle))
        archive.writestr("portable/portable.docx", b"a document")
    payload.seek(0)
    datasets_root = tmp_path / "datasets"
    client = _client(tmp_path / "runs", datasets_root=datasets_root)
    response = client.post(
        "/eval/datasets/import",
        files={"file": ("portable.zip", payload, "application/zip")},
    )
    assert response.status_code == 200, response.text
    stored = json.loads((datasets_root / "portable-smoke" / "manifest.json").read_text())
    assert stored["files"][0]["path"] == str(datasets_root / "portable-smoke" / "portable.docx")
    assert client.get("/eval/datasets").json()["datasets"][0]["dataset_id"] == "portable-smoke"


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

    clear_scan_cache(runs_tree)
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
    clear_scan_cache(runs_tree)
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
    clear_scan_cache(runs_tree)
    ids = {run["id"] for run in scan_runs(runs_tree)}
    assert "should-not-appear" not in ids
    assert "new-run" in ids


def test_scan_cache_ttl_serves_records_without_walking(
    monkeypatch, runs_tree: Path
) -> None:
    import lightrag.api.eval_index as eval_index
    from lightrag.api.eval_index import clear_scan_cache, scan_runs

    clear_scan_cache()
    calls = {"count": 0}
    original = eval_index._envelope_signature

    def counting(runs_root: Path):
        calls["count"] += 1
        return original(runs_root)

    monkeypatch.setattr(eval_index, "_envelope_signature", counting)
    assert len(scan_runs(runs_tree)) == 2
    assert len(scan_runs(runs_tree)) == 2
    assert len(scan_runs(runs_tree)) == 2
    assert calls["count"] == 1
    assert len(scan_runs(runs_tree, force=True)) == 2
    assert calls["count"] == 2


def test_persisted_scan_index_serves_records_without_walking(
    monkeypatch, runs_tree: Path
) -> None:
    import lightrag.api.eval_index as eval_index
    from lightrag.api.eval_index import scan_runs

    eval_index.clear_scan_cache(runs_tree)
    assert len(scan_runs(runs_tree)) == 2
    assert (runs_tree / ".eval_index.json").exists()

    # Cold process: drop the in-memory cache, then the persisted index must
    # serve the list without walking the tree.
    eval_index._scan_cache.pop(runs_tree, None)

    def boom(runs_root: Path):
        raise AssertionError("_envelope_signature should not be called on index hit")

    monkeypatch.setattr(eval_index, "_envelope_signature", boom)
    assert len(scan_runs(runs_tree)) == 2
    monkeypatch.undo()


def test_envelope_write_invalidates_persisted_scan_index(
    tmp_path: Path, monkeypatch
) -> None:
    from memory_eval_tests.experiments.common import write_simple_envelope

    monkeypatch.setenv("MEMORY_EVAL_RUNS_ROOT", str(tmp_path))
    index = tmp_path / ".eval_index.json"
    index.write_text("{}", encoding="utf-8")
    write_simple_envelope(
        tmp_path / "run",
        kind="offline",
        run_id="r",
        experiment={"id": "e", "label": "L", "description": "d"},
        baseline={},
        environment={},
        methods=[],
        status="complete",
    )
    assert not index.exists()


def test_flatten_cases_keeps_full_retrieval_evidence() -> None:
    from lightrag.api.eval_index import _flatten_cases

    methods = [
        {
            "method": "retrieval",
            "results": [
                {
                    "question_id": "Q1",
                    "recall_at_k": 0.5,
                    "hit_fact_ids": [f"FACT-{i}" for i in range(1, 7)],
                    "top_contexts": [
                        {"rank": 1, "file_path": "a.docx", "chunk_count": 2}
                    ],
                    "answer": "x" * 500,
                }
            ],
        }
    ]
    payload = _flatten_cases(methods)
    row = payload["rows"][0]
    assert row["detail"]["hit_fact_ids"] == [f"FACT-{i}" for i in range(1, 7)]
    assert row["detail"]["top_contexts"][0]["rank"] == 1
    assert row["detail"]["answer"] == "x" * 500
    # The table cell stays capped while the detail keeps the full evidence.
    assert row["hit_fact_ids"] != row["detail"]["hit_fact_ids"]
    assert len(row["hit_fact_ids"].split(", ")) == 5


def test_end_to_end_run_indexes_answer_sheet_not_pipeline_methods(
    runs_tree: Path,
) -> None:
    """A regular document evaluation is one run with one review row per question."""
    from lightrag.api.eval_index import clear_scan_cache, load_run

    payload = _experiment_envelope("end-to-end-v1")
    payload["experiment"] = {
        "id": "end_to_end_baseline",
        "label": "端到端测评",
        "description": "单次端到端测评",
    }
    payload["methods"] = [
        {
            "method": "retrieval",
            "label": "检索结果",
            "summary": {"average_recall": 1.0},
            "results": [
                {
                    "question_id": "Q1",
                    "question": "文档标题是什么？",
                    "recall_at_k": 1.0,
                }
            ],
        },
        {
            "method": "answer",
            "label": "回答结果",
            "summary": {"answer_accuracy": 1.0},
            "results": [
                {
                    "question_id": "Q1",
                    "answer": "LightRAG",
                    "expected": "LightRAG",
                    "exact_match": True,
                    "question_type": "事实题",
                }
            ],
        },
    ]
    _write(runs_tree / "end-to-end-v1" / "run.json", payload)
    _write(
        runs_tree / "end-to-end-v1" / "case_trace.json",
        {"cases": [{"question_id": "Q1", "oracle": {"question": "文档标题是什么？"}}]},
    )
    _write(
        runs_tree / "end-to-end-v1" / "report.md",
        "# 隔离端到端基线\n\n旧格式报告",
    )
    _write(
        runs_tree / "end-to-end-v1" / "diagnosis.json",
        {"cause_distribution": {"not_applicable": 1}, "diagnosis_coverage": 1.0},
    )

    clear_scan_cache(runs_tree)
    detail = load_run(runs_tree, "end-to-end-v1")

    assert detail is not None
    assert detail["kind"] == "online"
    assert "methods" not in {condition["key"] for condition in detail["conditions"]}
    assert detail["headline"]["answer_accuracy"]["value"] == 1.0
    assert detail["headline"]["correct_cases"]["value"] == 1
    cases = next(artifact for artifact in detail["artifacts"] if artifact["kind"] == "cases")
    assert cases["table"]["rows"] == [
        {
            "question_id": "Q1",
            "question": "文档标题是什么？",
            "answer": "LightRAG",
            "expected": "LightRAG",
            "exact_match": True,
            "question_type": "事实题",
            "method": "answer",
        }
    ]
    report = next(artifact for artifact in detail["artifacts"] if artifact["kind"] == "markdown_report")
    assert report["title"] == "测评报告"
    assert report["meta"]["uses_llm"] is False
    assert "不调用 LLM" in report["report_md"]


def test_summary_metrics_prefers_canonical_over_legacy_key() -> None:
    from lightrag.api.eval_index import _summary_metrics

    metrics = _summary_metrics(
        [
            {
                "method": "m",
                "summary": {"hallucination_rate": 0.9, "ungrounded_rate": 0.2},
            }
        ]
    )
    by_key = {metric["key"]: metric["value"] for metric in metrics}
    assert by_key["ungrounded_rate"] == 0.2
    assert "hallucination_rate" not in by_key


def test_run_record_surfaces_legacy_and_timing(runs_tree: Path) -> None:
    from lightrag.api.eval_index import load_run

    _write(
        runs_tree / "legacy-run" / "run.json",
        {
            "schema_version": "1.0",
            "kind": "online",
            "run_id": "legacy-run",
            "created_at": "2026-08-09T00:00:00+00:00",
            "started_at": "2026-08-09T00:00:00+00:00",
            "finished_at": "2026-08-09T00:05:00+00:00",
            "restarts": 2,
            "last_restart_resume": True,
            "launch_params": {
                "model": "qwen3:8b",
                "top_k": 5,
                "extra": ["stage=eval"],
            },
            "status": "complete",
            "legacy": True,
            "experiment": {"id": "legacy_online", "label": "Legacy", "description": ""},
            "environment": {},
            "baseline": {},
            "variables": [],
            "methods": [],
            "reports": {},
        },
    )
    detail = load_run(runs_tree, "legacy-run")
    assert detail is not None
    assert detail["legacy"] is True
    assert detail["compatibility_level"] == "legacy"
    assert detail["restarts"] == 2
    assert detail["last_restart_resume"] is True
    assert detail["launch_params"] == {
        "model": "qwen3:8b",
        "top_k": 5,
        "extra": ["stage=eval"],
    }
    assert detail["duration_seconds"] == pytest.approx(300.0)


def test_run_without_trust_contract_is_legacy_even_without_old_marker(
    runs_tree: Path,
) -> None:
    from lightrag.api.eval_index import load_run

    detail = load_run(runs_tree, "context-selection-v1")
    assert detail is not None
    assert detail["legacy"] is True
    assert detail["compatibility_level"] == "legacy"


def test_run_detail_surfaces_structured_failure_and_events(runs_tree: Path) -> None:
    from lightrag.api.eval_index import load_run

    run_dir = runs_tree / "context-selection-v1"
    payload = _experiment_envelope()
    payload.update(
        {
            "failure": {
                "phase": "retrieval",
                "error_type": "TimeoutError",
                "summary": "request timed out",
                "retryable": True,
                "recommendation": "retry after checking the API",
                "log_offset": 2,
            },
            "events_path": "events.jsonl",
        }
    )
    _write(run_dir / "run.json", payload)
    _write(
        run_dir / "events.jsonl",
        '{"timestamp":"2026-08-10T00:00:00+00:00","phase":"retrieval","severity":"error","message":"token=secret"}\n',
    )

    detail = load_run(runs_tree, "context-selection-v1")
    assert detail is not None
    assert detail["failure"]["phase"] == "retrieval"
    assert detail["events_path"] == "events.jsonl"
    assert detail["events"][0]["message"] == "token=configured"


def test_environment_profiles_are_versioned_published_and_execution_ready(
    runs_tree: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_tree, api_key="secret-key")
    headers = {"X-API-Key": "secret-key"}
    configuration = {
        "embedding": {
            "provider": "openai",
            "model": "text-embedding-3-large",
        },
        "query": {"provider": "openai", "model": "gpt-4.1"},
        "parser_engine": "native",
        "storage_backends": {"vector": "QdrantVectorDBStorage"},
        "retrieval_defaults": {"top_k": 5},
        "concurrency": {"max_async_llm": 2},
    }
    first = client.post(
        "/eval/environment-profiles",
        headers=headers,
        json={"name": "OpenAI isolated", "configuration": configuration},
    )
    assert first.status_code == 200
    draft = first.json()
    assert draft["status"] == "draft"
    assert draft["version"] == 1
    profile_id = draft["id"]

    published = client.post(
        f"/eval/environment-profiles/{profile_id}/versions/1/publish", headers=headers
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    detail = client.get(
        f"/eval/environment-profiles/{profile_id}/versions/1", headers=headers
    )
    assert detail.status_code == 200
    assert detail.json()["configuration"]["embedding"]["model"] == "text-embedding-3-large"

    second = client.post(
        "/eval/environment-profiles",
        headers=headers,
        json={"name": "OpenAI isolated", "profile_id": profile_id, "configuration": configuration},
    )
    assert second.status_code == 200
    assert second.json()["version"] == 2
    assert second.json()["status"] == "draft"
    listed = client.get("/eval/environment-profiles", headers=headers)
    assert listed.status_code == 200
    assert [item["version"] for item in listed.json()["profiles"][0]["versions"]] == [1, 2]

    insecure = client.post(
        "/eval/environment-profiles",
        headers=headers,
        json={
            "name": "insecure",
            "configuration": {
                **configuration,
                "embedding": {"provider": "openai", "model": "embed", "api_key": "plain-secret"},
            },
        },
    )
    assert insecure.status_code == 422

    unsafe_endpoint = client.post(
        "/eval/environment-profiles",
        headers=headers,
        json={
            "name": "unsafe endpoint",
            "configuration": {
                **configuration,
                "embedding": {
                    "provider": "openai",
                    "model": "embed",
                    "endpoint": "https://untrusted.example.test/v1",
                },
            },
        },
    )
    assert unsafe_endpoint.status_code == 400


def test_run_log_endpoint_returns_tail(runs_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    client = _client(runs_tree, api_key="secret-key")
    headers = {"X-API-Key": "secret-key"}
    log_path = runs_tree / "context-selection-v1" / "run.log"
    log_path.write_text(
        "\n".join(f"line-{index}" for index in range(1, 6)) + "\n",
        encoding="utf-8",
    )
    response = client.get(
        "/eval/runs/context-selection-v1/log?lines=2", headers=headers
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["exists"] is True
    assert payload["lines"] == ["line-4", "line-5"]
    (runs_tree / "context-selection-v1" / "execution_unit.log").write_text(
        "unit-line-1\nunit-line-2\n", encoding="utf-8"
    )
    combined = client.get(
        "/eval/runs/context-selection-v1/log?lines=4", headers=headers
    )
    assert combined.json()["lines"] == [
        "line-5",
        "--- execution_unit.log ---",
        "unit-line-1",
        "unit-line-2",
    ]
    missing = client.get("/eval/runs/rich-smoke-v1/log", headers=headers)
    # The offline fixture has no run.log; endpoint returns exists=False.
    assert missing.json()["exists"] is False
    assert missing.status_code == 200


def test_run_workspace_endpoint_returns_only_persisted_execution_unit(
    runs_tree: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    unit = {
        "workspace_id": "eval_context_selection",
        "storage_id": "storage-abc",
        "mode": "managed_local",
        "lifecycle_status": "interrupted",
    }
    _write(runs_tree / "context-selection-v1" / "execution_unit.json", unit)
    client = _client(runs_tree, api_key="secret-key")
    response = client.get(
        "/eval/runs/context-selection-v1/workspace", headers={"X-API-Key": "secret-key"}
    )
    assert response.status_code == 200
    assert response.json() == {"execution_unit": unit}
    missing = client.get(
        "/eval/runs/rich-smoke-v1/workspace", headers={"X-API-Key": "secret-key"}
    )
    assert missing.status_code == 404


def test_run_ingestion_endpoint_returns_persisted_receipts(
    runs_tree: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    run_dir = runs_tree / "context-selection-v1"
    _write(run_dir / "ingestion_receipt.json", {"documents": [{"file_name": "a.docx"}]})
    _write(run_dir / "index_receipt.json", {"successful_documents": 1})
    client = _client(runs_tree, api_key="secret-key")
    response = client.get(
        "/eval/runs/context-selection-v1/ingestion", headers={"X-API-Key": "secret-key"}
    )
    assert response.status_code == 200
    assert response.json()["ingestion_receipt"]["documents"][0]["file_name"] == "a.docx"


def test_run_diagnosis_endpoint_returns_trace_and_diagnosis(
    runs_tree: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    run_dir = runs_tree / "context-selection-v1"
    _write(run_dir / "case_trace.json", {"schema_version": "1.0", "cases": []})
    _write(run_dir / "diagnosis.json", {"rule_version": "1.0", "case_count": 0})
    client = _client(runs_tree, api_key="secret-key")
    response = client.get(
        "/eval/runs/context-selection-v1/diagnosis", headers={"X-API-Key": "secret-key"}
    )
    assert response.status_code == 200
    assert response.json()["diagnosis"]["case_count"] == 0


def test_run_diagnosis_csv_export_is_safe_and_structured(runs_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    _write(
        runs_tree / "context-selection-v1" / "diagnosis.json",
        {"cases": [{"question_id": "Q-1", "primary_cause": "retrieval_miss", "evidence": ["recall=0"]}]},
    )
    client = _client(runs_tree, api_key="secret-key")
    response = client.get(
        "/eval/runs/context-selection-v1/diagnosis.csv", headers={"X-API-Key": "secret-key"}
    )
    assert response.status_code == 200
    assert "question_id,question_type" in response.text
    assert "Q-1" in response.text


def test_oracle_upper_bound_endpoint_only_lists_explicitly_linked_runs(
    runs_tree: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    payload = _experiment_envelope("oracle-upper")
    payload["experiment"]["id"] = "oracle_upper_bound"
    payload["diagnoses_run_id"] = "context-selection-v1"
    _write(runs_tree / "oracle-upper" / "run.json", payload)
    client = _client(runs_tree, api_key="secret-key")
    response = client.get(
        "/eval/runs/context-selection-v1/oracle-upper-bounds",
        headers={"X-API-Key": "secret-key"},
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["oracle_upper_bounds"]] == ["oracle-upper"]


def test_frozen_context_endpoint_requires_traced_end_to_end_run(runs_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(_utils_api, "auth_configured", False)
    run_dir = runs_tree / "context-selection-v1"
    payload = _experiment_envelope()
    payload["experiment"]["id"] = "end_to_end_baseline"
    _write(run_dir / "run.json", payload)
    _write(run_dir / "case_trace.json", {"cases": [{"question_id": "Q1", "oracle": {"question": "q", "answer": "a"}, "final_context": {"status": "observed", "content": "ctx", "system_prompt": "sys ctx", "user_query": "q"}}]})
    client = _client(runs_tree, api_key="secret-key")
    response = client.post("/eval/frozen-contexts", headers={"X-API-Key": "secret-key"}, json={"parent_run_id": "context-selection-v1"})
    assert response.status_code == 200
    assert response.json()["case_count"] == 1
