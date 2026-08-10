"""Deterministic I2 failure attribution tests."""

from __future__ import annotations

import pytest

from memory_eval_tests.experiments.diagnosis import build_case_traces, build_diagnosis, diagnose_case

pytestmark = pytest.mark.offline


def _trace(**overrides):
    trace = {
        "question_id": "Q-1",
        "oracle": {"expected_behavior": "answer"},
        "parsed": {"status": "observed"},
        "chunks": {"status": "observed"},
        "index": {"status": "observed"},
        "retrieval": {"status": "observed", "recall_at_k": 1.0, "top_k_candidates": [{}]},
        "final_context": {"status": "observed", "contains_all_oracle_evidence": True},
        "answer": {"status": "observed", "exact_match": False},
        "oracle_upper_bound": {"status": "unavailable"},
    }
    trace.update(overrides)
    return trace


@pytest.mark.parametrize(
    ("trace", "cause"),
    [
        (_trace(parsed={"status": "missing"}), "parse_missing"),
        (_trace(retrieval={"status": "observed", "recall_at_k": 0.5}), "retrieval_miss"),
        (_trace(final_context={"status": "observed", "contains_all_oracle_evidence": False}), "selection_or_truncation_miss"),
        (_trace(), "generation_or_prompt_failure"),
    ],
)
def test_diagnosis_classifies_observed_failures(trace, cause) -> None:
    assert diagnose_case(trace)["primary_cause"] == cause


def test_unobservable_context_is_not_blame_assigned_to_generation() -> None:
    result = diagnose_case(_trace(final_context={"status": "unavailable"}))
    assert result["primary_cause"] == "unclassified"
    assert result["review_required"] is True


def test_case_trace_joins_oracle_retrieval_and_answer_without_claiming_prompt_visibility() -> None:
    traces = build_case_traces(
        oracle={
            "facts": [{"fact_id": "FACT-1", "answer": "42"}],
            "questions": [{"id": "Q-1", "question": "q", "answer": "42", "evidence_fact_ids": ["FACT-1"]}],
        },
        retrieval_results=[{"question_id": "Q-1", "recall_at_k": 1.0, "top_contexts": [{"rank": 1}]}],
        answer_results=[{"question_id": "Q-1", "answer": "wrong", "exact_match": False}],
    )
    assert traces[0]["oracle"]["evidence_facts"][0]["fact_id"] == "FACT-1"
    assert traces[0]["final_context"]["status"] == "unavailable"
    diagnosis = build_diagnosis(traces)
    assert diagnosis["cause_distribution"] == {"unclassified": 1}
    assert diagnosis["diagnosis_coverage"] == 0.0
