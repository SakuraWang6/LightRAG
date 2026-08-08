"""Controlled evidence-selection experiment on a frozen LightRAG KG index.

The runner deliberately reads the existing KG and its keyword cache, disables
the LightRAG query cache, and writes only to a new run directory.  It keeps
the answer prompt/model/decoding parameters fixed across all four methods.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.experiments.kg_ablation import DEFAULT_STORAGE, _find_rag, _load_keyword_cache, _query_param
from memory_eval_tests.online.answer_eval import score_answer


METHODS = (
    ("direct_top3", "Direct Top-3", 3),
    ("direct_top20", "Direct Top-20", 20),
    ("select3", "Top20 → Select3", 3),
    ("select5", "Top20 → Select5", 5),
)


def _split_prompt(prompt: str) -> tuple[str, str]:
    marker = "\n\n---User Query---\n"
    if marker not in prompt:
        raise ValueError("LightRAG prompt is missing the user-query marker")
    system, question = prompt.split(marker, 1)
    context_marker = "---Context---\n"
    if context_marker not in system:
        raise ValueError("LightRAG prompt is missing the context marker")
    prefix = system.split(context_marker, 1)[0]
    prefix += (
        "For this controlled evaluation, answer concisely in no more than three sentences "
        "before the required references section.\n\n"
    )
    return prefix + context_marker + "\n", question


def _entity_rows(prompt: str, *, limit: int) -> list[dict[str, Any]]:
    match = re.search(
        r"Knowledge Graph Data \(Entity\):\s*```json\s*(.*?)\s*```",
        prompt,
        flags=re.S,
    )
    if not match:
        return []
    rows = []
    for line in match.group(1).splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("entity"):
            rows.append(item)
        if len(rows) >= limit:
            break
    return rows


def _make_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for ordinal, row in enumerate(rows, start=1):
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
        candidates.append(
            {
                "evidence_id": f"EVD-E-{ordinal:02d}-{digest}",
                "object_type": str(row.get("type") or "UNKNOWN"),
                "entity": str(row.get("entity") or ""),
                "text": str(row.get("description") or ""),
                "raw": row,
            }
        )
    return candidates


def _render_context(candidates: list[dict[str, Any]]) -> str:
    rows = [item["raw"] for item in candidates]
    return "Knowledge Graph Data (Entity):\n\n```json\n" + "\n".join(
        json.dumps(row, ensure_ascii=False) for row in rows
    ) + "\n```\n\nKnowledge Graph Data (Relationship):\n\n```json\n\n```\n\nDocument Chunks:\n\n```json\n\n```\n"


def _contains_fact(candidate: dict[str, Any], fact: dict[str, Any]) -> bool:
    text = f"{candidate['entity']} {candidate['text']}".lower()
    markers = [
        str(fact.get("fact_id") or ""),
        str(fact.get("answer") or ""),
    ]
    return any(marker and marker.lower() in text for marker in markers)


def _oracle_candidate_ids(candidates: list[dict[str, Any]], facts: list[dict[str, Any]]) -> list[str]:
    return [
        item["evidence_id"]
        for item in candidates
        if any(_contains_fact(item, fact) for fact in facts)
    ]


def _chat_ollama(*, host: str, model: str, system: str, user: str, num_predict: int) -> str:
    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=json.dumps(
            {
                "model": model,
                "stream": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "options": {"temperature": 0, "num_ctx": 16384, "num_predict": num_predict},
                "think": False,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        body = json.loads(response.read().decode("utf-8"))
    return str((body.get("message") or {}).get("content") or "")


def _selector_prompt(question: str, candidates: list[dict[str, Any]], limit: int) -> str:
    rendered = [
        {
            "evidence_id": item["evidence_id"],
            "object_type": item["object_type"],
            "entity": item["entity"],
            "text": item["text"],
        }
        for item in candidates
    ]
    return (
        "You are an evidence selector. Do not answer the question. Select at most "
        f"{limit} evidence IDs that are sufficient and directly relevant. Prefer authoritative facts "
        "over distractors. Return ONLY strict JSON with this shape: "
        '{"selected_evidence_ids":["EVD-..."]}.\n\n'
        f"Question: {question}\n\nCandidates:\n{json.dumps(rendered, ensure_ascii=False)}"
    )


def _parse_selection(raw: str, candidates: list[dict[str, Any]], limit: int) -> list[str]:
    candidate_ids = {item["evidence_id"] for item in candidates}
    match = re.search(r"\{.*?\}", raw, flags=re.S)
    if match:
        try:
            parsed = json.loads(match.group(0))
            selected = parsed.get("selected_evidence_ids", [])
            if isinstance(selected, list):
                valid = [str(item) for item in selected if str(item) in candidate_ids]
                if valid:
                    return list(dict.fromkeys(valid))[:limit]
        except json.JSONDecodeError:
            pass
    # The fallback is only for malformed selector output and is recorded in raw
    # output. It preserves a runnable/reviewable experiment rather than silently
    # giving the answer model all candidates.
    return [item["evidence_id"] for item in candidates[:limit]]


def _group(question: dict[str, Any]) -> str:
    if question.get("expected_behavior") == "abstain":
        return "ABSTAIN"
    kind = str(question.get("question_type", "")).lower()
    if "multi" in kind or "cross" in kind:
        return "MULTIHOP"
    if "table" in kind:
        return "TABLE"
    if "figure" in kind or "fig" in kind:
        return "FIGURE"
    if "equation" in kind or "formula" in kind:
        return "FORMULA"
    return "FACT"


def _average(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row[key] for row in rows if row.get(key) is not None]
    return sum(bool(value) for value in values) / len(values) if values else None


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    metric = lambda key: sum(bool(row.get(key)) for row in rows) / total if total else 0.0
    grouped: dict[str, dict[str, Any]] = {}
    for name in ("FACT", "TABLE", "FIGURE", "FORMULA", "MULTIHOP", "ABSTAIN"):
        subset = [row for row in rows if row["question_group"] == name]
        if subset:
            grouped[name] = {"cases": len(subset), "answer_accuracy": metric_for(subset, "exact_match"), "groundedness": metric_for(subset, "grounded")}
    return {
        "cases": total,
        "answer_accuracy": metric("exact_match"),
        "groundedness": metric("grounded"),
        "hallucination_rate": metric("hallucinated"),
        "abstention_accuracy": _average(rows, "abstention_correct"),
        "numeric_unit_accuracy": _average(rows, "numeric_unit_correct"),
        "formula_accuracy": _average(rows, "formula_correct"),
        "table_cell_accuracy": _average(rows, "table_cell_correct"),
        "citation_presence": metric("citation_presence"),
        "citation_correctness": _average(rows, "citation_correctness"),
        "candidate_recall": sum(row["candidate_recall"] for row in rows) / total if total else 0.0,
        "selected_recall": sum(row["selected_recall"] for row in rows) / total if total else 0.0,
        "selection_precision": _average(rows, "selection_precision"),
        "mean_candidate_context_chars": sum(row["candidate_context_chars"] for row in rows) / total if total else 0.0,
        "mean_selected_context_chars": sum(row["selected_context_chars"] for row in rows) / total if total else 0.0,
        "mean_evidence_count": sum(row["evidence_count"] for row in rows) / total if total else 0.0,
        "by_question_type": grouped,
    }


def metric_for(rows: list[dict[str, Any]], key: str) -> float:
    return sum(bool(row.get(key)) for row in rows) / len(rows) if rows else 0.0


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Evidence Selector / Reranker Experiment",
        "",
        "All methods use the same dataset, existing KG index, cached keywords, qwen3:8b answer model, 16,384-token generation window, temperature 0, and answer prompt. The selector alone receives Top-20 candidates; its debug rationale/output is never inserted into the answer context.",
        "",
        "The LightRAG `mix` retrieval renderer expands an engine `top_k=20` request into several entity/relationship rows. For a bounded, stable selector input, this experiment uses the first 20 ranked entity rows emitted by that renderer as the candidate pool. `candidate_recall` and `selection_precision` are object-level proxies based on oracle FACT IDs/answers occurring in those rows.",
        "",
        "## Core result table",
        "",
        "| Method | Candidate K | Selected K | Candidate Recall | Selected Recall | Selection Precision | Avg Context (chars) | Accuracy | Groundedness | Hallucination |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in payload["methods"]:
        summary = item["summary"]
        selected_k = "≤" + str(item["selected_limit"]) if item["selector"] else str(item["selected_limit"])
        lines.append(
            f"| {item['label']} | {item['candidate_k']} | {selected_k} | {summary['candidate_recall']:.4f} | {summary['selected_recall']:.4f} | "
            f"{_format(summary['selection_precision'])} | {summary['mean_selected_context_chars']:.0f} | {summary['answer_accuracy']:.4f} | {summary['groundedness']:.4f} | {summary['hallucination_rate']:.4f} |"
        )
    lines.extend(["", "## Per-type answer metrics", ""])
    for item in payload["methods"]:
        lines.extend([f"### {item['label']}", "", "| Type | Cases | Accuracy | Groundedness |", "|---|---:|---:|---:|"])
        for name, row in item["summary"]["by_question_type"].items():
            lines.append(f"| {name} | {row['cases']} | {row['answer_accuracy']:.4f} | {row['groundedness']:.4f} |")
        lines.append("")
    lines.extend(["## Reproducibility", "", "- Historical KG storage and cached keywords were reused; query-cache writes were disabled.", "- Raw selector outputs and raw answers are retained in the JSON artifact for each question.", "- No historical result file was modified.", ""])
    return "\n".join(lines)


def _format(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    oracle = DatasetClient(str(args.dataset)).oracle()
    questions = list(oracle["questions"])
    if args.max_cases > 0:
        questions = questions[: args.max_cases]
    facts_by_id = {fact["fact_id"]: fact for fact in oracle["facts"]}
    cache = _load_keyword_cache(args.storage_dir)
    missing = [q["id"] for q in questions if q["question"] not in cache]
    if missing:
        raise RuntimeError(f"Missing cached keywords: {', '.join(missing)}")
    rag = _find_rag()
    await rag.initialize_storages()
    rag.llm_response_cache.global_config["enable_llm_cache"] = False
    method_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if args.resume and args.output_json.exists():
        saved = json.loads(args.output_json.read_text(encoding="utf-8"))
        for item in saved.get("methods", []):
            method_rows[str(item.get("method"))].extend(item.get("results", []))
    complete_ids = {
        row["question_id"]
        for method, _, _ in METHODS
        for row in method_rows[method]
    }
    completed_all_methods = {
        question_id
        for question_id in complete_ids
        if all(question_id in {row["question_id"] for row in method_rows[method]} for method, _, _ in METHODS)
    }

    def current_payload() -> dict[str, Any]:
        methods = []
        for method, label, selected_limit in METHODS:
            rows = method_rows[method]
            methods.append(
                {
                    "method": method,
                    "label": label,
                    "candidate_k": 3 if method == "direct_top3" else 20,
                    "selected_limit": selected_limit,
                    "selector": method.startswith("select"),
                    "summary": _aggregate(rows),
                    "results": rows,
                }
            )
        return {
            "dataset": str(args.dataset),
            "storage_dir": str(args.storage_dir),
            "retrieval_mode": "mix",
            "model": args.model,
            "ollama_url": args.ollama_url,
            "generation": {"temperature": 0, "num_ctx": 16384, "num_predict": 128},
            "status": "complete" if len(completed_all_methods) == len(questions) else "in_progress",
            "methods": methods,
        }

    try:
        for index, question in enumerate(questions, start=1):
            if question["id"] in completed_all_methods:
                continue
            text = str(question["question"])
            high, low = cache[text]
            top20_prompt = await rag.aquery(text, param=_query_param(top_k=20, high_keywords=high, low_keywords=low, prompt_only=True))
            top3_prompt = await rag.aquery(text, param=_query_param(top_k=3, high_keywords=high, low_keywords=low, prompt_only=True))
            prefix, user = _split_prompt(str(top20_prompt))
            top20 = _make_candidates(_entity_rows(str(top20_prompt), limit=20))
            top3 = _make_candidates(_entity_rows(str(top3_prompt), limit=3))
            if not top20 or not top3:
                raise RuntimeError(f"No entity candidates parsed for {question['id']}")
            evidence_facts = [facts_by_id[fid] for fid in question.get("evidence_fact_ids", []) if fid in facts_by_id]
            selections: dict[str, tuple[list[dict[str, Any]], str]] = {
                "direct_top3": (top3, ""),
                "direct_top20": (top20, ""),
            }
            for method, _, limit in METHODS[2:]:
                raw_selector = _chat_ollama(
                    host=args.ollama_url,
                    model=args.model,
                    system="Follow the requested JSON schema exactly.",
                    user=_selector_prompt(text, top20, limit),
                    num_predict=128,
                )
                ids = _parse_selection(raw_selector, top20, limit)
                selections[method] = ([item for item in top20 if item["evidence_id"] in ids], raw_selector)
            for method, label, selected_limit in METHODS:
                selected, selector_raw = selections[method]
                candidate_pool = top20 if method != "direct_top3" else top3
                candidate_context = _render_context(candidate_pool)
                selected_context = _render_context(selected)
                answer = _chat_ollama(
                    host=args.ollama_url,
                    model=args.model,
                    system=prefix + selected_context,
                    user=user,
                    num_predict=256,
                )
                selected_ids = [item["evidence_id"] for item in selected]
                oracle_fact_ids = [str(item["fact_id"]) for item in evidence_facts]
                candidate_oracle_ids = _oracle_candidate_ids(candidate_pool, evidence_facts)
                matched_candidate_facts = [
                    str(fact["fact_id"])
                    for fact in evidence_facts
                    if any(_contains_fact(candidate, fact) for candidate in candidate_pool)
                ]
                matched_selected_facts = [
                    str(fact["fact_id"])
                    for fact in evidence_facts
                    if any(_contains_fact(candidate, fact) for candidate in selected)
                ]
                relevant_selected = [item for item in selected_ids if item in candidate_oracle_ids]
                denominator = len(evidence_facts)
                scores = score_answer(
                    answer_text=answer,
                    expected=str(question.get("answer", "")),
                    question=question,
                    evidence_facts=evidence_facts,
                    references_blob=selected_context,
                )
                method_rows[method].append(
                    {
                        "question_id": question["id"],
                        "question": text,
                        "question_type": question.get("question_type", ""),
                        "question_group": _group(question),
                        "oracle_evidence_ids": oracle_fact_ids,
                        "candidate_evidence_ids": [item["evidence_id"] for item in candidate_pool],
                        "selected_evidence_ids": selected_ids,
                        "candidate_oracle_evidence_ids": candidate_oracle_ids,
                        "candidate_matched_fact_ids": matched_candidate_facts,
                        "selected_matched_fact_ids": matched_selected_facts,
                        "candidate_recall": len(matched_candidate_facts) / denominator if denominator else 1.0,
                        "selected_recall": len(matched_selected_facts) / denominator if denominator else 1.0,
                        "selection_precision": len(relevant_selected) / len(selected_ids) if selected_ids and denominator else None,
                        "evidence_retention_rate": len(selected_ids) / len(candidate_pool),
                        "candidate_context_chars": len(candidate_context),
                        "selected_context_chars": len(selected_context),
                        "estimated_tokens": (len(selected_context) + 3) // 4,
                        "evidence_count": len(selected_ids),
                        "evidence_density_proxy": len(relevant_selected) / len(selected_ids) if selected_ids and denominator else None,
                        "candidate_context": candidate_context,
                        "selected_context": selected_context,
                        "selector_raw_output": selector_raw or None,
                        "answer": answer,
                        "expected": question.get("answer", ""),
                        **scores,
                    }
                )
            completed_all_methods.add(question["id"])
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(
                json.dumps(current_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"[{index}/{len(questions)}] {question['id']}", flush=True)
    finally:
        await rag.finalize_storages()
    return current_payload()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("memory_data_service/generated/rich-smoke-v1"))
    parser.add_argument("--storage-dir", type=Path, default=DEFAULT_STORAGE)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    payload = asyncio.run(_run(args))
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(_render_report(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
