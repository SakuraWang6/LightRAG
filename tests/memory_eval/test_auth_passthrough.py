"""Tests that the harness threads LightRAG API credentials end-to-end."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from memory_eval_tests.experiments.common import (
    ExperimentSpec,
    RunContext,
    capture_environment,
)

pytestmark = pytest.mark.offline


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_online_baseline_passes_auth_and_max_cases(monkeypatch, tmp_path: Path) -> None:
    from memory_eval_tests.experiments.online_baseline import _runner

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs) -> None:
        calls.append(argv)
        output = Path(argv[argv.index("--output") + 1])
        if "retrieval_eval" in argv:
            _write(
                output,
                {
                    "mode": "mix",
                    "top_k": 5,
                    "cases": 1,
                    "average_recall": 1.0,
                    "mrr": 0.5,
                    "results": [],
                },
            )
        else:
            _write(
                output,
                {
                    "mode": "mix",
                    "cases": 1,
                    "answer_accuracy": 1.0,
                    "groundedness": 1.0,
                    "ungrounded_rate": 0.0,
                    "abstention_accuracy": None,
                    "evidence_available": 1.0,
                    "results": [],
                },
            )

    monkeypatch.setattr(
        "memory_eval_tests.experiments.online_baseline.subprocess.run",
        fake_run,
    )
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    spec = ExperimentSpec(
        id="online_baseline",
        label="B",
        description="baseline",
        runner=lambda context: {},
    )
    context = RunContext(
        spec=spec,
        dataset=tmp_path / "dataset",
        output_dir=output_dir,
        baseline={
            "top_k": 5,
            "chunk_top_k": 5,
            "max_total_tokens": 8192,
            "mode": "mix",
            "max_cases": 7,
        },
        environment=capture_environment(
            rag_api_url="http://api.test",
            api_key="k",
            access_token="t",
        ),
        variables=[],
        run_id="r",
    )
    _runner(context)

    assert len(calls) == 2
    for argv in calls:
        assert "--api-key" in argv
        assert argv[argv.index("--api-key") + 1] == "k"
        assert "--access-token" in argv
        assert argv[argv.index("--access-token") + 1] == "t"
        assert "--max-cases" in argv
        assert argv[argv.index("--max-cases") + 1] == "7"


def test_legacy_adapter_namespace_carries_credentials(tmp_path: Path) -> None:
    from memory_eval_tests.experiments.legacy_adapter import namespace_from_context

    spec = ExperimentSpec(
        id="x",
        label="X",
        description="x",
        runner=lambda context: {},
    )
    context = RunContext(
        spec=spec,
        dataset=tmp_path / "dataset",
        output_dir=tmp_path / "run",
        baseline={},
        environment=capture_environment(
            api_key="k",
            access_token="t",
            storage_dir="",
        ),
        variables=[],
        run_id="r",
    )
    args = namespace_from_context(context, artifact_stem="x")
    assert args.api_key == "k"
    assert args.access_token == "t"
    # Default storage is relative to the run output dir, not a hardcoded path.
    assert args.storage_dir == tmp_path / "run" / "rag_storage"


def test_harness_tee_log_captures_runner_output(tmp_path: Path) -> None:
    from memory_eval_tests.experiments.run import _tee_log

    log_path = tmp_path / "run.log"
    with _tee_log(tmp_path):
        print("runner-progress-line")
        sys.stderr.write("runner-error-line\n")
    content = log_path.read_text(encoding="utf-8")
    assert "runner-progress-line" in content
    assert "runner-error-line" in content
