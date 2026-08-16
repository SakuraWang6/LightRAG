"""Unit tests for the read-only recall-lab server helpers."""

from __future__ import annotations

import json

import pytest

from memory_recall_lab.server import RecallLabHandler, _run_summary


def _handler(root):
    class Handler(RecallLabHandler):
        runs_root = root

    return Handler


def test_run_summary_reads_label_and_baseline(tmp_path):
    run = tmp_path / "run-1"
    run.mkdir()
    (run / "run.json").write_text(
        json.dumps(
            {
                "label": "A1 atomic baseline",
                "status": "complete",
                "dataset": "verify-en-20p",
                "baseline": {"mode": "naive", "top_k": 20},
            }
        ),
        encoding="utf-8",
    )
    (run / "recall_report.json").write_text("{}", encoding="utf-8")

    summary = _run_summary(run)
    assert summary["label"] == "A1 atomic baseline"
    assert summary["dataset"] == "verify-en-20p"
    assert summary["baseline"]["top_k"] == 20
    assert summary["has_recall_report"] is True


def test_resolve_run_rejects_paths_outside_root(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(exist_ok=True)
    handler = object.__new__(_handler(tmp_path))
    with pytest.raises(ValueError):
        handler._resolve_run(str(outside))


def test_resolve_run_accepts_relative_path(tmp_path):
    (tmp_path / "run-2").mkdir()
    handler = object.__new__(_handler(tmp_path))
    resolved = handler._resolve_run("run-2")
    assert resolved.name == "run-2"
