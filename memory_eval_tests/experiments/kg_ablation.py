"""Run controlled KG-context ablations against an existing LightRAG index.

The runner deliberately reuses cached keyword extraction results from the
baseline index.  This means every run varies only the requested retrieval
depth (or, with ``--freeze-prompts``, no retrieval parameter at all), rather
than silently changing the query-rewrite model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lightrag import LightRAG
from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.experiments.common import ExperimentSpec, RunContext
from memory_eval_tests.experiments.common.rag_session import (
    find_rag,
    load_keyword_cache,
    query_param,
)
from memory_eval_tests.experiments.legacy_adapter import namespace_from_context
from memory_eval_tests.online.answer_eval import score_answer


def _fact_in_context(fact: dict[str, Any], context: str) -> bool:
    candidates = (
        str(fact.get("fact_id") or ""),
        str(fact.get("answer") or ""),
        str(fact.get("expected_text") or ""),
    )
    lowered = context.lower()
    return any(candidate and candidate.lower() in lowered for candidate in candidates)


async def freeze_prompts(
    rag: LightRAG,
    questions: list[dict[str, Any]],
    keyword_cache: dict[str, tuple[list[str], list[str]]],
) -> list[dict[str, Any]]:
    snapshots = []
    for question in questions:
        text = str(question["question"])
        high, low = keyword_cache[text]
        prompt = await rag.aquery(
            text,
            param=query_param(
                top_k=5,
                high_keywords=high,
                low_keywords=low,
                prompt_only=True,
            ),
        )
        snapshots.append(
            {
                "question_id": question["id"],
                "question": text,
                "expected": question.get("answer", ""),
                "question_type": question.get("question_type", ""),
                "expected_behavior": question.get("expected_behavior", "answer"),
                "evidence_fact_ids": question.get("evidence_fact_ids", []),
                "high_level_keywords": high,
                "low_level_keywords": low,
                "prompt": prompt,
            }
        )
    return snapshots


async def run_local_ablation(
    rag: LightRAG,
    *,
    questions: list[dict[str, Any]],
    facts_by_id: dict[str, dict[str, Any]],
    keyword_cache: dict[str, tuple[list[str], list[str]]],
    top_k: int,
    existing_rows: list[dict[str, Any]] | None = None,
    on_progress: Callable[[list[dict[str, Any]]], None] | None = None,
    per_query_timeout: int = 600,
) -> dict[str, Any]:
    rows = list(existing_rows or [])
    completed_ids = {str(row["question_id"]) for row in rows}
    for question in questions:
        if str(question["id"]) in completed_ids:
            continue
        text = str(question["question"])
        high, low = keyword_cache[text]
        # A stuck local inference previously left the whole ablation running
        # indefinitely and produced no usable checkpoint.  Bound both calls so
        # the partial report can be resumed after a transient Ollama failure.
        context = await asyncio.wait_for(
            rag.aquery(
                text,
                param=query_param(
                    top_k=top_k,
                    high_keywords=high,
                    low_keywords=low,
                    context_only=True,
                ),
            ),
            timeout=per_query_timeout,
        )
        answer = await asyncio.wait_for(
            rag.aquery(
                text,
                param=query_param(top_k=top_k, high_keywords=high, low_keywords=low),
            ),
            timeout=per_query_timeout,
        )
        evidence = [
            facts_by_id[fact_id]
            for fact_id in question.get("evidence_fact_ids", [])
            if fact_id in facts_by_id
        ]
        scores = score_answer(
            answer_text=str(answer),
            expected=str(question.get("answer", "")),
            question=question,
            evidence_facts=evidence,
            # Context is the frozen retrieval evidence.  This measures evidence
            # availability rather than response-format-specific API references.
            references_blob=str(context),
        )
        recall = (
            sum(_fact_in_context(fact, str(context)) for fact in evidence)
            / len(evidence)
            if evidence
            else 1.0
        )
        rows.append(
            {
                "question_id": question["id"],
                "retrieval_recall": recall,
                "context_chars": len(str(context)),
                "answer": str(answer),
                **scores,
            }
        )
        if on_progress:
            on_progress(rows)

    return _report_from_rows(rows, top_k=top_k)


def _report_from_rows(rows: list[dict[str, Any]], *, top_k: int) -> dict[str, Any]:
    """Build either a complete report or an explicitly partial checkpoint."""
    total = len(rows)
    abstention_total = sum(row["abstention_correct"] is not None for row in rows)
    return {
        "model": "qwen3:8b",
        "mode": "mix",
        "top_k": top_k,
        "chunk_top_k": top_k,
        "max_total_tokens": 8192,
        "cases": total,
        "retrieval_recall": sum(row["retrieval_recall"] for row in rows) / total
        if total
        else 0.0,
        "answer_accuracy": sum(row["exact_match"] for row in rows) / total
        if total
        else 0.0,
        "groundedness": sum(row["grounded"] for row in rows) / total if total else 0.0,
        "ungrounded_rate": sum(row["ungrounded"] for row in rows) / total
        if total
        else 0.0,
        "evidence_available": sum(row["evidence_available"] for row in rows) / total
        if total
        else 0.0,
        "abstention_accuracy": (
            sum(
                bool(row["abstention_correct"])
                for row in rows
                if row["abstention_correct"] is not None
            )
            / abstention_total
            if abstention_total
            else None
        ),
        "mean_context_chars": sum(row["context_chars"] for row in rows) / total
        if total
        else 0.0,
        "results": rows,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a JSON report so an interruption cannot corrupt it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


async def run_retrieval_ablation(
    rag: LightRAG,
    *,
    questions: list[dict[str, Any]],
    facts_by_id: dict[str, dict[str, Any]],
    keyword_cache: dict[str, tuple[list[str], list[str]]],
    top_k: int,
) -> dict[str, Any]:
    """Measure KG evidence availability without invoking the answer model."""
    rows = []
    for question in questions:
        text = str(question["question"])
        high, low = keyword_cache[text]
        context = await rag.aquery(
            text,
            param=query_param(
                top_k=top_k,
                high_keywords=high,
                low_keywords=low,
                context_only=True,
            ),
        )
        evidence = [
            facts_by_id[fact_id]
            for fact_id in question.get("evidence_fact_ids", [])
            if fact_id in facts_by_id
        ]
        recall = (
            sum(_fact_in_context(fact, str(context)) for fact in evidence)
            / len(evidence)
            if evidence
            else 1.0
        )
        rows.append(
            {
                "question_id": question["id"],
                "retrieval_recall": recall,
                "context_chars": len(str(context)),
            }
        )
    total = len(rows)
    return {
        "model": "qwen3:8b (generation not invoked)",
        "mode": "mix",
        "top_k": top_k,
        "chunk_top_k": top_k,
        "max_total_tokens": 8192,
        "cases": total,
        "retrieval_recall": sum(row["retrieval_recall"] for row in rows) / total,
        "mean_context_chars": sum(row["context_chars"] for row in rows) / total,
        "results": rows,
    }


async def _amain(args: argparse.Namespace) -> None:
    oracle = DatasetClient(str(args.dataset)).oracle()
    questions = list(oracle["questions"])
    if args.question_id:
        requested = set(args.question_id)
        questions = [
            question for question in questions if str(question["id"]) in requested
        ]
        found = {str(question["id"]) for question in questions}
        missing_requested = requested - found
        if missing_requested:
            raise RuntimeError(
                f"Unknown question IDs: {', '.join(sorted(missing_requested))}"
            )
    facts_by_id = {fact["fact_id"]: fact for fact in oracle["facts"]}
    keyword_cache = load_keyword_cache(args.storage_dir)
    missing = [
        question["id"]
        for question in questions
        if question["question"] not in keyword_cache
    ]
    if missing:
        raise RuntimeError(f"Missing cached keywords for: {', '.join(missing)}")

    rag = find_rag()
    await rag.initialize_storages()
    try:
        # Never mutate the source index's query cache while evaluating variants.
        rag.llm_response_cache.global_config["enable_llm_cache"] = False
        if args.freeze_prompts:
            prompts = await freeze_prompts(rag, questions, keyword_cache)
            args.freeze_prompts.parent.mkdir(parents=True, exist_ok=True)
            args.freeze_prompts.write_text(
                json.dumps(
                    {
                        "dataset": str(args.dataset),
                        "retrieval": "KG mix",
                        "top_k": 5,
                        "chunk_top_k": 5,
                        "max_total_tokens": 8192,
                        "cases": len(prompts),
                        "prompts": prompts,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        if args.run_local:
            checkpoint_path = args.run_local.with_suffix(".partial.json")
            saved_reports: dict[int, dict[str, Any]] = {}
            if checkpoint_path.exists():
                saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                saved_reports = {
                    int(report["top_k"]): report for report in saved.get("reports", [])
                }

            def save_progress(
                current_top_k: int, current_rows: list[dict[str, Any]]
            ) -> None:
                current_report = _report_from_rows(current_rows, top_k=current_top_k)
                ordered = [
                    saved_reports.get(
                        top_k, current_report if top_k == current_top_k else None
                    )
                    for top_k in args.top_k
                ]
                _write_json(
                    checkpoint_path,
                    {
                        "dataset": str(args.dataset),
                        "status": "in_progress",
                        "reports": [report for report in ordered if report is not None],
                    },
                )

            reports = []
            for top_k in args.top_k:
                previous = saved_reports.get(top_k)
                existing_rows = list(previous.get("results", [])) if previous else []
                if len(existing_rows) == len(questions):
                    report = previous
                else:
                    report = await run_local_ablation(
                        rag,
                        questions=questions,
                        facts_by_id=facts_by_id,
                        keyword_cache=keyword_cache,
                        top_k=top_k,
                        existing_rows=existing_rows,
                        on_progress=lambda rows, k=top_k: save_progress(k, rows),
                        per_query_timeout=args.per_query_timeout,
                    )
                saved_reports[top_k] = report
                reports.append(report)
                save_progress(top_k, report["results"])
            _write_json(
                args.run_local,
                {
                    "dataset": str(args.dataset),
                    "status": "complete",
                    "reports": reports,
                },
            )
        if args.retrieval_only:
            reports = []
            for top_k in args.top_k:
                reports.append(
                    await run_retrieval_ablation(
                        rag,
                        questions=questions,
                        facts_by_id=facts_by_id,
                        keyword_cache=keyword_cache,
                        top_k=top_k,
                    )
                )
            args.retrieval_only.parent.mkdir(parents=True, exist_ok=True)
            args.retrieval_only.write_text(
                json.dumps(
                    {"dataset": str(args.dataset), "reports": reports},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
    finally:
        await rag.finalize_storages()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("memory_data_service/generated/rich-smoke-v1"),
    )
    parser.add_argument(
        "--storage-dir",
        type=Path,
        required=True,
        help="Existing LightRAG storage dir containing the keyword cache.",
    )
    parser.add_argument("--freeze-prompts", type=Path)
    parser.add_argument("--run-local", type=Path)
    parser.add_argument("--retrieval-only", type=Path)
    parser.add_argument("--top-k", type=int, nargs="+", default=[1, 3, 5, 10, 20])
    parser.add_argument(
        "--per-query-timeout",
        type=int,
        default=600,
        help="Fail a context or generation call after this many seconds (default: 600).",
    )
    parser.add_argument(
        "--question-id",
        action="append",
        help="Run only a named question; intended for preflight diagnostics.",
    )
    args = parser.parse_args()
    if not args.freeze_prompts and not args.run_local and not args.retrieval_only:
        parser.error(
            "one of --freeze-prompts, --run-local, or --retrieval-only is required"
        )
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()


def _render_kg_report(payload: dict[str, Any]) -> str:
    lines = [
        "# KG 上下文消融",
        "",
        "固定 KG 索引、关键词缓存、qwen3:8b 与 16,384 token 生成窗口，仅改变检索深度。",
        "",
        "| Top-K | Cases | Retrieval Recall | Accuracy | Groundedness | 未支撑率 | Mean Context (chars) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for report in payload.get("reports") or []:
        lines.append(
            f"| {report.get('top_k')} | {report.get('cases', 0)} | "
            f"{report.get('retrieval_recall', 0):.4f} | "
            f"{report.get('answer_accuracy', 0):.4f} | "
            f"{report.get('groundedness', 0):.4f} | "
            f"{report.get('ungrounded_rate', 0):.4f} | "
            f"{report.get('mean_context_chars', 0):.0f} |"
        )
    return "\n".join(lines) + "\n"


def _run_kg_ablation(context: RunContext) -> dict[str, Any]:
    extra = context.extra
    args = namespace_from_context(context, artifact_stem="kg_ablation")
    args.top_k = [
        int(value)
        for value in str(extra.get("top_k") or "1,3,5,10,20").split(",")
        if value.strip()
    ]
    args.freeze_prompts = None
    args.retrieval_only = None
    args.run_local = context.output_dir / "kg_ablation_results.json"
    args.per_query_timeout = int(extra.get("per_query_timeout") or 600)
    args.question_id = None
    asyncio.run(_amain(args))
    payload = json.loads(args.run_local.read_text(encoding="utf-8"))
    methods = []
    for report in payload.get("reports") or []:
        summary = {key: value for key, value in report.items() if key != "results"}
        methods.append(
            {
                "method": f"top{report.get('top_k')}",
                "label": f"Top-{report.get('top_k')}",
                "params": {},
                "summary": summary,
                "results": report.get("results") or [],
            }
        )
    return {
        "methods": methods,
        "report": _render_kg_report(payload),
        "status": "complete",
    }


spec = ExperimentSpec(
    id="kg_ablation",
    label="KG Top-K 上下文消融",
    description=(
        "固定 KG 索引与关键词缓存，遍历 Top-K=1/3/5/10/20 测量证据召回、"
        "上下文体积与回答质量。"
    ),
    runner=_run_kg_ablation,
    variables=[
        {
            "axis": "top_k",
            "label": "Top-K",
            "arms": [{"arm": str(k)} for k in (1, 3, 5, 10, 20)],
        }
    ],
    kind="experiment",
    supports_resume=True,
)
