from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Protocol

from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.common.http import post_json as _http_post_json
from memory_eval_tests.common.sampling import sample_evenly
from memory_eval_tests.online.review import build_review_queue

SCORER_NAME = "deterministic-answer-rules"
SCORER_VERSION = "1.0"


class SemanticAnswerScorer(Protocol):
    """Optional pluggable scorer for valid non-literal answer expressions."""

    name: str
    version: str

    def score(
        self, *, answer_text: str, expected: str, question: dict[str, Any]
    ) -> tuple[str, str]:
        """Return a ``pass``/``fail``/``uncertain`` verdict and its reason."""


def evaluate_answers(
    *,
    dataset_source: str,
    rag_api_url: str,
    mode: str = "mix",
    top_k: int | None = None,
    chunk_top_k: int | None = None,
    max_total_tokens: int | None = None,
    max_cases: int | None = None,
    api_key: str | None = None,
    access_token: str | None = None,
    evaluation_trace: bool = False,
    semantic_scorer: SemanticAnswerScorer | None = None,
    question_variant: str = "canonical",
) -> dict[str, Any]:
    oracle = DatasetClient(dataset_source).oracle()
    facts_by_id = {fact["fact_id"]: fact for fact in oracle.get("facts", [])}
    results = []
    questions = sample_evenly(oracle.get("questions", []), max_cases)
    for question in questions:
        question_text = _question_variant(question, question_variant)
        payload = {
            "query": question_text,
            "mode": mode,
            "include_references": True,
            "include_chunk_content": True,
            "response_type": "Multiple Paragraphs",
            "evaluation_trace": evaluation_trace,
        }
        if top_k is not None:
            payload["top_k"] = top_k
        if chunk_top_k is not None:
            payload["chunk_top_k"] = chunk_top_k
        if max_total_tokens is not None:
            payload["max_total_tokens"] = max_total_tokens
        response = _post_json(
            f"{rag_api_url.rstrip('/')}/query",
            payload,
            api_key=api_key,
            access_token=access_token,
        )
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
            semantic_scorer=semantic_scorer,
        )
        results.append(
            {
                "question_id": question["id"],
                **scores,
                "answer": answer_text,
                "expected": expected,
                "question_type": question.get("question_type", ""),
                "question_variant": question_variant,
                "scenario_labels": question.get("scenario_labels", []),
                # References are kept as a response-side observation only. They
                # are not used as proof of the final prompt context (I2).
                "response_references": response.get("references", []),
                "final_context_trace": response.get("evaluation_trace"),
            }
        )
    total = len(results)
    decisive = [row for row in results if row.get("answer_verdict") != "uncertain"]
    return {
        "mode": mode,
        "top_k": top_k,
        "chunk_top_k": chunk_top_k,
        "max_total_tokens": max_total_tokens,
        "cases": total,
        "max_cases": max_cases,
        "question_variant": question_variant,
        "answer_accuracy": sum(bool(r["exact_match"]) for r in decisive) / len(decisive)
        if decisive
        else None,
        "answer_accuracy_denominator": len(decisive),
        "uncertain_answers": total - len(decisive),
        "numeric_unit_accuracy": _average(results, "numeric_unit_correct"),
        "formula_accuracy": _average(results, "formula_correct"),
        "table_cell_accuracy": _average(results, "table_cell_correct"),
        "abstention_accuracy": _average(results, "abstention_correct"),
        "evidence_available": _average(results, "evidence_available"),
        "citation_presence": _average(results, "citation_presence"),
        "citation_correctness": _average(results, "citation_correctness"),
        "groundedness": _rate(results, "grounded"),
        "ungrounded_rate": _rate(results, "ungrounded"),
        "by_scenario": _stratify(results, "scenario_labels"),
        "by_question_type": _stratify(results, "question_type"),
        "metric_definitions": _metric_definitions(),
        "scorers": _scorer_inventory(results),
        "review_queue": build_review_queue(results),
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
    semantic_scorer: SemanticAnswerScorer | None = None,
) -> dict[str, Any]:
    question_type = question.get("question_type", "")
    expected_behavior = question.get("expected_behavior", "answer")
    deterministic_exact = _answer_match(expected, answer_text, evidence_facts, question_type=question_type)
    scoring_mode = question.get("scoring_mode", "deterministic")
    verdict = "pass" if deterministic_exact else "fail"
    scorer_name, scorer_version = SCORER_NAME, SCORER_VERSION
    reason = "deterministic answer rule matched" if deterministic_exact else "deterministic answer rule did not match"
    if scoring_mode in {"semantic", "hybrid"} and not deterministic_exact:
        if semantic_scorer is None:
            verdict = "uncertain"
            reason = "semantic scoring required but no semantic scorer is configured"
        else:
            verdict, reason = semantic_scorer.score(
                answer_text=answer_text, expected=expected, question=question
            )
            if verdict not in {"pass", "fail", "uncertain"}:
                raise ValueError("semantic scorer must return pass, fail, or uncertain")
            scorer_name, scorer_version = semantic_scorer.name, semantic_scorer.version
    exact = verdict == "pass"
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
        verdict = "pass" if abstention_correct else "fail"
        reason = "deterministic abstention rule matched" if abstention_correct else "deterministic abstention rule did not match"
        # Refusing an unanswerable question has no oracle evidence and needs no
        # citation.  Keep evidence_available as None so abstain questions are
        # excluded from the evidence-availability rate instead of inflating it.
        evidence_available = None
        citation_presence = False
        citation_correctness = None

    # Groundedness means the answer is correct and its oracle evidence was
    # supplied to the model; a correct abstain is grounded without evidence.
    grounded: bool | None = (
        None
        if verdict == "uncertain"
        else bool(exact and (True if evidence_available is None else evidence_available))
    )
    ungrounded = bool(expected_behavior == "abstain" and not abstention_correct)
    if expected_behavior != "abstain" and verdict != "uncertain":
        ungrounded = not grounded

    return {
        "exact_match": bool(exact),
        "answer_verdict": verdict,
        "review_required": verdict == "uncertain",
        "scorer": {
            "name": scorer_name,
            "version": scorer_version,
            "mode": scoring_mode,
            "reason": reason,
        },
        "numeric_unit_correct": numeric_unit_correct,
        "formula_correct": formula_correct,
        "table_cell_correct": table_cell_correct,
        "abstention_correct": abstention_correct,
        "evidence_available": evidence_available,
        "citation_presence": bool(citation_presence),
        "citation_correctness": citation_correctness,
        "grounded": grounded,
        "ungrounded": ungrounded,
    }


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _question_variant(question: dict[str, Any], variant: str) -> str:
    if variant == "canonical":
        return str(question["question"])
    variants = question.get("question_variants") or {}
    text = variants.get(variant)
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            f"question {question.get('id', '<unknown>')} has no {variant!r} variant"
        )
    return text


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


def _stratify(results: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        labels = row.get(key)
        labels = labels if isinstance(labels, list) else [labels]
        for label in labels:
            if isinstance(label, str) and label:
                groups.setdefault(label, []).append(row)
    return {
        label: {
            "cases": len(rows),
            "decisive_cases": sum(row.get("answer_verdict") != "uncertain" for row in rows),
            "uncertain": sum(row.get("answer_verdict") == "uncertain" for row in rows),
            "answer_accuracy": _rate(rows, "exact_match"),
            "groundedness": _rate(rows, "grounded"),
        }
        for label, rows in sorted(groups.items())
    }


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    applicable = [row.get(key) for row in rows if row.get(key) is not None]
    return sum(bool(value) for value in applicable) / len(applicable) if applicable else None


def _metric_definitions() -> dict[str, dict[str, str]]:
    return {
        "answer_accuracy": {
            "definition": "回答被评分器判为 pass 的比例",
            "denominator": "所有非 uncertain 的回答",
            "scope": "所有可回答题；语义待复核题不计入分母",
            "limitation": "不代表证据是否进入最终上下文",
        },
        "evidence_available": {
            "definition": "oracle 证据在候选检索引用中可见的比例",
            "denominator": "有 oracle 证据的非拒答题",
            "scope": "检索候选层",
            "limitation": "不证明证据进入最终上下文或回答引用正确",
        },
        "citation_correctness": {
            "definition": "回答中稳定 ID 引用覆盖 oracle 事实的比例",
            "denominator": "包含稳定 ID 引用的可回答题",
            "scope": "回答层",
            "limitation": "无引用时不可适用，不记为零",
        },
    }


def _scorer_inventory(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen = {
        (str((row.get("scorer") or {}).get("name")), str((row.get("scorer") or {}).get("version")))
        for row in results
    }
    return [{"name": name, "version": version} for name, version in sorted(seen)]


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




def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str | None = None,
    access_token: str | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    return _http_post_json(
        url,
        payload,
        api_key=api_key,
        access_token=access_token,
        timeout=timeout,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate LightRAG answers against oracle.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--rag-api-url", default="http://127.0.0.1:9621")
    parser.add_argument("--mode", default="mix")
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--chunk-top-k", type=int)
    parser.add_argument("--max-total-tokens", type=int)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--question-variant", default="canonical")
    parser.add_argument("--api-key", default=None, help="X-API-Key header for authenticated servers.")
    parser.add_argument(
        "--access-token",
        default=None,
        help="Bearer access token (Authorization header) for authenticated servers.",
    )
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
        api_key=args.api_key,
        access_token=args.access_token,
        question_variant=args.question_variant,
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
        dataset_path=Path(args.dataset),
    )


if __name__ == "__main__":
    raise SystemExit(main())
