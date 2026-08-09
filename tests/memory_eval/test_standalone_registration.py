"""Tests for the newly registered standalone scripts."""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_eval_tests.experiments.common import RunContext, capture_environment

pytestmark = pytest.mark.offline


def _context(tmp_path: Path, spec, **baseline) -> RunContext:
    return RunContext(
        spec=spec,
        dataset=tmp_path / "dataset",
        output_dir=tmp_path / "run",
        baseline=baseline or {"model": "gpt-4o-mini", "max_cases": 3},
        environment=capture_environment(storage_dir=""),
        variables=[],
        run_id="r",
        extra={"base_url": "http://hosted.test/v1"},
    )


def test_frozen_prompt_spec_defaults_and_args(tmp_path: Path) -> None:
    from memory_eval_tests.experiments.frozen_prompt_llm_eval import _frozen_args, spec

    assert spec.id == "frozen_prompt_llm_eval"
    assert spec.default_baseline["model"] == "gpt-4o-mini"
    args = _frozen_args(_context(tmp_path, spec))
    assert args.model == "gpt-4o-mini"
    assert args.base_url == "http://hosted.test/v1"
    assert args.max_cases == 3
    assert args.output == tmp_path / "run" / "frozen_prompt_results.json"
    assert args.api_key_env == "LIGHTRAG_PROJECT_OPENAI_API_KEY"


def test_analysis_specs_are_registered(tmp_path: Path) -> None:
    from memory_eval_tests.experiments.evaluator_recheck import spec as recheck
    from memory_eval_tests.experiments.evidence_selector_failure_analysis import (
        spec as failure,
    )

    assert recheck.id == "evaluator_recheck"
    assert failure.id == "evidence_selector_failure_analysis"
    assert callable(recheck.runner)
    assert callable(failure.runner)


def test_registry_contains_all_three(tmp_path: Path) -> None:
    from memory_eval_tests.experiments.registry import list_specs

    ids = {spec.id for spec in list_specs()}
    assert {
        "frozen_prompt_llm_eval",
        "evaluator_recheck",
        "evidence_selector_failure_analysis",
    } <= ids
