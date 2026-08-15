from __future__ import annotations

from collections.abc import Callable
from typing import Any

from memory_eval_tests.dataset import DatasetClient
from memory_eval_tests.evidence import normalize_evidence, normalize_evidence_offsets
from memory_eval_tests.http import post_json as _http_post_json
from memory_eval_tests.sampling import sample_evenly

_HIT_EVIDENCE_LIMIT = 5
_HIT_EVIDENCE_EXCERPT_CHARS = 700


def evaluate_api(
    *,
    dataset_source: str,
    rag_api_url: str,
    mode: str = "mix",
    top_k: int = 10,
    chunk_top_k: int | None = None,
    max_cases: int | None = None,
    question_types: list[str] | None = None,
    api_key: str | None = None,
    access_token: str | None = None,
    enable_rerank: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
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
        # ``/query/data`` exposes the original chunk list before the API
        # groups it into per-file references.  Use that ordered list for
        # MRR/Recall so multi-file runs do not lose the true cross-file rank.
        ordered_chunks = _ordered_chunks(response)
        ranked_chunks: list[tuple[int, int, str]] = [
            (rank, rank - 1, item["content"])
            for rank, item in enumerate(ordered_chunks, start=1)
        ]
        hits_by_fact: dict[str, int] = {}
        match_by_fact: dict[str, dict[str, Any]] = {}
        # Pass 1: precise FACT-ID-anchored matching.  Generated documents
        # repeat the same answer sentence on several pages (e.g. a release
        # constraint), so the bare answer alone cannot tell which instance was
        # retrieved.  The expected_text carries the FACT ID and wins.
        remaining_facts: list[dict[str, Any]] = []
        for fact in expected_facts:
            fact_id = str(fact["fact_id"])
            hit = next(
                (
                    (rank, match)
                    for rank, _ref_index, chunk in ranked_chunks
                    if (match := _find_expected_text_match(chunk, fact)) is not None
                ),
                None,
            )
            if hit is None:
                remaining_facts.append(fact)
                continue
            rank, match = hit
            hits_by_fact[fact_id] = rank
            match_by_fact[fact_id] = match
        # Pass 2: loose answer-content fallback for facts whose full sentence
        # is not verbatim in any chunk (tables/equations render as markup or
        # JSON, and older runs only carry the answer value).
        for fact in remaining_facts:
            fact_id = str(fact["fact_id"])
            hit = next(
                (
                    (rank, match)
                    for rank, _ref_index, chunk in ranked_chunks
                    if (match := _find_answer_match(chunk, fact)) is not None
                ),
                None,
            )
            if hit is None:
                continue
            rank, match = hit
            hits_by_fact[fact_id] = rank
            match_by_fact[fact_id] = match

        hit_evidence: list[dict[str, Any]] = []
        hit_chunk_count = 0
        for rank, ref_index, chunk in ranked_chunks:
            chunk_matches: list[dict[str, Any]] = [
                match_by_fact[str(fact["fact_id"])]
                for fact in expected_facts
                if hits_by_fact.get(str(fact["fact_id"])) == rank
                and str(fact["fact_id"]) in match_by_fact
            ]
            if chunk_matches:
                hit_chunk_count += 1
                hit_evidence.append(
                    {
                        "rank": rank,
                        "file_path": ordered_chunks[ref_index].get("file_path", ""),
                        "text": chunk,
                        "chars": len(chunk),
                        "matches": chunk_matches,
                    }
                )

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
                "hit_evidence": hit_evidence[:_HIT_EVIDENCE_LIMIT],
                "top_contexts": [
                    {
                        "rank": ref_index + 1,
                        "file_path": ref.get("file_path", ""),
                        "chunk_count": len(ref.get("content") or []),
                        # Full ranked chunk text so the WebUI can present every
                        # Top-K candidate in a review modal, not only the hits.
                        "chunks": [
                            {"index": index, "text": chunk}
                            for index, chunk in enumerate(ref.get("content") or [])
                        ],
                    }
                    for ref_index, ref in enumerate(references)
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
        raise TypeError(
            "/query/data response contains no ranked references list; "
            "cannot compute API retrieval metrics."
        )
    for index, ref in enumerate(references):
        if not isinstance(ref, dict) or not isinstance(ref.get("content"), list):
            raise TypeError(
                "Reference item at index "
                f"{index} has no chunk content array; set "
                "include_chunk_content=true on the /query/data request."
            )
    return references


def _ordered_chunks(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Return chunks in the API's original ranking order.

    The public response only promises ranked ``references``; however
    ``data.chunks`` is populated by the same retrieval pipeline before the
    per-file reference grouping, so it preserves the true global order for
    single- and multi-file datasets.
    """
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    chunks = data.get("chunks")
    if not isinstance(chunks, list):
        # Older/mocked responses only expose the already-grouped reference
        # content.  Preserve compatibility by flattening those references.
        references = data.get("references")
        if isinstance(references, list):
            flattened: list[dict[str, Any]] = []
            for ref in references:
                if not isinstance(ref, dict):
                    continue
                for content in ref.get("content") or []:
                    if isinstance(content, str):
                        flattened.append(
                            {
                                "content": content,
                                "file_path": ref.get("file_path", ""),
                                "chunk_id": "",
                            }
                        )
            return flattened
        raise TypeError(
            "/query/data response contains no ordered chunk list; "
            "cannot compute API retrieval metrics."
        )
    ordered: list[dict[str, Any]] = []
    for chunk in chunks:
        if not isinstance(chunk, dict) or not isinstance(chunk.get("content"), str):
            raise TypeError(
                "chunk item has no string content; set include_chunk_content=true "
                "on the /query/data request."
            )
        ordered.append(
            {
                "content": chunk["content"],
                "file_path": chunk.get("file_path", ""),
                "chunk_id": chunk.get("chunk_id", ""),
            }
        )
    return ordered


def _content_contains_fact(content: str, fact: dict[str, Any]) -> bool:
    """Return whether ``content`` contains answer-bearing evidence for ``fact``.

    A stable fact ID is useful for diagnostics but it is not sufficient
    evidence: source prose can name the ID without including its value. Keep
    this helper as the compatibility boundary for callers/tests while making
    the stricter witness rule explicit.
    """
    return _find_fact_match(content, fact) is not None


def _find_fact_match(content: str, fact: dict[str, Any]) -> dict[str, Any] | None:
    """Return a focused proof of a fact match, or ``None`` when it is absent."""
    return _find_expected_text_match(content, fact) or _find_answer_match(content, fact)


def _find_expected_text_match(
    content: str, fact: dict[str, Any]
) -> dict[str, Any] | None:
    """Match the FACT-ID-anchored sentence, the most specific witness."""
    value = fact.get("expected_text")
    if not isinstance(value, str) or not value.strip():
        return None
    return _match_witness(content, value.strip(), fact, match_type="expected_text")


def _find_answer_match(content: str, fact: dict[str, Any]) -> dict[str, Any] | None:
    """Match the bare answer value, used as a fallback for artifacts."""
    value = fact.get("answer")
    if not isinstance(value, str) or not value.strip():
        return None
    return _match_witness(content, value.strip(), fact, match_type="answer")


def _match_witness(
    content: str, witness: str, fact: dict[str, Any], *, match_type: str
) -> dict[str, Any] | None:
    span = _find_evidence_span(content, witness)
    if span is None:
        return None
    start, end = span
    excerpt_start, excerpt_end = _excerpt_bounds(len(content), start, end)
    return {
        "fact_id": str(fact.get("fact_id") or ""),
        "match_type": match_type,
        "matched_text": witness,
        "start": start,
        "end": end,
        "excerpt": content[excerpt_start:excerpt_end],
        "excerpt_start": excerpt_start,
        "excerpt_end": excerpt_end,
        "excerpt_truncated": excerpt_start > 0 or excerpt_end < len(content),
    }


def _find_evidence_span(content: str, witness: str) -> tuple[int, int] | None:
    """Locate a literal or normalization-equivalent witness in source text."""
    literal_start = content.lower().find(witness.lower())
    if literal_start >= 0:
        return literal_start, literal_start + len(witness)

    normalized_witness = normalize_evidence(witness)
    if not normalized_witness:
        return None
    normalized_content, offsets = normalize_evidence_offsets(content)
    normalized_start = normalized_content.find(normalized_witness)
    if normalized_start < 0:
        return None
    normalized_end = normalized_start + len(normalized_witness) - 1
    return offsets[normalized_start], offsets[normalized_end] + 1


def _excerpt_bounds(content_chars: int, match_start: int, match_end: int) -> tuple[int, int]:
    """Return a bounded excerpt centred on the answer-bearing evidence."""
    if content_chars <= _HIT_EVIDENCE_EXCERPT_CHARS:
        return 0, content_chars
    prefix_chars = min(220, match_start)
    start = match_start - prefix_chars
    end = min(content_chars, start + _HIT_EVIDENCE_EXCERPT_CHARS)
    if end - start < _HIT_EVIDENCE_EXCERPT_CHARS:
        start = max(0, end - _HIT_EVIDENCE_EXCERPT_CHARS)
    # A very long witness is still shown in full whenever it fits the source.
    if match_end > end:
        end = min(content_chars, match_end)
        start = max(0, end - _HIT_EVIDENCE_EXCERPT_CHARS)
    return start, end


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
