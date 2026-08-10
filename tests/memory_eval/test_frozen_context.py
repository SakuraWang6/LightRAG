from __future__ import annotations

import json

import pytest

from memory_eval_tests.experiments.frozen_context import freeze_final_contexts

pytestmark = pytest.mark.offline


def test_freeze_final_contexts_creates_shared_hashed_input(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "run.json").write_text(json.dumps({"run_id": "r", "baseline": {"temperature": 0, "num_predict": 128, "num_ctx": 8192}, "execution_manifest": {"dataset": {"manifest_sha256": "abc"}}}), encoding="utf-8")
    (parent / "case_trace.json").write_text(json.dumps({"cases": [{"question_id": "Q1", "oracle": {"question": "q", "answer": "a", "evidence_fact_ids": ["F1"]}, "final_context": {"status": "observed", "content": "ctx", "system_prompt": "sys ctx", "user_query": "q"}}]}), encoding="utf-8")
    frozen = freeze_final_contexts(parent_run_dir=parent, output_path=tmp_path / "frozen.json")
    assert frozen["input_hash"]
    assert frozen["generation_parameters_hash"]
    assert frozen["token_budget"] == {"max_total_tokens": None, "num_ctx": 8192, "num_predict": 128}
    assert frozen["prompts"][0]["prompt"] == "sys ctx\n\n---User Query---\nq"
