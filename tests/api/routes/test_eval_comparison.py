"""Metric-domain comparison contract tests."""

from __future__ import annotations

from typing import Any

from lightrag.api.eval_comparison import compare_contract


def _envelope(
    *,
    methods: list[dict[str, Any]],
    dataset_fingerprint: str = "d1",
    answer_scorers: list[dict[str, str]] | None = None,
    retrieval_scorers: list[dict[str, str]] | None = None,
    profile: str = "p1",
    configuration_fingerprint: str = "c1",
) -> dict[str, Any]:
    return {
        "run_id": "r",
        "status": "complete",
        "methods": methods,
        "scorers": answer_scorers,
        "answer_scorers": answer_scorers,
        "retrieval_scorers": retrieval_scorers,
        "execution_manifest": {
            "dataset": {"manifest_sha256": dataset_fingerprint},
            "case_selection": {"case_ids": ["Q-1", "Q-2", "Q-3"]},
            "execution_unit": {
                "profile": profile,
                "configuration_fingerprint": configuration_fingerprint,
            },
        },
    }


def _retrieval_method(question_ids: list[str]) -> dict[str, Any]:
    return {
        "method": "retrieval",
        "summary": {"cases": len(question_ids), "recall_at_1": 0.5, "mrr": 0.6},
        "results": [
            {"question_id": qid, "recall_at_1": 0.5, "mrr": 0.6} for qid in question_ids
        ],
    }


def _answer_method(question_ids: list[str]) -> dict[str, Any]:
    return {
        "method": "answer",
        "summary": {
            "cases": len(question_ids),
            "answer_accuracy": 0.8,
            "groundedness": 0.9,
        },
        "results": [{"question_id": qid, "exact_match": True} for qid in question_ids],
    }


RETRIEVAL_SCORERS = [
    {"name": "recall@k", "version": "1"},
    {"name": "mrr", "version": "1"},
]
ANSWER_SCORERS = [{"name": "deterministic-answer-rules", "version": "1"}]


def test_retrieval_only_vs_end_to_end_compares_retrieval_domain() -> None:
    retrieval_only = _envelope(
        methods=[_retrieval_method(["Q-1", "Q-2"])],
        retrieval_scorers=RETRIEVAL_SCORERS,
    )
    end_to_end = _envelope(
        methods=[_retrieval_method(["Q-1", "Q-2"]), _answer_method(["Q-1", "Q-2"])],
        retrieval_scorers=RETRIEVAL_SCORERS,
        answer_scorers=ANSWER_SCORERS,
    )
    contract = compare_contract([retrieval_only, end_to_end])
    assert contract["domains"]["retrieval"]["comparable"] is True
    assert contract["domains"]["retrieval"]["comparable_cases"] == 2
    assert contract["domains"]["answer"]["comparable"] is False
    assert contract["domains"]["answer"]["available"] == [False, True]
    assert any(
        item["domain"] == "answer"
        and item["run_index"] == 0
        and "did not evaluate" in item["reason"]
        for item in contract["metrics_unavailable"]
    )
    # Retrieval domain shares dataset/cases/scorers, so it is ranking-eligible;
    # the answer domain is missing in one run, so whole-run ranking is not.
    assert contract["ranking"]["retrieval"]["eligible"] is True
    assert contract["ranking_permitted"] is False
    assert "answer not evaluated in every run" in contract["ranking_reasons"]
    assert contract["comparable"] is True


def test_identical_end_to_end_runs_are_ranking_eligible() -> None:
    runs = [
        _envelope(
            methods=[_retrieval_method(["Q-1", "Q-2"]), _answer_method(["Q-1", "Q-2"])],
            retrieval_scorers=RETRIEVAL_SCORERS,
            answer_scorers=ANSWER_SCORERS,
        )
        for _ in range(2)
    ]
    contract = compare_contract(runs)
    assert contract["ranking_permitted"] is True
    assert contract["domains"]["retrieval"]["comparable"] is True
    assert contract["domains"]["answer"]["comparable"] is True


def test_answer_scorer_mismatch_blocks_answer_domain_and_ranking() -> None:
    runs = [
        _envelope(
            methods=[_retrieval_method(["Q-1", "Q-2"]), _answer_method(["Q-1", "Q-2"])],
            retrieval_scorers=RETRIEVAL_SCORERS,
            answer_scorers=ANSWER_SCORERS,
        ),
        _envelope(
            methods=[_retrieval_method(["Q-1", "Q-2"]), _answer_method(["Q-1", "Q-2"])],
            retrieval_scorers=RETRIEVAL_SCORERS,
            answer_scorers=[{"name": "deterministic-answer-rules", "version": "2"}],
        ),
    ]
    contract = compare_contract(runs)
    assert contract["domains"]["answer"]["comparable"] is False
    assert contract["domains"]["answer"]["reason"] == "answer scorer inventories differ"
    assert contract["domains"]["retrieval"]["comparable"] is True
    assert contract["ranking_permitted"] is False
    assert "answer scorer inventory differs" in contract["ranking_reasons"]


def test_case_set_mismatch_keeps_compare_but_blocks_ranking() -> None:
    runs = [
        _envelope(
            methods=[_retrieval_method(["Q-1", "Q-2"]), _answer_method(["Q-1", "Q-2"])],
            retrieval_scorers=RETRIEVAL_SCORERS,
            answer_scorers=ANSWER_SCORERS,
        ),
        _envelope(
            methods=[_retrieval_method(["Q-2", "Q-3"]), _answer_method(["Q-2", "Q-3"])],
            retrieval_scorers=RETRIEVAL_SCORERS,
            answer_scorers=ANSWER_SCORERS,
        ),
    ]
    contract = compare_contract(runs)
    assert contract["domains"]["retrieval"]["comparable"] is True
    assert contract["domains"]["retrieval"]["comparable_cases"] == 1
    assert contract["ranking_permitted"] is False
    assert "retrieval case set differs" in contract["ranking_reasons"]
