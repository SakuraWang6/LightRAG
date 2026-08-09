"""Controlled evidence-selection experiment on a frozen LightRAG KG index.

The runner deliberately reads the existing KG and its keyword cache, disables
the LightRAG query cache, and writes only to a new run directory.  It keeps
the answer prompt/model/decoding parameters fixed across all four methods.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.experiments.common.rag_session import (
    DEFAULT_STORAGE,
    find_rag,
    load_keyword_cache,
    query_param,
)
from memory_eval_tests.experiments.common.selectors import (
    contains_fact,
    entity_rows,
    group,
    make_candidates,
    oracle_candidate_ids,
    parse_selection,
    render_context,
    selector_prompt,
    simple_chat_ollama,
    split_prompt,
)
from memory_eval_tests.experiments.legacy_adapter import legacy_spec
from memory_eval_tests.online.answer_eval import score_answer

METHODS = (
    ("direct_top3", "Direct Top-3", 3),
    ("direct_top20", "Direct Top-20", 20),
    ("select3", "Top20 → Select3", 3),
    ("select5", "Top20 → Select5", 5),
)


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
        "ungrounded_rate": metric("ungrounded"),
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
            f"{_format(summary['selection_precision'])} | {summary['mean_selected_context_chars']:.0f} | {summary['answer_accuracy']:.4f} | {summary['groundedness']:.4f} | {summary['ungrounded_rate']:.4f} |"
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
    cache = load_keyword_cache(args.storage_dir)
    missing = [q["id"] for q in questions if q["question"] not in cache]
    if missing:
        raise RuntimeError(f"Missing cached keywords: {', '.join(missing)}")
    rag = find_rag()
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
            top20_prompt = await rag.aquery(text, param=query_param(top_k=20, high_keywords=high, low_keywords=low, prompt_only=True))
            top3_prompt = await rag.aquery(text, param=query_param(top_k=3, high_keywords=high, low_keywords=low, prompt_only=True))
            prefix, user = split_prompt(str(top20_prompt))
            top20 = make_candidates(entity_rows(str(top20_prompt), limit=20))
            top3 = make_candidates(entity_rows(str(top3_prompt), limit=3))
            if not top20 or not top3:
                raise RuntimeError(f"No entity candidates parsed for {question['id']}")
            evidence_facts = [facts_by_id[fid] for fid in question.get("evidence_fact_ids", []) if fid in facts_by_id]
            selections: dict[str, tuple[list[dict[str, Any]], str]] = {
                "direct_top3": (top3, ""),
                "direct_top20": (top20, ""),
            }
            for method, _, limit in METHODS[2:]:
                raw_selector = simple_chat_ollama(
                    host=args.ollama_url,
                    model=args.model,
                    system="Follow the requested JSON schema exactly.",
                    user=selector_prompt(text, top20, limit),
                    num_predict=128,
                )
                ids = parse_selection(raw_selector, top20, limit)
                selections[method] = ([item for item in top20 if item["evidence_id"] in ids], raw_selector)
            for method, label, selected_limit in METHODS:
                selected, selector_raw = selections[method]
                candidate_pool = top20 if method != "direct_top3" else top3
                candidate_context = render_context(candidate_pool)
                selected_context = render_context(selected)
                answer = simple_chat_ollama(
                    host=args.ollama_url,
                    model=args.model,
                    system=prefix + selected_context,
                    user=user,
                    num_predict=256,
                )
                selected_ids = [item["evidence_id"] for item in selected]
                oracle_fact_ids = [str(item["fact_id"]) for item in evidence_facts]
                candidate_oracle_ids = oracle_candidate_ids(candidate_pool, evidence_facts)
                matched_candidate_facts = [
                    str(fact["fact_id"])
                    for fact in evidence_facts
                    if any(contains_fact(candidate, fact) for candidate in candidate_pool)
                ]
                matched_selected_facts = [
                    str(fact["fact_id"])
                    for fact in evidence_facts
                    if any(contains_fact(candidate, fact) for candidate in selected)
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
                        "question_group": group(question),
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


spec = legacy_spec(
    experiment_id="evidence_selector",
    label="证据选择消融（Top20→Select）",
    description=(
        "冻结 KG 索引上的候选选择消融：Direct Top-3/Top-20 与 "
        "Top20→Select3/Select5，固定答案模型与生成参数。"
    ),
    run=_run,
    artifact_stem="evidence_selector",
    render_report=_render_report,
)
