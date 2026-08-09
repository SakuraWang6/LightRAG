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

pytestmark = pytest.mark.offline


def test_metric_alias_normalization() -> None:
    summary = normalize_summary(
        {
            "accuracy": 0.8,
            "groundedness": 0.7,
            "abstention_correct": 1.0,
            "by_question_type": {},
        },
        "selector",
    )
    assert summary["answer_accuracy"] == 0.8
    assert summary["abstention_accuracy"] == 1.0
    assert "accuracy" not in summary
    assert "abstention_correct" not in summary
    # Canonical padding: missing keys are null, so all selector runs share columns.
    assert summary["ungrounded_rate"] is None
    assert summary["candidate_recall"] is None


def test_legacy_metric_aliases_map_to_canonical_names() -> None:
    from memory_eval_tests.experiments.common.metrics import normalize_metric_key

    summary = normalize_summary(
        {
            "hallucination_rate": 0.25,
            "citation_accuracy": 0.9,
            "answer_accuracy": 0.75,
        },
        "answer",
    )
    assert summary["ungrounded_rate"] == 0.25
    assert summary["evidence_available"] == 0.9
    assert "hallucination_rate" not in summary
    assert "citation_accuracy" not in summary
    # Direct key normalization mirrors what the console applies at read time.
    assert normalize_metric_key("hallucination_rate") == "ungrounded_rate"
    assert normalize_metric_key("citation_accuracy") == "evidence_available"
    assert normalize_metric_key("ungrounded_rate") == "ungrounded_rate"


def test_capture_environment_credentials(monkeypatch) -> None:
    monkeypatch.delenv("LIGHTRAG_API_KEY", raising=False)
    monkeypatch.delenv("LIGHTRAG_ACCESS_TOKEN", raising=False)
    env = capture_environment()
    assert env.get("api_key") is None
    assert env.get("access_token") is None

    overridden = capture_environment(api_key="k", access_token="t")
    assert overridden["api_key"] == "k"
    assert overridden["access_token"] == "t"

    monkeypatch.setenv("LIGHTRAG_API_KEY", "from-env")
    assert capture_environment()["api_key"] == "from-env"
    # Explicit arguments win over environment defaults.
    assert capture_environment(api_key="cli")["api_key"] == "cli"


def test_sample_evenly_is_deterministic_and_spread() -> None:
    from memory_eval_tests.common.sampling import sample_evenly

    items = list(range(36))
    sampled = sample_evenly(items, 4)
    assert sampled == sample_evenly(items, 4)
    assert sampled == [0, 12, 23, 35]
    # No cap returns everything; a cap at least as large as the input too.
    assert sample_evenly(items, None) == items
    assert sample_evenly(items, 0) == items
    assert sample_evenly(items, 36) == items
    assert sample_evenly(items, 1) == [0]


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
        methods=[
            {
                "method": "m",
                "label": "M",
                "params": {},
                "summary": {"passed": True},
                "results": [],
            }
        ],
        status="passed",
    )
    envelope = json.loads(path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == "1.0"
    assert envelope["kind"] == "offline"
    assert envelope["methods"][0]["summary"]["passed"] is True


def test_envelope_records_started_and_finished(tmp_path: Path) -> None:
    from memory_eval_tests.experiments.common import (
        ExperimentSpec,
        RunContext,
        write_envelope,
    )

    spec = ExperimentSpec(id="x", label="X", description="d", runner=lambda c: {})
    started = "2026-08-09T00:00:00+00:00"
    context = RunContext(
        spec=spec,
        dataset=tmp_path / "dataset",
        output_dir=tmp_path / "run",
        baseline={},
        environment={},
        variables=[],
        run_id="r",
        started_at=started,
    )
    write_envelope(tmp_path / "run", context=context, status="complete", methods=[])
    envelope = json.loads((tmp_path / "run" / "run.json").read_text(encoding="utf-8"))
    assert envelope["started_at"] == started
    assert envelope["finished_at"]

    path = write_simple_envelope(
        tmp_path / "simple",
        kind="online",
        run_id="s",
        experiment={"id": "e", "label": "L", "description": "d"},
        baseline={},
        environment={},
        methods=[],
        status="complete",
        started_at=started,
    )
    simple = json.loads(path.read_text(encoding="utf-8"))
    assert simple["started_at"] == started
    assert simple["finished_at"]


def test_envelope_redacts_credentials(tmp_path: Path) -> None:
    from memory_eval_tests.experiments.common import (
        ExperimentSpec,
        RunContext,
        write_envelope,
        write_simple_envelope,
    )

    secret_environment = {
        "api_key": "super-secret-key",
        "access_token": "super-secret-token",
        "rag_api_url": "http://api.test",
        "storage_dir": "",
    }
    spec = ExperimentSpec(id="x", label="X", description="d", runner=lambda c: {})
    context = RunContext(
        spec=spec,
        dataset=tmp_path / "dataset",
        output_dir=tmp_path / "run",
        baseline={},
        environment=secret_environment,
        variables=[],
        run_id="r",
    )
    write_envelope(tmp_path / "run", context=context, status="complete", methods=[])
    envelope = json.loads((tmp_path / "run" / "run.json").read_text(encoding="utf-8"))
    assert envelope["environment"]["api_key"] == "configured"
    assert envelope["environment"]["access_token"] == "configured"
    assert "super-secret" not in json.dumps(envelope, ensure_ascii=False)
    # The in-memory environment keeps the real credentials for runners.
    assert context.environment["api_key"] == "super-secret-key"

    simple_path = write_simple_envelope(
        tmp_path / "simple",
        kind="online",
        run_id="s",
        experiment={"id": "e", "label": "L", "description": "d"},
        baseline={},
        environment=secret_environment,
        methods=[],
        status="complete",
    )
    simple = json.loads(simple_path.read_text(encoding="utf-8"))
    assert simple["environment"]["api_key"] == "configured"
    assert "super-secret" not in json.dumps(simple, ensure_ascii=False)


def test_environment_without_credentials_keeps_nulls(tmp_path: Path) -> None:
    from memory_eval_tests.experiments.common import (
        ExperimentSpec,
        RunContext,
        write_envelope,
    )

    spec = ExperimentSpec(id="x", label="X", description="d", runner=lambda c: {})
    context = RunContext(
        spec=spec,
        dataset=tmp_path / "dataset",
        output_dir=tmp_path / "run",
        baseline={},
        environment={"api_key": None, "access_token": None, "rag_api_url": "http://x"},
        variables=[],
        run_id="r",
    )
    write_envelope(tmp_path / "run", context=context, status="complete", methods=[])
    envelope = json.loads((tmp_path / "run" / "run.json").read_text(encoding="utf-8"))
    assert envelope["environment"]["api_key"] is None


def test_registry_specs() -> None:
    from memory_eval_tests.experiments.registry import list_specs

    ids = [spec.id for spec in list_specs()]
    assert ids == [
        "context_selection",
        "context_size",
        "structure_ablation",
        "scale",
        "online_baseline",
        "kg_ablation",
        "evidence_selector",
        "relation_selector",
        "table_packing",
        "combined_pipeline",
        "oracle_upper_bound",
        "frozen_prompt_llm_eval",
        "evaluator_recheck",
        "evidence_selector_failure_analysis",
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
        experiment={
            "id": "context_selection",
            "label": "上下文选择消融",
            "description": "d",
        },
        baseline={"dataset": "rich-smoke-v1"},
        environment={},
        methods=[
            {
                "method": "select5",
                "label": "Select Top-5",
                "params": {},
                "summary": {
                    "cases": 36,
                    "answer_accuracy": 0.8333,
                    "groundedness": 0.75,
                },
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
        methods=[
            {
                "method": "offline_summary",
                "label": "汇总",
                "params": {},
                "summary": {"passed": True},
                "results": [],
            }
        ],
        status="passed",
    )
    payload = build_summary(runs)
    assert payload["run_count"] == 2
    markdown = (runs / "SUMMARY.md").read_text(encoding="utf-8")
    assert "上下文选择消融" in markdown
    assert "Select Top-5" in markdown
    assert (runs / "SUMMARY.json").exists()
