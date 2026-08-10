"""I3 fair-comparison plan contract tests."""

from __future__ import annotations

import pytest

from lightrag.api.eval_comparison import validate_plan

pytestmark = pytest.mark.offline


def test_answer_model_plan_rejects_embedding_variables() -> None:
    with pytest.raises(ValueError, match="does not allow variables: embedding_model"):
        validate_plan(
            comparison_type="answer_model",
            variables={"answer_model": ["a", "b"], "embedding_model": ["e1", "e2"]},
            inputs={"frozen_context_run_id": "run-1"},
        )


def test_embedding_plan_requires_isolated_rebuild_per_arm() -> None:
    plan = validate_plan(
        comparison_type="embedding",
        variables={"embedding_model": ["embed-a", "embed-b"]},
        inputs={"environment_profile_id": "p", "environment_profile_version": 1},
    )
    assert plan["reuse_permitted"] is False
    assert plan["index_requirement"] == "rebuild_isolated_index_per_arm"
    assert "allocate_isolated_workspace_per_arm" in plan["execution_dependencies"]
