"""Tests for report-kind envelopes and the baseline/regression data layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_eval_tests.reporting.baseline import build_baseline_table, render_markdown

pytestmark = pytest.mark.offline


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_comparison_report_writes_report_envelope(tmp_path: Path) -> None:
    from lightrag.api.eval_index import load_run
    from memory_eval_tests.reporting.comparison_report import main as comparison_main

    runs = tmp_path / "runs"
    _write_json(
        runs / "a.json", {"mode": "mix", "answer_accuracy": 0.8, "average_recall": 0.9}
    )
    _write_json(
        runs / "b.json", {"mode": "mix", "answer_accuracy": 0.7, "average_recall": 0.8}
    )
    output = runs / "reports" / "comparison_report.md"

    comparison_main(
        [str(runs / "a.json"), str(runs / "b.json"), "--output", str(output)]
    )

    envelope = json.loads((runs / "reports" / "run.json").read_text(encoding="utf-8"))
    assert envelope["kind"] == "report"
    assert envelope["report_type"] == "comparison"
    assert envelope["reports"]["report.md"] == "comparison_report.md"
    detail = load_run(runs, "comparison_report")
    assert detail is not None
    assert any(
        artifact["kind"] == "markdown_report" for artifact in detail["artifacts"]
    )


def test_report_envelope_can_be_disabled(tmp_path: Path) -> None:
    from memory_eval_tests.reporting.comparison_report import main as comparison_main

    runs = tmp_path / "runs"
    _write_json(runs / "a.json", {"answer_accuracy": 0.8})
    output = runs / "reports" / "comparison_report.md"
    comparison_main([str(runs / "a.json"), "--output", str(output), "--no-envelope"])
    assert not (runs / "reports" / "run.json").exists()


def test_baseline_table_groups_repeats_and_marks_significance() -> None:
    records = [
        {
            "id": "a1",
            "kind": "experiment",
            "label": "E",
            "dataset": "d",
            "updated_at": "2026-08-09T00:00:00+00:00",
            "metrics": {"answer_accuracy": 0.8},
        },
        {
            "id": "a2",
            "kind": "experiment",
            "label": "E",
            "dataset": "d",
            "updated_at": "2026-08-09T01:00:00+00:00",
            "metrics": {"answer_accuracy": 0.9},
        },
        {
            "id": "a3",
            "kind": "experiment",
            "label": "E",
            "dataset": "d",
            "updated_at": "2026-08-09T02:00:00+00:00",
            "metrics": {"answer_accuracy": 0.85},
        },
        {
            "id": "b1",
            "kind": "experiment",
            "label": "E",
            "dataset": "d",
            "updated_at": "2026-08-08T00:00:00+00:00",
            "metrics": {"answer_accuracy": 0.5},
        },
        {
            "id": "c1",
            "kind": "experiment",
            "label": "E",
            "dataset": "other-d",
            "updated_at": "2026-08-08T00:00:00+00:00",
            "metrics": {"answer_accuracy": 0.6},
        },
    ]
    payload = build_baseline_table(records, baseline_run_id="b1")
    assert payload["baseline_run_id"] == "b1"
    repeated = next(
        row
        for row in payload["groups"]
        if row["label"] == "E" and row["dataset"] == "d"
    )
    assert repeated["n"] == 4
    assert repeated["mean"] == pytest.approx(0.7625)
    assert repeated["delta"] == pytest.approx(0.2625)
    assert repeated["significance"] == "差异较大（启发式）"
    single = next(row for row in payload["groups"] if row["n"] == 1)
    assert single["significance"] == "样本不足"
    assert "基线/回归对比报告" in render_markdown(payload)


def test_baseline_defaults_to_newest_run() -> None:
    records = [
        {
            "id": "old",
            "kind": "online",
            "label": "R",
            "dataset": "d",
            "updated_at": "2026-08-01T00:00:00+00:00",
            "metrics": {"mrr": 0.5},
        },
        {
            "id": "new",
            "kind": "online",
            "label": "R",
            "dataset": "d",
            "updated_at": "2026-08-09T00:00:00+00:00",
            "metrics": {"mrr": 0.7},
        },
    ]
    payload = build_baseline_table(records)
    assert payload["baseline_run_id"] == "new"
