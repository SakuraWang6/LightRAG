from __future__ import annotations

from typing import Any, Callable

from memory_eval_tests.dataset import DatasetClient
from memory_eval_tests.evidence import normalize_evidence
from memory_eval_tests.http import post_json as _http_post_json
from memory_eval_tests.sampling import sample_evenly

_HIT_EVIDENCE_LIMIT = 5
_HIT_EVIDENCE_CHARS = 500
_TRACE_CANDIDATE_CHARS = 1200


def evaluate_api(
    *,
    dataset_source: str,
    rag_api_url: str,
    mode: str = "mix",
    top_k: int = 10,
    chunk_top_k: int | None = None,
    max_cases: int | None = None,
    api_key: str | None = None,
    access_token: str | None = None,
    enable_rerank: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    oracle = DatasetClient(dataset_source).oracle()
    questions = sample_evenly(list(oracle.get("questions", [])), max_cases)
    questions = [
        question
        for question in questions
        if question.get("expected_behavior") != "abstain"
    ]
    results: list[dict[str, Any]] = []
    total_questions = len(questions)
    for position, question in enumerate(questions, start=1):
        payload = {
            "query": question["question"],
            "mode": mode,
            "top_k": top_k,
            "chunk_top_k": chunk_top_k if chunk_top_k is not None else top_k,
            # The references carry ranked chunk content only when requested;
            # MRR/Recall are computed from those ranks, not from the whole
            # serialized response.
            "include_chunk_content": True,
            # Product evaluations do not expose a reranker selection.  Leaving
            # this unset makes the query API enable reranking by default and
            # emit a warning when the server has no reranker configured.
            "enable_rerank": enable_rerank,
        }
        response = _post_json(
            f"{rag_api_url.rstrip('/')}/query/data",
            payload,
            api_key=api_key,
            access_token=access_token,
        )
        expected_facts = [
            fact
            for fact in oracle.get("facts", [])
            if fact["fact_id"] in question.get("evidence_fact_ids", [])
        ]
        references = _ranked_references(response)
        ranked_chunks: list[tuple[int, int, str]] = []
        rank = 0
        for ref_index, ref in enumerate(references):
            for chunk in ref.get("content") or []:
                rank += 1
                ranked_chunks.append((rank, ref_index, chunk))
        hits_by_fact: dict[str, int] = {}
        hit_evidence: dict[str, dict[str, Any]] = {}
        hit_chunk_count = 0
        for rank, ref_index, chunk in ranked_chunks:
            chunk_hit = False
            for fact in expected_facts:
                if _content_contains_fact(chunk, fact):
                    fact_id = fact["fact_id"]
                    if fact_id not in hits_by_fact:
                        hits_by_fact[fact_id] = rank
                        hit_evidence[fact_id] = {
                            "fact_id": fact_id,
                            "rank": rank,
                            "file_path": references[ref_index].get("file_path", ""),
                            "text": chunk[:_HIT_EVIDENCE_CHARS],
                        }
                    chunk_hit = True
            if chunk_hit:
                hit_chunk_count += 1

        expected_count = len(expected_facts)
        recall = len(hits_by_fact) / expected_count if expected_count else 0.0
        first_rank = min(hits_by_fact.values()) if hits_by_fact else 0
        results.append(
            {
                "question_id": question["id"],
                "question_type": question.get("question_type", ""),
                "recall_at_k": recall,
                "reciprocal_rank": 1 / first_rank if first_rank else 0.0,
                "first_evidence_rank": first_rank or None,
                "context_precision": (
                    hit_chunk_count / len(ranked_chunks) if ranked_chunks else 0.0
                ),
                # The API references expose file paths and chunk text only, not
                # the parsed object kind, so object-level hits are unavailable.
                "object_hit_rate": None,
                "expected_fact_ids": [fact["fact_id"] for fact in expected_facts],
                "hit_fact_ids": [
                    fact_id
                    for fact_id, _ in sorted(
                        hits_by_fact.items(), key=lambda item: item[1]
                    )
                ],
                "hit_evidence": [
                    hit_evidence[fact_id]
                    for fact_id in sorted(
                        hit_evidence, key=lambda item: hits_by_fact[item]
                    )
                ][:_HIT_EVIDENCE_LIMIT],
                "top_contexts": [
                    {
                        "rank": ref_index + 1,
                        "file_path": ref.get("file_path", ""),
                        "chunk_count": len(ref.get("content") or []),
                    }
                    for ref_index, ref in enumerate(references)
                ],
                "top_k_candidates": [
                    {
                        "rank": candidate_rank,
                        "reference_rank": ref_index + 1,
                        "file_path": references[ref_index].get("file_path", ""),
                        "score": {
                            "value": "unknown",
                            "reason": "query/data response does not expose a candidate score",
                        },
                        "text": chunk[:_TRACE_CANDIDATE_CHARS],
                        "truncated": len(chunk) > _TRACE_CANDIDATE_CHARS,
                    }
                    for candidate_rank, ref_index, chunk in ranked_chunks
                ],
            }
        )
        if progress_callback:
            progress_callback(position, total_questions)
    report = _summarize_dict_results(results, mode=mode, top_k=top_k, backend="api")
    report["max_cases"] = max_cases
    return report


def _summarize_dict_results(
    results: list[dict[str, Any]],
    *,
    mode: str,
    top_k: int,
    backend: str,
) -> dict[str, Any]:
    if not results:
        return {
            "backend": backend,
            "mode": mode,
            "top_k": top_k,
            "cases": 0,
            "average_recall": 0.0,
            "mrr": 0.0,
            "context_precision": 0.0,
            "object_hit_rate": None,
            "results": [],
        }
    object_rates = [
        r["object_hit_rate"] for r in results if r.get("object_hit_rate") is not None
    ]
    return {
        "backend": backend,
        "mode": mode,
        "top_k": top_k,
        "cases": len(results),
        "average_recall": sum(r["recall_at_k"] for r in results) / len(results),
        "mrr": sum(r["reciprocal_rank"] for r in results) / len(results),
        "context_precision": sum(r["context_precision"] for r in results)
        / len(results),
        "object_hit_rate": sum(object_rates) / len(object_rates)
        if object_rates
        else None,
        "full_recall_cases": sum(r["recall_at_k"] == 1.0 for r in results),
        "results": results,
    }


def _ranked_references(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the ranked reference list from a ``/query/data`` response.

    The server returns references in retrieval order; each item lists the chunk
    contents from one source file when ``include_chunk_content`` is requested.
    Rank semantics require that content, so a response without it fails loudly
    instead of silently degrading to whole-payload substring matching.
    """
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    references = data.get("references")
    if references is None:
        references = response.get("references")
    if not isinstance(references, list):
        raise ValueError(
            "/query/data response contains no ranked references list; "
            "cannot compute API retrieval metrics."
        )
    for index, ref in enumerate(references):
        if not isinstance(ref, dict) or not isinstance(ref.get("content"), list):
            raise ValueError(
                "Reference item at index "
                f"{index} has no chunk content array; set "
                "include_chunk_content=true on the /query/data request."
            )
    return references


def _content_contains_fact(content: str, fact: dict[str, Any]) -> bool:
    normalized_content = normalize_evidence(content)
    return (
        fact.get("fact_id", "") in content
        or fact.get("answer", "") in content
        or fact.get("expected_text", "") in content
        or normalize_evidence(str(fact.get("answer", ""))) in normalized_content
        or normalize_evidence(str(fact.get("expected_text", ""))) in normalized_content
    )


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str | None = None,
    access_token: str | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    return _http_post_json(
        url,
        payload,
        api_key=api_key,
        access_token=access_token,
        timeout=timeout,
    )
