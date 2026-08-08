"""Unit tests for the standardized evaluation harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_eval_tests.experiments.common import (
    build_conditions,
    capture_environment,
    context_check,
    normalize_summary,
    write_simple_envelope,
)


def test_metric_alias_normalization() -> None:
    summary = normalize_summary(
        {"accuracy": 0.8, "groundedness": 0.7, "abstention_correct": 1.0, "by_question_type": {}},
        "selector",
    )
    assert summary["answer_accuracy"] == 0.8
    assert summary["abstention_accuracy"] == 1.0
    assert "accuracy" not in summary
    assert "abstention_correct" not in summary
    # Canonical padding: missing keys are null, so all selector runs share columns.
    assert summary["hallucination_rate"] is None
    assert summary["candidate_recall"] is None


def test_context_preflight_overflow() -> None:
    short = "A" * 100
    assert context_check(short, 16384, "arm")["overflow"] is False
    long_prompt = "数" * 20000
    check = context_check(long_prompt, 8192, "arm")
    assert check["overflow"] is True
    assert check["estimated_tokens"] > 8192
    assert check["arm"] == "arm"


def test_conditions_build(tmp_path: Path) -> None:
    conditions = build_conditions(
        capture_environment(rag_api_url="http://127.0.0.1:9621"),
        {"dataset": "rich-smoke-v1", "model": "qwen3:8b", "num_ctx": 16384, "kg": True},
        {"dataset": "rich-smoke-v1", "pages": 12, "tier": "smoke", "profile": "rich"},
        method_count=8,
    )
    by_key = {c["key"]: c for c in conditions}
    assert by_key["pages"]["value"] == "12"
    assert by_key["model"]["value"] == "qwen3:8b"
    assert by_key["num_ctx"]["value"] == "16384"
    assert by_key["kg"]["value"] == "开"
    assert by_key["methods"]["value"] == "8"


def test_envelope_roundtrip(tmp_path: Path) -> None:
    path = write_simple_envelope(
        tmp_path / "run",
        kind="offline",
        run_id="rich-smoke-v1",
        experiment={"id": "offline_audit", "label": "离线审计", "description": "x"},
        baseline={"dataset": "rich-smoke-v1"},
        environment={},
        methods=[{"method": "m", "label": "M", "params": {}, "summary": {"passed": True}, "results": []}],
        status="passed",
    )
    envelope = json.loads(path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == "1.0"
    assert envelope["kind"] == "offline"
    assert envelope["methods"][0]["summary"]["passed"] is True


def test_registry_specs() -> None:
    from memory_eval_tests.experiments.registry import list_specs

    ids = [spec.id for spec in list_specs()]
    assert ids == [
        "context_selection",
        "context_size",
        "structure_ablation",
        "scale",
        "online_baseline",
    ]
    for spec in list_specs():
        assert spec.description
        assert callable(spec.runner)


def test_summary_report(tmp_path: Path) -> None:
    from memory_eval_tests.reporting.summary_report import build_summary

    runs = tmp_path / "runs"
    write_simple_envelope(
        runs / "context-selection-v1",
        kind="experiment",
        run_id="context-selection-v1",
        experiment={"id": "context_selection", "label": "上下文选择消融", "description": "d"},
        baseline={"dataset": "rich-smoke-v1"},
        environment={},
        methods=[
            {
                "method": "select5",
                "label": "Select Top-5",
                "params": {},
                "summary": {"cases": 36, "answer_accuracy": 0.8333, "groundedness": 0.75},
                "results": [],
            }
        ],
        status="complete",
    )
    write_simple_envelope(
        runs / "offline" / "rich-smoke-v1",
        kind="offline",
        run_id="rich-smoke-v1",
        experiment={"id": "offline_audit", "label": "离线审计", "description": "d"},
        baseline={"dataset": "rich-smoke-v1"},
        environment={},
        methods=[{"method": "offline_summary", "label": "汇总", "params": {}, "summary": {"passed": True}, "results": []}],
        status="passed",
    )
    payload = build_summary(runs)
    assert payload["run_count"] == 2
    markdown = (runs / "SUMMARY.md").read_text(encoding="utf-8")
    assert "上下文选择消融" in markdown
    assert "Select Top-5" in markdown
    assert (runs / "SUMMARY.json").exists()
