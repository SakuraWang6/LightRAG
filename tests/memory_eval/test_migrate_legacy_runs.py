"""Tests for the legacy online run migration tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_eval_tests.tools.migrate_legacy_runs import migrate_legacy_runs


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _legacy_tree(tmp_path: Path) -> tuple[Path, Path]:
    runs = tmp_path / "runs"
    run_dir = runs / "online" / "rich-smoke-v1-local-qwen8b-skipkg"
    _write_json(
        run_dir / "retrieval_mix_top5.json",
        {
            "mode": "mix",
            "top_k": 5,
            "cases": 34,
            "average_recall": 0.9411,
            "mrr": 0.9411,
        },
    )
    _write_json(
        run_dir / "answer_mix.json",
        {"mode": "mix", "cases": 36, "answer_accuracy": 0.8611, "groundedness": 0.75},
    )
    _write_json(run_dir / "prompts_mix.json", {"prompts": []})
    _write_json(run_dir / "api_preflight.json", {})
    (run_dir / "online_report.md").write_text("# Online", encoding="utf-8")

    # An online run with no result artifacts at all.
    (runs / "online" / "rich-smoke-v1-native-teP" / "rag_storage").mkdir(parents=True)

    generated = tmp_path / "generated"
    (generated / "rich-smoke-v1").mkdir(parents=True)
    return runs, generated


pytestmark = pytest.mark.offline


def test_dry_run_does_not_write_envelopes(tmp_path: Path) -> None:
    runs, generated = _legacy_tree(tmp_path)
    summary = migrate_legacy_runs(
        runs_root=runs, generated_root=generated, dry_run=True
    )
    assert summary["count"] == 2
    assert summary["dry_run"] is True
    assert not list(runs.rglob("run.json"))


def test_migrates_legacy_run_with_marker_and_methods(tmp_path: Path) -> None:
    runs, generated = _legacy_tree(tmp_path)
    migrate_legacy_runs(runs_root=runs, generated_root=generated)

    envelope = json.loads(
        (runs / "online" / "rich-smoke-v1-local-qwen8b-skipkg" / "run.json").read_text(
            encoding="utf-8"
        )
    )
    assert envelope["kind"] == "online"
    assert envelope["status"] == "complete"
    assert envelope["legacy"] is True
    assert envelope["metric_semantics"] == "legacy"
    assert envelope["baseline"]["dataset"] == "rich-smoke-v1"
    assert envelope["baseline"]["mode"] == "mix"
    methods = {m["method"]: m for m in envelope["methods"]}
    assert set(methods) == {"retrieval", "answer"}
    assert methods["retrieval"]["summary"]["average_recall"] == pytest.approx(0.9411)
    assert methods["answer"]["summary"]["answer_accuracy"] == pytest.approx(0.8611)
    assert envelope["reports"]["report.md"] == "online_report.md"


def test_run_without_artifacts_gets_incomplete_envelope(tmp_path: Path) -> None:
    runs, generated = _legacy_tree(tmp_path)
    migrate_legacy_runs(runs_root=runs, generated_root=generated)
    envelope = json.loads(
        (runs / "online" / "rich-smoke-v1-native-teP" / "run.json").read_text(
            encoding="utf-8"
        )
    )
    assert envelope["status"] == "incomplete"
    assert envelope["legacy"] is True
    assert envelope["methods"] == []


def test_existing_envelopes_are_not_touched(tmp_path: Path) -> None:
    runs, generated = _legacy_tree(tmp_path)
    current = runs / "online" / "rich-smoke-v1"
    _write_json(
        current / "run.json",
        {"schema_version": "1.0", "kind": "online", "run_id": "rich-smoke-v1"},
    )
    migrate_legacy_runs(runs_root=runs, generated_root=generated)
    assert (
        json.loads((current / "run.json").read_text(encoding="utf-8"))["run_id"]
        == "rich-smoke-v1"
    )
    assert not (runs / "online" / "rich-smoke-v1" / "analysis.json").exists()
