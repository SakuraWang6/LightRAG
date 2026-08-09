from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.common.sampling import sample_evenly


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
    questions = sample_evenly(oracle.get("questions", []), max_cases)
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
        "evidence_available": _average(results, "evidence_available"),
        "citation_presence": _average(results, "citation_presence"),
        "citation_correctness": _average(results, "citation_correctness"),
        "groundedness": sum(r["grounded"] for r in results) / total if total else 0.0,
        "ungrounded_rate": sum(r["ungrounded"] for r in results) / total if total else 0.0,
        "results": results,
    }


def score_answer(
    *,
    answer_text: str,
    expected: str,
    question: dict[str, Any],
    evidence_facts: list[dict[str, Any]],
    references_blob: str,
    evidence_available_override: bool | None = None,
) -> dict[str, bool | None]:
    question_type = question.get("question_type", "")
    expected_behavior = question.get("expected_behavior", "answer")
    exact = _answer_match(expected, answer_text, evidence_facts, question_type=question_type)
    evidence_available = (
        evidence_available_override
        if evidence_available_override is not None
        else _evidence_available(evidence_facts, references_blob)
    )
    citation_presence, citation_correctness = _citation_metrics(evidence_facts, answer_text)

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
        # Refusing an unanswerable question does not require a source citation.
        evidence_available = True
        citation_presence = False
        citation_correctness = None

    # Groundedness means the answer is correct and its oracle evidence was
    # supplied to the model.  It deliberately does not conflate availability
    # with an explicit citation in the generated prose.
    grounded = bool(exact and evidence_available)
    ungrounded = bool(expected_behavior == "abstain" and not abstention_correct)
    if expected_behavior != "abstain":
        ungrounded = not grounded

    return {
        "exact_match": bool(exact),
        "numeric_unit_correct": numeric_unit_correct,
        "formula_correct": formula_correct,
        "table_cell_correct": table_cell_correct,
        "abstention_correct": abstention_correct,
        "evidence_available": bool(evidence_available),
        "citation_presence": bool(citation_presence),
        "citation_correctness": citation_correctness,
        "grounded": grounded,
        "ungrounded": ungrounded,
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
    return bool(expected_compact) and expected_compact in answer_compact


def _answer_match(
    expected: str,
    answer_text: str,
    evidence_facts: list[dict[str, Any]],
    *,
    question_type: str,
) -> bool:
    normalized_answer = _normalize(answer_text)
    if expected and _normalize(expected) in normalized_answer:
        return True
    formula = _formula_fragment(expected)
    if formula:
        if not _formula_match(formula, answer_text):
            return False
        remainder = expected.replace(formula, " ", 1)
        numeric_pairs = re.findall(r"([-+]?\d+(?:\.\d+)?)\s*([A-Za-z%]+)", remainder)
        if numeric_pairs and not _numeric_unit_match(" ".join("".join(pair) for pair in numeric_pairs), answer_text):
            return False
        return _required_terms_present(remainder, answer_text)
    if question_type in {"direct_numeric", "table_cell"}:
        return _numeric_unit_match(expected, answer_text)
    if _numeric_unit_match(expected, answer_text) and not _required_terms(expected):
        return True
    return _required_terms_present(expected, answer_text)


def _formula_fragment(text: str) -> str | None:
    match = re.search(r"[A-Za-z]\s*_?\s*\{?\d+\}?\s*=\s*[^;\n]+", text)
    return match.group(0).strip() if match else None


def _required_terms(text: str) -> list[str]:
    ignored = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "is", "of", "on", "or", "the", "to", "with"}
    return [
        term
        for term in re.findall(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", text.lower())
        if term not in ignored and not re.fullmatch(r"\d+(?:\.\d+)?", term)
    ]


def _required_terms_present(expected: str, answer_text: str) -> bool:
    terms = _required_terms(expected)
    if not terms:
        return True
    compact_answer = _compact(answer_text)
    return all(_compact(term) in compact_answer for term in terms)


def _evidence_available(evidence_facts: list[dict[str, Any]], references_blob: str) -> bool:
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


def _citation_metrics(
    evidence_facts: list[dict[str, Any]], answer_text: str
) -> tuple[bool, bool | None]:
    """Score explicit stable-ID citations separately from available evidence.

    Answers may be grounded without mentioning a stable ID.  When a response
    does cite IDs, correctness requires every oracle fact ID to be cited.  The
    function intentionally avoids treating a matching answer value as a
    citation; otherwise numeric answers would make citation presence vacuous.
    """
    cited_ids = {item.upper() for item in re.findall(r"\b(?:FACT|OBJ)-\d{5}\b", answer_text, re.I)}
    if not cited_ids:
        return False, None
    expected_ids = {str(fact.get("fact_id", "")).upper() for fact in evidence_facts}
    if not expected_ids:
        return True, False
    return True, expected_ids <= cited_ids


def _average(results: list[dict[str, Any]], key: str) -> float | None:
    applicable = [row[key] for row in results if row.get(key) is not None]
    if not applicable:
        return None
    return sum(bool(value) for value in applicable) / len(applicable)


def _looks_like_abstain(text: str) -> bool:
    lowered = _normalize(text)
    patterns = (
        r"\b(?:is |are )?not (?:mentioned|provided|specified|stated)\b",
        r"\b(?:does|do) not (?:mention|provide|specify|state|contain)\b",
        r"\b(?:cannot|can't|can not) (?:be )?(?:determined|answered|addressed)\b",
        r"\b(?:unable to|insufficient information to|not enough information to) (?:determine|answer|address)\b",
        r"\binsufficient information\b",
        r"\b(?:document|context|provided information) (?:does not|do not) contain\b",
        r"(?:文档|上下文).{0,12}(?:没有|未提供|未提及|无法确定|无法回答)",
    )
    return any(re.search(pattern, lowered) is not None for pattern in patterns)


def _canonical_formula(text: str) -> str:
    """Canonicalize the limited algebra grammar used by the synthetic oracle.

    This is deliberately structural rather than token-set based: it preserves
    equality and division, converts only equivalent LaTex/Unicode spellings,
    and does not accept a bag of variable names as a formula match.
    """
    normalized = text.lower()
    normalized = normalized.replace("η", "eta")
    normalized = re.sub(r"\\(?:eta|mathrm\{eta\})", "eta", normalized)
    normalized = normalized.replace("\\left", "").replace("\\right", "")
    normalized = normalized.replace("\\times", "*").replace("\\cdot", "*")

    # Convert LaTex fractions before stripping braces. The operands can contain
    # subscript braces (for example ``P_{5}``), so a flat regex is insufficient.
    previous = None
    while previous != normalized:
        previous = normalized
        normalized = _replace_latex_fractions(normalized)

    normalized = re.sub(r"_\s*\{\s*([^{}]+?)\s*\}", r"_\1", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    # Multiplication signs and grouping parentheses are optional for the
    # single-product formulas in this dataset (P_5 T_5 == P_5*T_5).
    normalized = normalized.replace("*", "").replace("{", "").replace("}", "")
    normalized = normalized.replace("(", "").replace(")", "")
    return re.sub(r"[^a-z0-9_=/+\-.]+", "", normalized)


def _replace_latex_fractions(text: str) -> str:
    def group_end(start: int) -> tuple[str, int] | None:
        if start >= len(text) or text[start] != "{":
            return None
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return text[start + 1 : index], index + 1
        return None

    output = []
    index = 0
    while index < len(text):
        if not text.startswith("\\frac", index):
            output.append(text[index])
            index += 1
            continue
        numerator_start = index + len("\\frac")
        while numerator_start < len(text) and text[numerator_start].isspace():
            numerator_start += 1
        numerator = group_end(numerator_start)
        denominator = group_end(numerator[1]) if numerator else None
        if not numerator or not denominator:
            output.append(text[index])
            index += 1
            continue
        output.append(f"({numerator[0]})/({denominator[0]})")
        index = denominator[1]
    return "".join(output)




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
        _write_envelope(args, report)
    print(output)
    return 0


def _write_envelope(args, report: dict) -> None:
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
            "id": "online_answer",
            "label": "在线回答评测",
            "description": "通过 LightRAG API 回答 oracle 问题并计算准确率/groundedness/幻觉率等。",
        },
        baseline={
            "mode": report.get("mode"),
            "top_k": report.get("top_k"),
            "chunk_top_k": report.get("chunk_top_k"),
            "max_total_tokens": report.get("max_total_tokens"),
        },
        environment=capture_environment(rag_api_url=getattr(args, "rag_api_url", None)),
        methods=[
            {
                "method": "answer",
                "label": "回答评测",
                "params": {"top_k": report.get("top_k")},
                "summary": summary,
                "results": report.get("results", []),
            }
        ],
        status="complete",
    )


if __name__ == "__main__":
    raise SystemExit(main())
