"""Recall-focused evaluation for one isolated LightRAG run.

This module intentionally evaluates only the retrieval stage.  It reuses the
same evidence-matching rules as the product evaluation framework so recall
numbers are comparable, but returns a richer ranking artifact that makes
``rank 4 -> rank 1`` and ``rank 7 -> rank 4`` distinguishable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from memory_eval_tests.dataset import DatasetClient
from memory_eval_tests.http import post_json
from memory_eval_tests.retrieval import _find_fact_match, _ordered_chunks
from memory_eval_tests.sampling import sample_evenly

_EXCERPT_CHARS = 900


def _excerpt(text: str) -> str:
    text = " ".join(text.split())
    return text[:_EXCERPT_CHARS] + ("…" if len(text) > _EXCERPT_CHARS else "")


def _question_level_metrics(
    fact_ranks: list[int], expected_count: int, top_k: int
) -> dict[str, Any]:
    """Return the metric set that lets us separate candidate recall from ranking."""
    if not fact_ranks:
        return {
            "recall_at_1": 0.0,
            "recall_at_3": 0.0,
            "recall_at_5": 0.0,
            "recall_at_k": 0.0,
            "full_recall_at_1": False,
            "full_recall_at_3": False,
            "full_recall_at_5": False,
            "full_recall_at_k": False,
            "mrr": 0.0,
            "mean_fact_mrr": 0.0,
            "first_evidence_rank": None,
        }

    def recall_at(cutoff: int) -> float:
        return sum(rank <= cutoff for rank in fact_ranks) / expected_count

    def full_at(cutoff: int) -> bool:
        return all(rank <= cutoff for rank in fact_ranks)

    first_rank = min(fact_ranks)
    return {
        "recall_at_1": recall_at(1),
        "recall_at_3": recall_at(3),
        "recall_at_5": recall_at(5),
        "recall_at_k": recall_at(top_k),
        "full_recall_at_1": full_at(1),
        "full_recall_at_3": full_at(3),
        "full_recall_at_5": full_at(5),
        "full_recall_at_k": full_at(top_k),
        "mrr": 1 / first_rank,
        "mean_fact_mrr": sum(1 / rank for rank in fact_ranks) / expected_count,
        "first_evidence_rank": first_rank,
    }


def _summarize(results: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    """Aggregate recall metrics overall and by question type."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        buckets[str(result.get("question_type") or "unknown")].append(result)

    def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {}
        first_ranks = [
            row["first_evidence_rank"]
            for row in rows
            if row.get("first_evidence_rank") is not None
        ]
        return {
            "cases": len(rows),
            "recall_at_1": sum(row["recall_at_1"] for row in rows) / len(rows),
            "recall_at_3": sum(row["recall_at_3"] for row in rows) / len(rows),
            "recall_at_5": sum(row["recall_at_5"] for row in rows) / len(rows),
            "recall_at_k": sum(row["recall_at_k"] for row in rows) / len(rows),
            "full_recall_at_1": sum(row["full_recall_at_1"] for row in rows),
            "full_recall_at_3": sum(row["full_recall_at_3"] for row in rows),
            "full_recall_at_5": sum(row["full_recall_at_5"] for row in rows),
            "full_recall_at_k": sum(row["full_recall_at_k"] for row in rows),
            "mrr": sum(row["mrr"] for row in rows) / len(rows),
            "mean_fact_mrr": sum(row["mean_fact_mrr"] for row in rows) / len(rows),
            "gold_rank_distribution": {
                "1": sum(rank == 1 for rank in first_ranks),
                "2": sum(rank == 2 for rank in first_ranks),
                "3": sum(rank == 3 for rank in first_ranks),
                "4_5": sum(4 <= rank <= 5 for rank in first_ranks),
                "6_10": sum(6 <= rank <= 10 for rank in first_ranks),
                "11_plus": sum(rank >= 11 for rank in first_ranks),
                "miss": sum(row["first_evidence_rank"] is None for row in rows),
            },
        }

    by_type = {
        question_type: aggregate(rows)
        for question_type, rows in sorted(buckets.items())
    }
    return {
        "overall": aggregate(results),
        "by_question_type": by_type,
    }


def evaluate_recall(
    *,
    dataset_source: str,
    rag_api_url: str,
    mode: str = "naive",
    top_k: int = 20,
    chunk_top_k: int | None = None,
    max_cases: int | None = None,
    question_types: list[str] | None = None,
    api_key: str | None = None,
    access_token: str | None = None,
    enable_rerank: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Run recall-only retrieval scoring against an already-indexed server."""
    oracle = DatasetClient(dataset_source).oracle()
    questions = sample_evenly(list(oracle.get("questions", [])), max_cases)
    if question_types:
        questions = [
            question
            for question in questions
            if question.get("question_type") in question_types
        ]
    questions = [
        question
        for question in questions
        if question.get("expected_behavior") != "abstain"
    ]

    chunk_top_k = chunk_top_k or top_k
    results: list[dict[str, Any]] = []
    total = len(questions)
    for position, question in enumerate(questions, start=1):
        payload = {
            "query": question["question"],
            "mode": mode,
            "top_k": top_k,
            "chunk_top_k": chunk_top_k,
            "include_chunk_content": True,
            "enable_rerank": enable_rerank,
        }
        response = post_json(
            f"{rag_api_url.rstrip('/')}/query/data",
            payload,
            api_key=api_key,
            access_token=access_token,
        )
        ordered_chunks = _ordered_chunks(response)
        expected_facts = [
            fact
            for fact in oracle.get("facts", [])
            if fact["fact_id"] in question.get("evidence_fact_ids", [])
        ]
        expected_ids = [str(fact["fact_id"]) for fact in expected_facts]

        candidates: list[dict[str, Any]] = []
        fact_ranks: dict[str, int] = {}
        for rank, chunk in enumerate(ordered_chunks, start=1):
            matched_fact_ids: list[str] = []
            for fact in expected_facts:
                fact_id = str(fact["fact_id"])
                if fact_id in fact_ranks:
                    continue
                if _find_fact_match(chunk["content"], fact) is not None:
                    fact_ranks[fact_id] = rank
                    matched_fact_ids.append(fact_id)
            candidates.append(
                {
                    "rank": rank,
                    "chunk_id": chunk.get("chunk_id") or "",
                    "file_path": chunk.get("file_path") or "",
                    "content_excerpt": _excerpt(chunk["content"]),
                    "matched_fact_ids": matched_fact_ids,
                }
            )

        ranks = [fact_ranks.get(fact_id, 0) for fact_id in expected_ids]
        ranks = [rank for rank in ranks if rank]
        metrics = _question_level_metrics(ranks, len(expected_ids), chunk_top_k)
        results.append(
            {
                "question_id": question["id"],
                "question_type": question.get("question_type", ""),
                "question": question["question"],
                "expected_fact_ids": expected_ids,
                "gold_rank_by_fact": {
                    fact_id: fact_ranks.get(fact_id)
                    for fact_id in expected_ids
                },
                **metrics,
                "candidates": candidates,
            }
        )
        if progress_callback:
            progress_callback(position, total)

    return {
        "mode": mode,
        "top_k": top_k,
        "chunk_top_k": chunk_top_k,
        "cases": len(results),
        "summary": _summarize(results, chunk_top_k),
        "results": results,
    }
