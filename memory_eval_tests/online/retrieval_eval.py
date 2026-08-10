from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from memory_data_service.schemas import OraclePayload
from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.common.evidence import normalize_evidence
from memory_eval_tests.common.http import post_json as _http_post_json
from memory_eval_tests.common.sampling import sample_evenly

_HIT_EVIDENCE_LIMIT = 5
_HIT_EVIDENCE_CHARS = 500


def evaluate_api(
    *,
    dataset_source: str,
    rag_api_url: str,
    mode: str = "mix",
    top_k: int = 10,
    max_cases: int | None = None,
    api_key: str | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    oracle = DatasetClient(dataset_source).oracle()
    questions = sample_evenly(list(oracle.get("questions", [])), max_cases)
    questions = [
        question
        for question in questions
        if question.get("expected_behavior") != "abstain"
    ]
    results: list[dict[str, Any]] = []
    for question in questions:
        payload = {
            "query": question["question"],
            "mode": mode,
            "top_k": top_k,
            "chunk_top_k": top_k,
            # The references carry ranked chunk content only when requested;
            # MRR/Recall are computed from those ranks, not from the whole
            # serialized response.
            "include_chunk_content": True,
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
            }
        )
    report = _summarize_dict_results(results, mode=mode, top_k=top_k, backend="api")
    report["max_cases"] = max_cases
    return report


def evaluate_sidecar(
    *,
    dataset_source: str,
    parsed_dir: Path,
    mode: str = "sidecar",
    top_k: int = 10,
    max_cases: int | None = None,
) -> dict[str, Any]:
    oracle = OraclePayload.model_validate(DatasetClient(dataset_source).oracle())
    facts_by_id = {fact.fact_id: fact for fact in oracle.facts}
    contexts = _load_sidecar_contexts(parsed_dir)
    results: list[dict[str, Any]] = []

    questions = sample_evenly(list(oracle.questions), max_cases)
    questions = [
        question for question in questions if question.expected_behavior != "abstain"
    ]
    for question in questions:
        ranked = _rank_contexts(question.question, contexts)
        top_contexts = ranked[:top_k]
        expected_facts = [
            facts_by_id[fact_id]
            for fact_id in question.evidence_fact_ids
            if fact_id in facts_by_id
        ]
        hits_by_fact: dict[str, int] = {}
        hit_evidence: dict[str, dict[str, Any]] = {}
        object_hits: set[str] = set()
        hit_context_count = 0
        for rank, context in enumerate(top_contexts, start=1):
            context_hit = False
            for fact in expected_facts:
                if _context_contains_fact(context, fact):
                    if fact.fact_id not in hits_by_fact:
                        hits_by_fact[fact.fact_id] = rank
                        hit_evidence[fact.fact_id] = {
                            "fact_id": fact.fact_id,
                            "rank": rank,
                            "kind": context["kind"],
                            "id": context["id"],
                            "text": str(context.get("content", ""))[
                                :_HIT_EVIDENCE_CHARS
                            ],
                        }
                    context_hit = True
                    if context["kind"] == fact.object_type or (
                        fact.object_type == "figure" and context["kind"] == "drawing"
                    ):
                        object_hits.add(fact.fact_id)
            if context_hit:
                hit_context_count += 1

        expected_count = len(expected_facts)
        recall = len(hits_by_fact) / expected_count if expected_count else 0.0
        first_rank = min(hits_by_fact.values()) if hits_by_fact else 0
        object_expected = [
            fact
            for fact in expected_facts
            if fact.object_type in {"table", "figure", "equation"}
        ]
        results.append(
            {
                "question_id": question.id,
                "question_type": question.question_type,
                "recall_at_k": recall,
                "reciprocal_rank": 1 / first_rank if first_rank else 0.0,
                "context_precision": hit_context_count / len(top_contexts)
                if top_contexts
                else 0.0,
                "object_hit_rate": (
                    len(object_hits) / len(object_expected) if object_expected else 1.0
                ),
                "expected_fact_ids": [fact.fact_id for fact in expected_facts],
                "hit_fact_ids": sorted(hits_by_fact),
                "hit_evidence": [
                    hit_evidence[fact_id]
                    for fact_id in sorted(
                        hit_evidence, key=lambda item: hits_by_fact[item]
                    )
                ][:_HIT_EVIDENCE_LIMIT],
                "top_contexts": [
                    {
                        "rank": index + 1,
                        "kind": context["kind"],
                        "id": context["id"],
                        "blockid": context.get("blockid", ""),
                        "score": context["score"],
                    }
                    for index, context in enumerate(top_contexts)
                ],
            }
        )

    report = _summarize_dict_results(results, mode=mode, top_k=top_k, backend="sidecar")
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


def _load_sidecar_contexts(parsed_dir: Path) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    blocks_path = next(parsed_dir.glob("*.blocks.jsonl"), None)
    if blocks_path is None:
        raise FileNotFoundError(f"no *.blocks.jsonl found in {parsed_dir}")
    for line in blocks_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("type") != "content":
            continue
        contexts.append(
            {
                "kind": "text",
                "id": row.get("blockid", ""),
                "blockid": row.get("blockid", ""),
                "content": "\n".join(
                    str(value)
                    for value in (
                        row.get("heading", ""),
                        row.get("parent_headings", ""),
                        row.get("content", ""),
                    )
                    if value
                ),
            }
        )
    for kind, root_key, suffix in (
        ("table", "tables", ".tables.json"),
        ("drawing", "drawings", ".drawings.json"),
        ("equation", "equations", ".equations.json"),
    ):
        path = next(parsed_dir.glob(f"*{suffix}"), None)
        if not path:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item_id, item in (payload.get(root_key) or {}).items():
            contexts.append(
                {
                    "kind": kind,
                    "id": item_id,
                    "blockid": item.get("blockid", ""),
                    "content": "\n".join(
                        str(value)
                        for value in (
                            item_id,
                            item.get("heading", ""),
                            item.get("parent_headings", ""),
                            item.get("content", ""),
                            item.get("caption", ""),
                        )
                        if value
                    ),
                }
            )
    return contexts


def _rank_contexts(query: str, contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query_terms = _terms(query)
    ranked = []
    for context in contexts:
        content = str(context.get("content", ""))
        terms = _terms(content)
        overlap = len(query_terms & terms)
        score = overlap / math.sqrt(max(len(terms), 1))
        if query_terms and query_terms <= terms:
            score += 1.0
        ranked.append({**context, "score": score})
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def _terms(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_]+", text.lower()))


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


def _context_contains_fact(context: dict[str, Any], fact: Any) -> bool:
    content = str(context.get("content", ""))
    normalized_content = normalize_evidence(content)
    return (
        fact.fact_id in content
        or fact.answer in content
        or fact.expected_text in content
        or normalize_evidence(fact.answer) in normalized_content
        or normalize_evidence(fact.expected_text) in normalized_content
    )


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate LightRAG retrieval against oracle."
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--backend", choices=("api", "sidecar"), default="api")
    parser.add_argument("--parsed-dir", type=Path)
    parser.add_argument("--rag-api-url", default="http://127.0.0.1:9621")
    parser.add_argument("--mode", default="mix")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument(
        "--api-key", default=None, help="X-API-Key header for authenticated servers."
    )
    parser.add_argument(
        "--access-token",
        default=None,
        help="Bearer access token (Authorization header) for authenticated servers.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.backend == "sidecar":
        if args.parsed_dir is None:
            parser.error("--parsed-dir is required when --backend sidecar")
        report = evaluate_sidecar(
            dataset_source=args.dataset,
            parsed_dir=args.parsed_dir,
            mode=args.mode,
            top_k=args.top_k,
            max_cases=args.max_cases,
        )
    else:
        report = evaluate_api(
            dataset_source=args.dataset,
            rag_api_url=args.rag_api_url,
            mode=args.mode,
            top_k=args.top_k,
            max_cases=args.max_cases,
            api_key=args.api_key,
            access_token=args.access_token,
        )
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
        _write_envelope(args, report, "retrieval")
    print(output)
    return 0


def _write_envelope(args, report: dict, kind: str) -> None:
    from memory_eval_tests.experiments.common import (
        capture_environment,
        write_simple_envelope,
    )

    output_dir = args.output.parent
    summary = {
        key: value
        for key, value in report.items()
        if isinstance(value, (int, float, bool)) and key != "results"
    }
    write_simple_envelope(
        output_dir,
        kind="online",
        run_id=output_dir.name,
        experiment={
            "id": "online_retrieval",
            "label": "在线检索评测",
            "description": "通过 LightRAG API 检索 oracle 证据并计算 Recall@K / MRR。",
        },
        baseline={
            "mode": report.get("mode"),
            "top_k": report.get("top_k"),
            "backend": report.get("backend"),
        },
        environment=capture_environment(rag_api_url=getattr(args, "rag_api_url", None)),
        methods=[
            {
                "method": kind,
                "label": "检索结果",
                "params": {"top_k": report.get("top_k")},
                "summary": summary,
                "results": report.get("results", []),
            }
        ],
        status="complete",
        dataset_path=Path(args.dataset),
    )


if __name__ == "__main__":
    raise SystemExit(main())
