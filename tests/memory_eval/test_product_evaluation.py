"""Focused contract tests for the single LightRAG product evaluation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from memory_eval_tests import cli
from memory_eval_tests.artifacts import (
    EvaluationDefinition,
    RunContext,
    read_progress,
    write_envelope,
    write_progress,
)


def test_envelope_has_a_single_evaluation_contract(tmp_path: Path) -> None:
    definition = EvaluationDefinition(
        id="end_to_end_baseline",
        label="端到端测评",
        description="产品链路",
        runner=lambda _: {},
    )
    context = RunContext(
        definition=definition,
        dataset=tmp_path / "missing-dataset",
        output_dir=tmp_path / "evaluation",
        baseline={"mode": "mix"},
        environment={},
        run_id="evaluation-one",
        runtime_snapshot={"status": "unavailable"},
    )
    path = write_envelope(
        context.output_dir,
        context=context,
        status="complete",
        methods=[],
        runs_root=tmp_path,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["evaluation"]["id"] == "end_to_end_baseline"
    assert "kind" not in payload
    assert "experiment" not in payload
    assert "variables" not in payload


def test_progress_is_valid_json_after_each_update(tmp_path: Path) -> None:
    write_progress(tmp_path, status="running", done=1, total=3, phase="ingestion")
    assert read_progress(tmp_path)["phase"] == "ingestion"
    assert not (tmp_path / ".progress.json.tmp").exists()


def test_disabling_kg_requires_vector_query_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "memory_eval_tests.cli",
            "--dataset",
            str(tmp_path / "dataset"),
            "--output-dir",
            str(tmp_path / "run"),
            "--skip-kg",
            "--mode",
            "mix",
        ],
    )
    with pytest.raises(SystemExit, match="2"):
        cli.main()
