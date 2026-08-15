from __future__ import annotations

import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Protocol

from memory_eval_tests.dataset import DatasetClient
from memory_eval_tests.http import post_json as _http_post_json
from memory_eval_tests.review import build_review_queue
from memory_eval_tests.sampling import sample_evenly

SCORER_NAME = "deterministic-answer-rules"
SCORER_VERSION = "1.1"
_EVIDENCE_UNSET = object()

CONCISE_ANSWER_USER_PROMPT = (
    "直接、简洁地回答：第一句直接给出答案，只保留必要的依据说明，"
    "不要复述问题，不要写标题、前言、客套话或结尾总结。"
    "Answer directly and concisely without restating the question or "
    "adding headings or preamble."
)


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
    question_types: list[str] | None = None,
    api_key: str | None = None,
    access_token: str | None = None,
    evaluation_trace: bool = False,
    enable_rerank: bool = False,
    semantic_scorer: SemanticAnswerScorer | None = None,
    question_variant: str = "canonical",
    max_concurrency: int = 1,
    response_type: str = "Single Paragraph",
    user_prompt: str | None = CONCISE_ANSWER_USER_PROMPT,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    oracle = DatasetClient(dataset_source).oracle()
    facts_by_id = {fact["fact_id"]: fact for fact in oracle.get("facts", [])}
    questions = sample_evenly(oracle.get("questions", []), max_cases)
    if question_types:
        questions = [
            question
            for question in questions
            if question.get("question_type") in question_types
        ]
    total_questions = len(questions)

    def _evaluate_one(position: int, question: dict[str, Any]) -> dict[str, Any]:
        question_text = _question_variant(question, question_variant)
        payload = {
            "query": question_text,
            "mode": mode,
            "include_references": True,
            "include_chunk_content": True,
            "response_type": response_type,
            "evaluation_trace": evaluation_trace,
            "enable_rerank": enable_rerank,
        }
        if user_prompt:
            payload["user_prompt"] = user_prompt
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
        response_truncated = bool(response.get("response_truncated"))
        references_blob = json.dumps(response.get("references", []), ensure_ascii=False)
        expected = question.get("answer", "")
        evidence_ids = question.get("evidence_fact_ids", [])
        evidence_facts = [facts_by_id[fid] for fid in evidence_ids if fid in facts_by_id]
        final_context_trace = response.get("evaluation_trace")
        final_context_evidence = (
            _not_applicable_final_context_evidence()
            if question.get("expected_behavior") == "abstain"
            else _final_context_evidence(evidence_facts, final_context_trace)
        )
        scores = score_answer(
            answer_text=answer_text,
            expected=expected,
            question=question,
            evidence_facts=evidence_facts,
            references_blob=references_blob,
            # References expose candidate retrieval results only.  The
            # controlled trace is the sole proof of model-visible context.
            evidence_available_override=final_context_evidence["available"],
            semantic_scorer=semantic_scorer,
        )
        row = {
            "question_id": question["id"],
            # Persist the rendered question with its result so the WebUI
            # can present a self-contained review sheet without joining
            # back to the source dataset at display time.
            "question": question_text,
            **scores,
            "answer": answer_text,
            "response_truncated": response_truncated,
            "expected": expected,
            "question_type": question.get("question_type", ""),
            "expected_behavior": question.get("expected_behavior", "answer"),
            "question_variant": question_variant,
            "scenario_labels": question.get("scenario_labels", []),
            "response_type": response_type,
            "user_prompt": user_prompt,
            # Raw candidate references are used only by the deterministic
            # scorer above.  They are intentionally not persisted: they
            # can be megabytes of repeated chunk text and are not proof of
            # model-visible final context.
            "response_reference_count": len(response.get("references", []) or []),
            "final_context_trace": final_context_trace,
            "final_context_evidence": final_context_evidence,
        }
        if progress_callback:
            progress_callback(position, total_questions)
        return row

    concurrency = max(1, max_concurrency)
    if concurrency == 1:
        results = [
            _evaluate_one(position, question)
            for position, question in enumerate(questions, start=1)
        ]
    else:
        # /query is I/O-bound against the child server, so a small thread pool
        # keeps the local GPU busy across questions instead of waiting on each
        # answer serially.  Order is preserved for deterministic envelopes.
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            future_to_position = {
                pool.submit(_evaluate_one, position, question): position
                for position, question in enumerate(questions, start=1)
            }
            ordered: dict[int, dict[str, Any]] = {}
            for future in as_completed(future_to_position):
                ordered[future_to_position[future]] = future.result()
            results = [ordered[position] for position in sorted(ordered)]
    total = len(results)
    decisive = [row for row in results if row.get("answer_verdict") != "uncertain"]
    return {
        "mode": mode,
        "top_k": top_k,
        "chunk_top_k": chunk_top_k,
        "max_total_tokens": max_total_tokens,
        "cases": total,
        "correct_cases": sum(bool(r["exact_match"]) for r in results),
        "max_cases": max_cases,
        "question_variant": question_variant,
        "answer_accuracy": sum(bool(r["exact_match"]) for r in decisive) / len(decisive)
        if decisive
        else None,
        "answer_accuracy_denominator": len(decisive),
        "uncertain_answers": total - len(decisive),
        "generation_truncation_rate": _average(results, "response_truncated"),
        "numeric_unit_accuracy": _average(results, "numeric_unit_correct"),
        "formula_accuracy": _average(results, "formula_correct"),
        "table_cell_accuracy": _average(results, "table_cell_correct"),
        "abstention_accuracy": _average(results, "abstention_correct"),
        "evidence_available": _average(results, "evidence_available"),
        "final_context_observable_rate": _average_nested_bool(
            results, "final_context_evidence", "observable"
        ),
        "final_context_evidence_coverage": _average_nested_number(
            results, "final_context_evidence", "coverage"
        ),
        "final_context_evidence_available": _average_nested_bool(
            results, "final_context_evidence", "available"
        ),
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
    evidence_available_override: bool | None | object = _EVIDENCE_UNSET,
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
        _evidence_available(evidence_facts, references_blob)
        if evidence_available_override is _EVIDENCE_UNSET
        else evidence_available_override
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
    # supplied to the model. An unavailable trace is an observability gap, not
    # a positive or negative evidence judgement.
    if verdict == "uncertain":
        grounded: bool | None = None
    elif expected_behavior == "abstain":
        grounded = bool(abstention_correct)
    elif evidence_available is None:
        grounded = None
    else:
        grounded = bool(exact and evidence_available)
    if expected_behavior == "abstain":
        ungrounded: bool | None = not bool(abstention_correct)
    elif verdict == "uncertain" or grounded is None:
        ungrounded = None
    else:
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
    # Units may be Latin ("ms", "%", "QMU") or Chinese ("次/秒", "小时", "分",
    # "天").  The previous Latin-only pattern silently dropped Chinese units,
    # falling back to a whitespace-sensitive substring check that failed on
    # "114 次/秒" vs the model's "114次/秒".
    unit = r"[A-Za-z%]+|(?:[\u4e00-\u9fff]+/)*[\u4e00-\u9fff]+"
    expected_pairs = re.findall(
        rf"([-+]?\d+(?:\.\d+)?)\s*({unit})", expected
    )
    if not expected_pairs:
        return _compact(expected) in _compact(answer_text)
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


def _final_context_evidence(
    evidence_facts: list[dict[str, Any]], final_context_trace: Any
) -> dict[str, Any]:
    """Report which oracle facts actually reached the answer model.

    Candidate response references are deliberately not used as a fallback.
    Missing trace data is an observability gap, not an evidence miss.
    """
    expected_ids = [str(fact.get("fact_id") or "") for fact in evidence_facts]
    if not isinstance(final_context_trace, dict) or final_context_trace.get("status") != "observed":
        reason = (
            str(final_context_trace.get("reason") or "")
            if isinstance(final_context_trace, dict)
            else ""
        )
        return {
            "status": "unavailable",
            "observable": False,
            "available": None,
            "coverage": None,
            "expected_fact_ids": expected_ids,
            "hit_fact_ids": [],
            "missing_fact_ids": expected_ids,
            "reason": reason or "controlled final-context trace was not returned by the API",
        }
    context = str(final_context_trace.get("final_context") or "")
    normalized_context = _compact(context)
    hit_ids = [
        fact_id
        for fact_id, fact in zip(expected_ids, evidence_facts)
        if _fact_in_context(fact, context, normalized_context)
    ]
    missing_ids = [fact_id for fact_id in expected_ids if fact_id not in hit_ids]
    coverage = len(hit_ids) / len(expected_ids) if expected_ids else 1.0
    return {
        "status": "observed",
        "observable": True,
        "available": not missing_ids,
        "coverage": coverage,
        "expected_fact_ids": expected_ids,
        "hit_fact_ids": hit_ids,
        "missing_fact_ids": missing_ids,
        "context_chars": final_context_trace.get("final_context_chars"),
    }


def _not_applicable_final_context_evidence() -> dict[str, Any]:
    return {
        "status": "not_applicable",
        "observable": None,
        "available": None,
        "coverage": None,
        "expected_fact_ids": [],
        "hit_fact_ids": [],
        "missing_fact_ids": [],
    }


def _fact_in_context(fact: dict[str, Any], context: str, normalized_context: str) -> bool:
    # A bare FACT id is metadata, not proof that the answer-bearing value
    # reached the model.  Prefer the FACT-ID-anchored expected text, then the
    # answer value; this matches the stricter rule used by diagnosis.py.
    expected_text = str(fact.get("expected_text") or "")
    answer = str(fact.get("answer") or "")
    candidates = (expected_text, answer) if expected_text else (answer,)
    return any(
        candidate and (candidate in context or _compact(candidate) in normalized_context)
        for candidate in candidates
    )

def _citation_metrics(
    evidence_facts: list[dict[str, Any]], answer_text: str
) -> tuple[bool, bool | None]:
    """Score explicit stable-ID citations separately from available evidence.

    Answers may be grounded without mentioning a stable ID.  When a response
    does cite IDs, correctness requires every oracle fact ID to be cited.  The
    function intentionally avoids treating a matching answer value as a
    citation; otherwise numeric answers would make citation presence vacuous.
    """
    cited_ids = {item.upper() for item in re.findall(r"\b(?:FACT|OBJ)-\d{5}\b", answer_text, re.IGNORECASE)}
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


def _average_nested_bool(
    results: list[dict[str, Any]], parent_key: str, key: str
) -> float | None:
    values = [
        row[parent_key].get(key)
        for row in results
        if isinstance(row.get(parent_key), dict) and row[parent_key].get(key) is not None
    ]
    return sum(bool(value) for value in values) / len(values) if values else None


def _average_nested_number(
    results: list[dict[str, Any]], parent_key: str, key: str
) -> float | None:
    values = [
        float(row[parent_key][key])
        for row in results
        if isinstance(row.get(parent_key), dict)
        and isinstance(row[parent_key].get(key), (int, float))
    ]
    return sum(values) / len(values) if values else None


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
            "definition": "oracle 证据完整进入最终回答上下文的比例",
            "denominator": "有 oracle 证据的非拒答题",
            "scope": "受控 final-context trace",
            "limitation": "API 未返回 trace 时不可观测，不计为证据缺失",
        },
        "citation_correctness": {
            "definition": "回答中稳定 ID 引用覆盖 oracle 事实的比例",
            "denominator": "包含稳定 ID 引用的可回答题",
            "scope": "回答层",
            "limitation": "无引用时不可适用，不记为零",
        },
        "generation_truncation_rate": {
            "definition": "模型因输出 token 上限而返回不完整回答的比例",
            "denominator": "所有回答请求",
            "scope": "支持 provider 截断信号的非流式 /query 响应",
            "limitation": "未提供 provider 信号的模型不能据此证明回答完整",
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
        r"(?:文档|上下文).{0,15}(?:没有|不存在|未提供|未提及|未包含|找不到)",
        r"(?:无法|不能|难以)(?:提供|给出|确定|回答)",
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
