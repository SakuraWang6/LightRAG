"""Context-size ablation: Top-K x context-window grid on a frozen dataset.

Retrieval is cached per Top-K; only generation repeats across the three window
sizes. Replaces the kg_ablation partial/validated file family with one
standard envelope.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.experiments.common import (
    ExperimentSpec,
    RunContext,
    chat_ollama,
    context_check,
    normalize_summary,
    write_progress,
)
from memory_eval_tests.experiments.common.context import group, split_prompt
from memory_eval_tests.experiments.kg_ablation import (
    DEFAULT_STORAGE,
    _find_rag,
    _load_keyword_cache,
    _query_param,
)
from memory_eval_tests.online.answer_eval import score_answer

TOP_K_GRID = (1, 3, 5, 10, 20)
NUM_CTX_GRID = (8192, 16384, 32768)


def _extract_context(prompt: str) -> str:
    body = prompt.split("---Context---\n", 1)[1]
    return body.split("\n\n---User Query---\n", 1)[0]


def _recall_proxy(context: str, facts: list[dict[str, Any]]) -> tuple[int, int]:
    lowered = context.lower()
    covered = 0
    for fact in facts:
        markers = [
            str(fact.get("fact_id") or ""),
            str(fact.get("answer") or ""),
            str(fact.get("expected_text") or ""),
        ]
        if any(marker and marker.lower() in lowered for marker in markers):
            covered += 1
    return covered, len(facts)


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    rate = lambda key: sum(bool(row.get(key)) for row in rows) / total if total else 0.0
    average = lambda key: (
        sum(row[key] for row in rows if row.get(key) is not None)
        / sum(1 for row in rows if row.get(key) is not None)
        if any(row.get(key) is not None for row in rows)
        else None
    )
    return {
        "cases": total,
        "answer_accuracy": rate("exact_match"),
        "groundedness": rate("grounded"),
        "hallucination_rate": rate("hallucinated"),
        "abstention_accuracy": average("abstention_correct"),
        "citation_accuracy": average("citation_correct"),
        "retrieval_recall": average("retrieval_recall"),
        "mean_context_chars": average("context_chars"),
        "mean_estimated_tokens": average("estimated_tokens"),
        "overflow_cases": sum(bool(row.get("context_overflow")) for row in rows),
    }


def _render_report(description: str, methods: list[dict[str, Any]]) -> str:
    lines = [
        "# 上下文规模消融",
        "",
        description,
        "",
        "## 核心结果",
        "",
        "| Top-K | 上下文窗口 | 检索召回(代理) | Accuracy | Groundedness | Hallucination | 平均上下文(字符) | 平均预估 Token | 溢出题数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in methods:
        s = item["summary"]
        p = item["params"]
        fmt = lambda v: "n/a" if v is None else f"{v:.4f}"
        lines.append(
            f"| {p['top_k']} | {p['num_ctx']} | {fmt(s.get('retrieval_recall'))} | {fmt(s.get('answer_accuracy'))} | "
            f"{fmt(s.get('groundedness'))} | {fmt(s.get('hallucination_rate'))} | "
            f"{s.get('mean_context_chars') or 0:.0f} | {s.get('mean_estimated_tokens') or 0:.0f} | "
            f"{s.get('overflow_cases') or 0} |"
        )
    lines.extend(
        [
            "",
            "## 口径",
            "",
            "- 检索召回(代理) = 冻结上下文中出现 oracle 证据（fact_id/answer）的比例，非 API 标准 Recall@K。",
            "- 每个臂的 `num_ctx`、预估 token 与是否溢出逐题记录；同一 Top-K 的检索结果跨三个窗口复用。",
            "- 统一 qwen3:8b、mix、temperature 0、num_predict 256。",
            "",
        ]
    )
    return "\n".join(lines)


async def _run(context: RunContext) -> dict[str, Any]:
    dataset = context.dataset
    baseline = context.baseline
    max_cases = int(baseline.get("max_cases") or 0)
    num_predict = int(baseline.get("num_predict") or 256)
    temperature = float(baseline.get("temperature") or 0)
    ollama_url = context.environment["ollama_url"]
    model = baseline["model"]
    storage_dir = Path(context.environment.get("storage_dir") or str(DEFAULT_STORAGE))

    oracle = DatasetClient(str(dataset)).oracle()
    questions = list(oracle["questions"])
    if max_cases > 0:
        questions = questions[:max_cases]
    facts_by_id = {fact["fact_id"]: fact for fact in oracle["facts"]}
    cache = _load_keyword_cache(storage_dir)
    missing = [q["id"] for q in questions if q["question"] not in cache]
    if missing:
        raise RuntimeError(f"Missing cached keywords for {len(missing)} questions; run ingest/cache first")
    rag = _find_rag()
    await rag.initialize_storages()
    rag.llm_response_cache.global_config["enable_llm_cache"] = False

    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total = len(questions)
    grid_total = len(TOP_K_GRID) * len(NUM_CTX_GRID) * total
    completed = 0
    try:
        for top_k in TOP_K_GRID:
            # Cache retrieval per Top-K; windows only affect generation.
            retrieval: dict[str, tuple[str, str, str]] = {}
            for question in questions:
                text = str(question["question"])
                high, low = cache[text]
                prompt = await rag.aquery(
                    text,
                    param=_query_param(
                        top_k=top_k,
                        high_keywords=high,
                        low_keywords=low,
                        prompt_only=True,
                    ),
                )
                prefix, user = split_prompt(str(prompt))
                retrieval[question["id"]] = (prefix, user, _extract_context(str(prompt)))
            for num_ctx in NUM_CTX_GRID:
                arm = f"top{top_k}_ctx{num_ctx}"
                for question in questions:
                    prefix, user, context = retrieval[question["id"]]
                    preflight = context_check(prefix + context, num_ctx, arm)
                    answer = chat_ollama(
                        host=ollama_url,
                        model=model,
                        system=prefix + context,
                        user=user,
                        num_predict=num_predict,
                        num_ctx=num_ctx,
                        temperature=temperature,
                    )
                    evidence_facts = [
                        facts_by_id[fid]
                        for fid in question.get("evidence_fact_ids", [])
                        if fid in facts_by_id
                    ]
                    covered, total_facts = _recall_proxy(context, evidence_facts)
                    scores = score_answer(
                        answer_text=answer,
                        expected=str(question.get("answer", "")),
                        question=question,
                        evidence_facts=evidence_facts,
                        references_blob=context,
                    )
                    rows[arm].append(
                        {
                            "question_id": question["id"],
                            "arm": arm,
                            "top_k": top_k,
                            "num_ctx": num_ctx,
                            "question_group": group(question),
                            "question_type": question.get("question_type", ""),
                            "context_chars": len(context),
                            "estimated_tokens": preflight["estimated_tokens"],
                            "context_overflow": preflight["overflow"],
                            "retrieval_recall": (covered / total_facts) if total_facts else 1.0,
                            "answer": answer,
                            "expected": question.get("answer", ""),
                            **scores,
                        }
                    )
                    completed += 1
                    if completed % 10 == 0 or completed == grid_total:
                        write_progress(
                            context.output_dir,
                            status="running",
                            done=completed,
                            total=grid_total,
                            phase=f"{arm} / {question['id']}",
                        )
                        print(f"[{completed}/{grid_total}] {arm} {question['id']}", flush=True)
    finally:
        await rag.finalize_storages()

    methods = []
    for top_k in TOP_K_GRID:
        for num_ctx in NUM_CTX_GRID:
            arm = f"top{top_k}_ctx{num_ctx}"
            methods.append(
                {
                    "method": arm,
                    "label": f"Top-{top_k} / {num_ctx}",
                    "params": {"top_k": top_k, "num_ctx": num_ctx, "num_predict": num_predict},
                    "summary": normalize_summary(_metrics(rows[arm]), "selector"),
                    "results": rows[arm],
                }
            )
    report = _render_report(spec.description, methods)
    return {"methods": methods, "report": report, "status": "complete"}


def _runner(context: RunContext) -> dict[str, Any]:
    import asyncio

    return asyncio.run(_run(context))


spec = ExperimentSpec(
    id="context_size",
    label="上下文规模消融",
    description=(
        "固定数据集与生成模型（qwen3:8b），遍历 Top-K {1,3,5,10,20} × 上下文窗口 {8K,16K,32K}，"
        "度量检索召回（证据出现代理）、上下文长度与回答质量。同一 Top-K 的检索结果跨窗口复用。"
    ),
    default_baseline={
        "model": "qwen3:8b",
        "mode": "mix",
        "top_k": 5,
        "chunk_top_k": 5,
        "num_ctx": 16384,
        "num_predict": 256,
        "temperature": 0,
        "kg": True,
    },
    variables=[
        {
            "axis": "top_k",
            "label": "Top-K",
            "arms": [{"arm": f"top{k}", "top_k": k} for k in TOP_K_GRID],
        },
        {
            "axis": "num_ctx",
            "label": "上下文窗口",
            "arms": [{"arm": f"ctx{n}", "num_ctx": n} for n in NUM_CTX_GRID],
        },
    ],
    runner=_runner,
)
