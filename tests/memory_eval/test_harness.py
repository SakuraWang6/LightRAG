"""Unit tests for the standardized evaluation harness."""

from __future__ import annotations

import json
import sys
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


def test_completed_case_count_uses_answer_rows_for_end_to_end_runs() -> None:
    from memory_eval_tests.experiments.run import _completed_case_count

    assert _completed_case_count(
        [
            {"method": "retrieval", "results": [{"question_id": "Q-1"}]},
            {"method": "answer", "results": [{"question_id": "Q-1"}]},
        ]
    ) == 1


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
    assert envelope["schema_version"] == "2.0"
    assert envelope["kind"] == "offline"
    assert envelope["methods"][0]["summary"]["passed"] is True
    assert envelope["compatibility_level"] == "current"
    assert envelope["execution_manifest"]["dataset"]["dataset_id"]["value"] == "unknown"


def test_execution_manifest_fingerprints_inputs_and_is_immutable(tmp_path: Path) -> None:
    from memory_eval_tests.experiments.common import (
        ExperimentSpec,
        RunContext,
        build_execution_manifest,
        write_envelope,
    )

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "source.docx").write_bytes(b"document bytes")
    (dataset / "oracle.json").write_text('{"facts": []}', encoding="utf-8")
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "dataset_id": "sample-v1",
                "oracle_file": "oracle.json",
                "generator_version": "gen-1",
                "template_version": "template-2",
                "random_seed": 7,
                "files": [
                    {"name": "source.docx", "format": "docx", "status": "created"}
                ],
            }
        ),
        encoding="utf-8",
    )
    spec = ExperimentSpec(id="context_size", label="X", description="d", runner=lambda c: {})
    context = RunContext(
        spec=spec,
        dataset=dataset,
        output_dir=tmp_path / "run",
        baseline={"top_k": 5},
        environment={},
        variables=[],
        run_id="run-1",
        started_at="2026-08-10T00:00:00+00:00",
        execution_manifest=build_execution_manifest(
            dataset=dataset,
            experiment_id=spec.id,
            experiment_type=spec.kind,
            parameters={"top_k": 5},
            parameter_sources={"top_k": "user"},
            started_at="2026-08-10T00:00:00+00:00",
        ),
    )
    write_envelope(context.output_dir, context=context, status="running", methods=[])
    first = json.loads((context.output_dir / "run.json").read_text(encoding="utf-8"))
    assert len(first["execution_manifest"]["dataset"]["manifest_sha256"]) == 64
    assert len(first["execution_manifest"]["dataset"]["oracle_sha256"]) == 64
    assert first["execution_manifest"]["dataset"]["document_files"][0]["sha256"]
    assert first["execution_manifest"]["parameters"]["top_k"] == {
        "value": 5,
        "source": "user",
    }

    context.execution_manifest["parameters"]["top_k"] = {"value": 99, "source": "user"}
    write_envelope(context.output_dir, context=context, status="complete", methods=[])
    final = json.loads((context.output_dir / "run.json").read_text(encoding="utf-8"))
    assert final["execution_manifest"] == first["execution_manifest"]


def test_runtime_snapshot_uses_authenticated_health_and_flags_model_mismatch(
    tmp_path: Path, monkeypatch
) -> None:
    from memory_eval_tests.experiments.common import (
        capture_runtime_snapshot,
        write_simple_envelope,
    )
    import memory_eval_tests.experiments.common.envelope as envelope_module

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "core_version": "1.2.3",
                    "api_version": "1.2.3",
                    "configuration": {
                        "llm_binding": "openai",
                        "llm_model": "actual-model",
                        "llm_binding_host": "https://key:secret@models.test/v1?token=nope",
                        "embedding_binding": "openai",
                        "embedding_model": "embed-1",
                        "embedding_binding_host": "https://embed.test/v1",
                        "vlm_process_enable": False,
                        "enable_rerank": False,
                        "workspace": "evaluated-workspace",
                        "kv_storage": "JsonKVStorage",
                        "doc_status_storage": "JsonDocStatusStorage",
                        "graph_storage": "NetworkXStorage",
                        "vector_storage": "NanoVectorDBStorage",
                        "parser_routing": "docx:native",
                    },
                }
            ).encode("utf-8")

    captured = {}

    def _urlopen(request, timeout):
        captured["headers"] = dict(request.headers)
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(envelope_module.urllib.request, "urlopen", _urlopen)
    snapshot = capture_runtime_snapshot(
        rag_api_url="https://rag.test/api",
        api_key="api-secret",
        access_token="token-secret",
    )
    assert snapshot["status"] == "captured"
    assert snapshot["llm"]["model"] == "actual-model"
    assert snapshot["llm"]["endpoint"] == "https://models.test/v1"
    assert snapshot["storage"]["workspace"] == "evaluated-workspace"
    assert captured["headers"]["X-api-key"] == "api-secret"
    assert captured["headers"]["Authorization"] == "Bearer token-secret"

    monkeypatch.setattr(envelope_module, "capture_runtime_snapshot", lambda **_kwargs: snapshot)
    path = write_simple_envelope(
        tmp_path / "run",
        kind="online",
        run_id="run-1",
        experiment={"id": "online_answer"},
        baseline={"model": "declared-model"},
        environment={"rag_api_url": "https://rag.test/api", "api_key": "api-secret"},
        methods=[],
        status="complete",
    )
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["declared_model"] == "declared-model"
    assert persisted["effective_model"] == "actual-model"
    assert persisted["configuration_mismatch"] is True
    assert "api-secret" not in json.dumps(persisted)
    assert "token-secret" not in json.dumps(persisted)


def test_failed_envelope_preserves_structured_failure_and_append_only_events(
    tmp_path: Path,
) -> None:
    from memory_eval_tests.experiments.common import (
        append_run_event,
        build_failure,
        write_simple_envelope,
    )

    run_dir = tmp_path / "run"
    offset = append_run_event(
        run_dir,
        phase="upload",
        severity="error",
        message="Api_Key=super-secret upload failed",
        error_type="ConnectionError",
    )
    failure = build_failure(
        phase="upload",
        error="access_token=super-secret upload failed",
        retryable=True,
        recommendation="check the evaluated service and retry",
        log_offset=offset,
    )
    write_simple_envelope(
        run_dir,
        kind="offline",
        run_id="failed-run",
        experiment={"id": "offline_audit"},
        baseline={},
        environment={},
        methods=[],
        status="failed",
        extra={"failure": failure},
    )
    first = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert first["events_path"] == "events.jsonl"
    assert first["failure"]["phase"] == "upload"
    assert first["failure"]["retryable"] is True
    assert "super-secret" not in events
    assert "super-secret" not in json.dumps(first)

    write_simple_envelope(
        run_dir,
        kind="offline",
        run_id="failed-run",
        experiment={"id": "offline_audit"},
        baseline={},
        environment={},
        methods=[],
        status="failed",
        extra={"failure": {}},
    )
    final = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert final["failure"] == first["failure"]


def test_run_context_progress_records_a_timeline_event(tmp_path: Path) -> None:
    from memory_eval_tests.experiments.common import ExperimentSpec, RunContext

    context = RunContext(
        spec=ExperimentSpec(id="x", label="X", description="d", runner=lambda _c: {}),
        dataset=tmp_path / "dataset",
        output_dir=tmp_path / "run",
        baseline={},
        environment={},
        variables=[],
        run_id="run-1",
    )
    context.progress("running", 2, 7, "retrieval", "evaluating retrieval")

    progress = json.loads((context.output_dir / "progress.json").read_text())
    events = [json.loads(line) for line in (context.output_dir / "events.jsonl").read_text().splitlines()]
    assert progress["done"] == 2
    assert len(events) == 1
    assert events[0]["phase"] == "retrieval"
    assert events[0]["severity"] == "info"
    assert events[0]["message"] == "evaluating retrieval"


def test_harness_exception_writes_failure_envelope_and_event(
    tmp_path: Path, monkeypatch
) -> None:
    import memory_eval_tests.experiments.run as harness
    from memory_eval_tests.experiments.common import ExperimentSpec

    def _fail(_context):
        raise RuntimeError("token=never-persist-this")

    spec = ExperimentSpec(
        id="failing_experiment",
        label="Failing",
        description="test failure capture",
        runner=_fail,
    )
    run_dir = tmp_path / "run"
    monkeypatch.setattr(harness, "get_spec", lambda _id: spec)
    monkeypatch.setattr(
        harness,
        "capture_runtime_snapshot",
        lambda **_kwargs: {"status": "unavailable", "reason": "test"},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run.py",
            "--experiment",
            spec.id,
            "--dataset",
            str(tmp_path / "dataset"),
            "--output-dir",
            str(run_dir),
        ],
    )
    with pytest.raises(RuntimeError, match="never-persist-this"):
        harness.main()

    envelope = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert envelope["status"] == "failed"
    assert envelope["failure"]["phase"] == "execution"
    assert envelope["failure"]["error_type"] == "RuntimeError"
    assert "never-persist-this" not in json.dumps(envelope)
    assert "never-persist-this" not in events
    assert "never-persist-this" not in (run_dir / "run.log").read_text(encoding="utf-8")


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
        restarts=2,
        last_restart_resume=True,
    )
    write_envelope(tmp_path / "run", context=context, status="complete", methods=[])
    envelope = json.loads((tmp_path / "run" / "run.json").read_text(encoding="utf-8"))
    assert envelope["started_at"] == started
    assert envelope["finished_at"]
    assert envelope["restarts"] == 2
    assert envelope["last_restart_resume"] is True

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
        restarts=3,
    )
    simple = json.loads(path.read_text(encoding="utf-8"))
    assert simple["started_at"] == started
    assert simple["finished_at"]
    assert simple["restarts"] == 3


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


def test_redact_launch_extra_masks_sensitive_keys() -> None:
    from memory_eval_tests.experiments.common import redact_launch_extra

    redacted = redact_launch_extra(
        ["api_key=super-secret", "selected_limit=5", "MY_TOKEN=abc", "stage=eval"]
    )
    assert redacted == [
        "api_key=configured",
        "selected_limit=5",
        "MY_TOKEN=configured",
        "stage=eval",
    ]


def test_registry_specs() -> None:
    from memory_eval_tests.experiments.registry import list_specs

    ids = [spec.id for spec in list_specs()]
    assert ids == [
        "context_selection",
        "context_size",
        "custom_arms",
        "structure_ablation",
        "scale",
        "end_to_end_baseline",
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
