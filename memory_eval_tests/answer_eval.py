from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from memory_eval_tests.dataset_client import DatasetClient


def evaluate_answers(
    *,
    dataset_source: str,
    rag_api_url: str,
    mode: str = "mix",
    top_k: int | None = None,
    chunk_top_k: int | None = None,
    max_total_tokens: int | None = None,
    max_cases: int | None = None,
) -> dict[str, Any]:
    oracle = DatasetClient(dataset_source).oracle()
    facts_by_id = {fact["fact_id"]: fact for fact in oracle.get("facts", [])}
    results = []
    questions = oracle.get("questions", [])
    if max_cases is not None and max_cases > 0:
        questions = questions[:max_cases]
    for question in questions:
        payload = {
            "query": question["question"],
            "mode": mode,
            "include_references": True,
            "include_chunk_content": True,
            "response_type": "Multiple Paragraphs",
        }
        if top_k is not None:
            payload["top_k"] = top_k
        if chunk_top_k is not None:
            payload["chunk_top_k"] = chunk_top_k
        if max_total_tokens is not None:
            payload["max_total_tokens"] = max_total_tokens
        response = _post_json(f"{rag_api_url.rstrip('/')}/query", payload)
        answer_text = str(response.get("response") or response.get("content") or "")
        references_blob = json.dumps(response.get("references", []), ensure_ascii=False)
        expected = question.get("answer", "")
        evidence_ids = question.get("evidence_fact_ids", [])
        evidence_facts = [facts_by_id[fid] for fid in evidence_ids if fid in facts_by_id]
        scores = score_answer(
            answer_text=answer_text,
            expected=expected,
            question=question,
            evidence_facts=evidence_facts,
            references_blob=references_blob,
        )
        results.append(
            {
                "question_id": question["id"],
                **scores,
                "answer": answer_text,
                "expected": expected,
            }
        )
    total = len(results)
    return {
        "mode": mode,
        "top_k": top_k,
        "chunk_top_k": chunk_top_k,
        "max_total_tokens": max_total_tokens,
        "cases": total,
        "max_cases": max_cases,
        "answer_accuracy": sum(r["exact_match"] for r in results) / total if total else 0.0,
        "numeric_unit_accuracy": _average(results, "numeric_unit_correct"),
        "formula_accuracy": _average(results, "formula_correct"),
        "table_cell_accuracy": _average(results, "table_cell_correct"),
        "abstention_accuracy": _average(results, "abstention_correct"),
        "citation_accuracy": sum(r["citation_correct"] for r in results) / total if total else 0.0,
        "groundedness": sum(r["grounded"] for r in results) / total if total else 0.0,
        "hallucination_rate": sum(r["hallucinated"] for r in results) / total if total else 0.0,
        "results": results,
    }


def score_answer(
    *,
    answer_text: str,
    expected: str,
    question: dict[str, Any],
    evidence_facts: list[dict[str, Any]],
    references_blob: str,
) -> dict[str, bool | None]:
    question_type = question.get("question_type", "")
    expected_behavior = question.get("expected_behavior", "answer")
    exact = _answer_match(expected, answer_text, evidence_facts)
    citation_correct = _citation_correct(evidence_facts, references_blob)

    numeric_unit_correct = None
    formula_correct = None
    table_cell_correct = None
    abstention_correct = None

    if question_type in {"direct_numeric", "table_cell"}:
        numeric_unit_correct = _numeric_unit_match(expected, answer_text)
    if question_type == "formula" or question_type == "equation":
        formula_correct = _formula_match(expected, answer_text)
    if question_type == "table_cell":
        table_cell_correct = exact or bool(numeric_unit_correct)
    if expected_behavior == "abstain":
        abstention_correct = _looks_like_abstain(answer_text)
        exact = abstention_correct
        citation_correct = True

    grounded = bool(exact and citation_correct)
    hallucinated = bool(expected_behavior == "abstain" and not abstention_correct)
    if expected_behavior != "abstain":
        hallucinated = not grounded

    return {
        "exact_match": bool(exact),
        "numeric_unit_correct": numeric_unit_correct,
        "formula_correct": formula_correct,
        "table_cell_correct": table_cell_correct,
        "abstention_correct": abstention_correct,
        "citation_correct": bool(citation_correct),
        "grounded": grounded,
        "hallucinated": hallucinated,
    }


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "", text.lower())


def _numeric_unit_match(expected: str, answer_text: str) -> bool:
    expected_pairs = re.findall(r"([-+]?\d+(?:\.\d+)?)\s*([A-Za-z%]+)", expected)
    if not expected_pairs:
        return _normalize(expected) in _normalize(answer_text)
    answer_compact = _compact(answer_text)
    for number, unit in expected_pairs:
        if _compact(number + unit) not in answer_compact:
            return False
    return True


def _formula_match(expected: str, answer_text: str) -> bool:
    expected_compact = _canonical_formula(expected)
    answer_compact = _canonical_formula(answer_text)
    if expected_compact and expected_compact in answer_compact:
        return True
    expected_tokens = _formula_tokens(expected)
    answer_tokens = _formula_tokens(answer_text)
    return bool(expected_tokens) and expected_tokens <= answer_tokens


def _answer_match(
    expected: str,
    answer_text: str,
    evidence_facts: list[dict[str, Any]],
) -> bool:
    normalized_answer = _normalize(answer_text)
    if expected and _normalize(expected) in normalized_answer:
        return True
    if _numeric_unit_match(expected, answer_text):
        return True
    if _formula_match(expected, answer_text):
        return True
    fact_answers = [
        str(fact.get("answer", ""))
        for fact in evidence_facts
        if str(fact.get("answer", "")).strip()
    ]
    return bool(fact_answers) and all(
        _numeric_unit_match(answer, answer_text)
        or _normalize(answer) in normalized_answer
        or _compact(answer) in _compact(answer_text)
        for answer in fact_answers
    )


def _citation_correct(evidence_facts: list[dict[str, Any]], references_blob: str) -> bool:
    if not evidence_facts:
        return True
    normalized_refs = _compact(references_blob)
    hits = 0
    for fact in evidence_facts:
        candidates = (
            fact.get("fact_id", ""),
            fact.get("answer", ""),
            fact.get("expected_text", ""),
        )
        if any(candidate and _compact(candidate) in normalized_refs for candidate in candidates):
            hits += 1
    return hits == len(evidence_facts)


def _average(results: list[dict[str, Any]], key: str) -> float | None:
    applicable = [row[key] for row in results if row.get(key) is not None]
    if not applicable:
        return None
    return sum(bool(value) for value in applicable) / len(applicable)


def _looks_like_abstain(text: str) -> bool:
    lowered = text.lower()
    return any(
        phrase in lowered
        for phrase in (
            "does not provide",
            "do not have enough information",
            "not enough information",
            "not provided",
            "not mentioned",
            "cannot determine",
            "cannot answer",
            "unable to determine",
            "没有",
            "未提供",
            "无法确定",
        )
    )


def _canonical_formula(text: str) -> str:
    normalized = text.lower()
    normalized = normalized.replace("\\eta", "eta")
    normalized = normalized.replace("\\times", "")
    normalized = normalized.replace("\\cdot", "")
    normalized = normalized.replace("\\frac", "")
    return re.sub(r"[^a-z0-9=/+*\\-]+", "", normalized)


def _formula_tokens(text: str) -> set[str]:
    normalized = text.lower().replace("\\eta", "eta")
    normalized = re.sub(r"\\[a-z]+", " ", normalized)
    normalized = normalized.replace("_", "")
    return set(re.findall(r"[a-z]+(?:\{?\d+\}?)?", normalized))


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate LightRAG answers against oracle.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--rag-api-url", default="http://127.0.0.1:9621")
    parser.add_argument("--mode", default="mix")
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--chunk-top-k", type=int)
    parser.add_argument("--max-total-tokens", type=int)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = evaluate_answers(
        dataset_source=args.dataset,
        rag_api_url=args.rag_api_url,
        mode=args.mode,
        top_k=args.top_k,
        chunk_top_k=args.chunk_top_k,
        max_total_tokens=args.max_total_tokens,
        max_cases=args.max_cases,
    )
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
