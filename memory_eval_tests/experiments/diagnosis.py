"""Versioned case traces and conservative deterministic failure attribution."""

from __future__ import annotations

from collections import Counter
from typing import Any

CASE_TRACE_SCHEMA_VERSION = "1.0"
DIAGNOSIS_SCHEMA_VERSION = "1.0"
DIAGNOSIS_RULE_VERSION = "1.0"

_UNAVAILABLE = "unavailable"
_MISSING = "missing"
_OBSERVED = "observed"


def unavailable(reason: str) -> dict[str, str]:
    return {"status": _UNAVAILABLE, "reason": reason}


def build_case_traces(
    *, oracle: dict[str, Any], retrieval_results: list[dict[str, Any]], answer_results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Join safe API observations into one trace per oracle question.

    Current public LightRAG query endpoints expose ranked references but not the
    assembled prompt.  This deliberately records that limitation rather than
    treating references as proof of the final answer context.
    """
    facts = {str(fact.get("fact_id")): fact for fact in oracle.get("facts") or []}
    retrieval = {str(row.get("question_id")): row for row in retrieval_results}
    answers = {str(row.get("question_id")): row for row in answer_results}
    traces: list[dict[str, Any]] = []
    for question in oracle.get("questions") or []:
        question_id = str(question.get("id"))
        evidence_ids = [str(item) for item in question.get("evidence_fact_ids") or []]
        retrieval_row = retrieval.get(question_id)
        answer_row = answers.get(question_id)
        traces.append(
            {
                "schema_version": CASE_TRACE_SCHEMA_VERSION,
                "question_id": question_id,
                "oracle": {
                    "question": question.get("question"),
                    "answer": question.get("answer"),
                    "expected_behavior": question.get("expected_behavior", "answer"),
                    "question_type": question.get("question_type"),
                    "evidence_fact_ids": evidence_ids,
                    "evidence_facts": [facts[item] for item in evidence_ids if item in facts],
                },
                "parsed": unavailable("isolated API does not expose parsed object trace"),
                "chunks": unavailable("isolated API does not expose chunk trace"),
                "index": unavailable("isolated API does not expose index membership trace"),
                "retrieval": (
                    {
                        "status": _OBSERVED,
                        "recall_at_k": retrieval_row.get("recall_at_k"),
                        "hit_fact_ids": retrieval_row.get("hit_fact_ids") or [],
                        "top_k_candidates": retrieval_row.get("top_k_candidates") or [],
                        "hit_evidence": retrieval_row.get("hit_evidence") or [],
                    }
                    if retrieval_row is not None
                    else unavailable("no retrieval trace was produced for this question")
                ),
                "final_context": unavailable(
                    "public query API does not expose the final rendered prompt context"
                ),
                "answer": (
                    {
                        "status": _OBSERVED,
                        "text": answer_row.get("answer"),
                        "exact_match": answer_row.get("exact_match"),
                        "abstention_correct": answer_row.get("abstention_correct"),
                        "citation_presence": answer_row.get("citation_presence"),
                        "citation_correctness": answer_row.get("citation_correctness"),
                        "response_references": answer_row.get("response_references") or [],
                    }
                    if answer_row is not None
                    else unavailable("no answer trace was produced for this question")
                ),
                "oracle_upper_bound": unavailable("no linked oracle upper-bound run"),
            }
        )
    return traces


def diagnose_case(trace: dict[str, Any]) -> dict[str, Any]:
    """Classify only when trace evidence supports the conclusion.

    Priority follows the data path.  Unknown observability never becomes a
    synthetic parser/index/model failure; it is returned as ``unclassified``.
    """
    oracle = trace.get("oracle") or {}
    answer = trace.get("answer") or {}
    expected_abstain = oracle.get("expected_behavior") == "abstain"
    if expected_abstain and answer.get("status") == _OBSERVED:
        if answer.get("abstention_correct") is False:
            return _diagnosis("abstention_failure", 1.0, ["answer.abstention_correct=false"])
        if answer.get("abstention_correct") is True:
            return _diagnosis("not_applicable", 1.0, ["correct abstention"])

    if answer.get("status") == _OBSERVED and answer.get("exact_match") is True:
        return _diagnosis("not_applicable", 1.0, ["answer.exact_match=true"])

    for key, cause in (("parsed", "parse_missing"), ("chunks", "chunk_missing"), ("index", "index_missing")):
        value = trace.get(key) or {}
        if value.get("status") == _MISSING:
            return _diagnosis(cause, 0.95, [f"{key}.status=missing"])

    retrieval = trace.get("retrieval") or {}
    if retrieval.get("status") == _OBSERVED:
        recall = retrieval.get("recall_at_k")
        if isinstance(recall, (int, float)) and recall < 1:
            return _diagnosis(
                "retrieval_miss",
                0.9,
                [f"retrieval.recall_at_k={recall}", "retrieval.top_k_candidates observed"],
            )

    final_context = trace.get("final_context") or {}
    if final_context.get("status") == _OBSERVED:
        sufficient = final_context.get("contains_all_oracle_evidence")
        if sufficient is False:
            return _diagnosis(
                "selection_or_truncation_miss",
                0.9,
                ["retrieval contains evidence", "final_context lacks complete oracle evidence"],
            )
        if sufficient is True:
            upper = trace.get("oracle_upper_bound") or {}
            if upper.get("status") == _OBSERVED and upper.get("exact_match") is False:
                return _diagnosis(
                    "oracle_or_scorer_uncertain",
                    0.8,
                    ["oracle upper-bound answer also failed"],
                )
            return _diagnosis(
                "generation_or_prompt_failure",
                0.85,
                ["final_context contains complete oracle evidence", "answer.exact_match=false"],
            )

    return _diagnosis(
        "unclassified",
        0.0,
        ["required trace is unavailable or the rule is not applicable"],
        review_required=True,
    )


def build_diagnosis(traces: list[dict[str, Any]]) -> dict[str, Any]:
    cases = [{"question_id": trace.get("question_id"), **diagnose_case(trace)} for trace in traces]
    causes = Counter(case["primary_cause"] for case in cases)
    applicable = [case for case in cases if case["primary_cause"] != "not_applicable"]
    classified = [
        case
        for case in applicable
        if case["primary_cause"] not in {"unclassified", "oracle_or_scorer_uncertain"}
    ]
    return {
        "schema_version": DIAGNOSIS_SCHEMA_VERSION,
        "rule_version": DIAGNOSIS_RULE_VERSION,
        "cases": cases,
        "case_count": len(cases),
        "applicable_case_count": len(applicable),
        "diagnosis_coverage": len(classified) / len(applicable) if applicable else 1.0,
        "trace_availability": {
            "fully_observable": sum(
                1 for trace in traces if (trace.get("final_context") or {}).get("status") == _OBSERVED
            ),
            "context_unavailable": sum(
                1 for trace in traces if (trace.get("final_context") or {}).get("status") != _OBSERVED
            ),
        },
        "cause_distribution": dict(sorted(causes.items())),
    }


def _diagnosis(
    primary_cause: str,
    confidence: float,
    evidence: list[str],
    *,
    review_required: bool = False,
) -> dict[str, Any]:
    return {
        "primary_cause": primary_cause,
        "confidence": confidence,
        "evidence": evidence,
        "rule_version": DIAGNOSIS_RULE_VERSION,
        "review_required": review_required,
    }
