from __future__ import annotations

import argparse
import json
import math
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memory_data_service.schemas import OraclePayload
from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.offline.object_traceability import _normalize_evidence


@dataclass
class RetrievalCaseResult:
    question_id: str
    recall_at_k: float
    reciprocal_rank: float
    expected: list[str]
    ranked_hits: list[str]


def evaluate_api(
    *,
    dataset_source: str,
    rag_api_url: str,
    mode: str = "mix",
    top_k: int = 10,
    max_cases: int | None = None,
) -> dict[str, Any]:
    oracle = DatasetClient(dataset_source).oracle()
    results: list[RetrievalCaseResult] = []
    questions = [
        question
        for question in oracle.get("questions", [])
        if question.get("expected_behavior") != "abstain"
    ]
    for question in _limit_cases(questions, max_cases):
        payload = {
            "query": question["question"],
            "mode": mode,
            "top_k": top_k,
            "chunk_top_k": top_k,
        }
        response = _post_json(f"{rag_api_url.rstrip('/')}/query/data", payload)
        search_space = json.dumps(response, ensure_ascii=False)
        expected_facts = [
            fact
            for fact in oracle.get("facts", [])
            if fact["fact_id"] in question.get("evidence_fact_ids", [])
        ]
        ranked_hits = [
            fact["fact_id"]
            for fact in expected_facts
            if _api_response_contains_fact(search_space, fact)
        ]
        recall = len(ranked_hits) / len(expected_facts) if expected_facts else 0.0
        rr = 1.0 if ranked_hits else 0.0
        results.append(
            RetrievalCaseResult(
                question_id=question["id"],
                recall_at_k=recall,
                reciprocal_rank=rr,
                expected=[fact["fact_id"] for fact in expected_facts],
                ranked_hits=ranked_hits,
            )
        )
    report = summarize(results, mode=mode, top_k=top_k)
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

    questions = [
        question
        for question in oracle.questions
        if question.expected_behavior != "abstain"
    ]
    for question in _limit_cases(questions, max_cases):
        ranked = _rank_contexts(question.question, contexts)
        top_contexts = ranked[:top_k]
        expected_facts = [
            facts_by_id[fact_id]
            for fact_id in question.evidence_fact_ids
            if fact_id in facts_by_id
        ]
        hits_by_fact: dict[str, int] = {}
        object_hits: set[str] = set()
        hit_context_count = 0
        for rank, context in enumerate(top_contexts, start=1):
            context_hit = False
            for fact in expected_facts:
                if _context_contains_fact(context, fact):
                    hits_by_fact.setdefault(fact.fact_id, rank)
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
                "context_precision": hit_context_count / len(top_contexts) if top_contexts else 0.0,
                "object_hit_rate": (
                    len(object_hits) / len(object_expected) if object_expected else 1.0
                ),
                "expected_fact_ids": [fact.fact_id for fact in expected_facts],
                "hit_fact_ids": sorted(hits_by_fact),
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


def summarize(results: list[RetrievalCaseResult], *, mode: str, top_k: int) -> dict[str, Any]:
    if not results:
        return {"mode": mode, "top_k": top_k, "cases": 0, "average_recall": 0.0, "mrr": 0.0}
    return {
        "mode": mode,
        "top_k": top_k,
        "cases": len(results),
        "average_recall": sum(r.recall_at_k for r in results) / len(results),
        "mrr": sum(r.reciprocal_rank for r in results) / len(results),
        "full_recall_cases": sum(r.recall_at_k == 1.0 for r in results),
        "results": [r.__dict__ for r in results],
    }


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
            "object_hit_rate": 0.0,
            "results": [],
        }
    return {
        "backend": backend,
        "mode": mode,
        "top_k": top_k,
        "cases": len(results),
        "average_recall": sum(r["recall_at_k"] for r in results) / len(results),
        "mrr": sum(r["reciprocal_rank"] for r in results) / len(results),
        "context_precision": sum(r["context_precision"] for r in results) / len(results),
        "object_hit_rate": sum(r["object_hit_rate"] for r in results) / len(results),
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


def _limit_cases(cases: list[Any], max_cases: int | None) -> list[Any]:
    if max_cases is None or max_cases <= 0 or len(cases) <= max_cases:
        return cases
    if max_cases == 1:
        return [cases[0]]
    step = (len(cases) - 1) / (max_cases - 1)
    indexes = sorted({round(index * step) for index in range(max_cases)})
    return [cases[index] for index in indexes]


def _context_contains_fact(context: dict[str, Any], fact: Any) -> bool:
    content = str(context.get("content", ""))
    normalized_content = _normalize_evidence(content)
    return (
        fact.fact_id in content
        or fact.answer in content
        or fact.expected_text in content
        or _normalize_evidence(fact.answer) in normalized_content
        or _normalize_evidence(fact.expected_text) in normalized_content
    )


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def _api_response_contains_fact(search_space: str, fact: dict[str, Any]) -> bool:
    normalized = _normalize_evidence(search_space)
    candidates = (
        fact.get("fact_id", ""),
        fact.get("answer", ""),
        fact.get("expected_text", ""),
    )
    return any(
        candidate
        and (
            candidate in search_space
            or _normalize_evidence(str(candidate)) in normalized
        )
        for candidate in candidates
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate LightRAG retrieval against oracle.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--backend", choices=("api", "sidecar"), default="api")
    parser.add_argument("--parsed-dir", type=Path)
    parser.add_argument("--rag-api-url", default="http://127.0.0.1:9621")
    parser.add_argument("--mode", default="mix")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--max-cases", type=int)
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
    )


if __name__ == "__main__":
    raise SystemExit(main())
