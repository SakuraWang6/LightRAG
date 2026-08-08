"""Context-selection ablation: 8 methods over the same KG candidate pool.

Consolidates the previous evidence-selector / relation-selector / table-packing
/ combined-pipeline / oracle-upper-bound runs into one envelope with fixed
conditions and comparable per-method metrics.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.experiments.common import (
    ExperimentSpec,
    RunContext,
    build_conditions,
    chat_ollama,
    context_check,
    normalize_summary,
    write_progress,
)
from memory_eval_tests.experiments.common.context import (
    contains_fact,
    entity_rows,
    facts_covered,
    group,
    make_candidates,
    oracle_candidate_ids,
    parse_selection,
    render_combined_context,
    render_context,
    role_guaranteed_repair,
    role_prompt,
    selector_prompt,
    split_prompt,
    target_tables,
)
from memory_eval_tests.experiments.common.tables import (
    build_oracle_context,
    load_sidecar_tables,
    table_markdown,
)
from memory_eval_tests.experiments.kg_ablation import (
    DEFAULT_STORAGE,
    _find_rag,
    _load_keyword_cache,
    _query_param,
)
from memory_eval_tests.online.answer_eval import score_answer

ARMS: list[tuple[str, str, int | None, bool]] = [
    ("direct_top3", "Direct Top-3", 3, False),
    ("direct_top20", "Direct Top-20", 20, False),
    ("select3", "Select Top-3", 3, True),
    ("select5", "Select Top-5", 5, True),
    ("role_select5", "Role Select Top-5", 5, True),
    ("combined_focus", "Combined Focus", 5, True),
    ("combined_precision", "Combined Precision", 5, True),
    ("oracle_text", "Oracle-Text", None, False),
]

_WIDE_ARMS = {
    "direct_top20",
    "select3",
    "select5",
    "role_select5",
    "combined_focus",
    "combined_precision",
    "oracle_text",
}


def _sidecar_parsed_dir(dataset: Path) -> Path:
    return Path("memory_eval_tests/runs/offline") / dataset.name / "sidecar" / f"{dataset.name}.docx.parsed"


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    rate = lambda key: sum(bool(row.get(key)) for row in rows) / total if total else 0.0
    average = lambda key: (
        sum(row[key] for row in rows if row.get(key) is not None) / sum(1 for row in rows if row.get(key) is not None)
        if any(row.get(key) is not None for row in rows)
        else None
    )
    grouped: dict[str, dict[str, Any]] = {}
    for name in ("FACT", "TABLE", "FIGURE", "FORMULA", "MULTIHOP", "ABSTAIN"):
        subset = [row for row in rows if row["question_group"] == name]
        if subset:
            grouped[name] = {
                "cases": len(subset),
                "answer_accuracy": sum(bool(r["exact_match"]) for r in subset) / len(subset),
                "groundedness": sum(bool(r["grounded"]) for r in subset) / len(subset),
            }
    return {
        "cases": total,
        "answer_accuracy": rate("exact_match"),
        "groundedness": rate("grounded"),
        "hallucination_rate": rate("hallucinated"),
        "abstention_accuracy": average("abstention_correct"),
        "numeric_unit_accuracy": average("numeric_unit_correct"),
        "formula_accuracy": average("formula_correct"),
        "table_cell_accuracy": average("table_cell_correct"),
        "citation_presence": rate("citation_presence"),
        "citation_correctness": average("citation_correctness"),
        "candidate_recall": average("candidate_recall"),
        "selected_recall": average("selected_recall"),
        "selection_precision": average("selection_precision"),
        "role_coverage": average("role_coverage"),
        "mean_candidate_context_chars": average("candidate_context_chars"),
        "mean_selected_context_chars": average("selected_context_chars"),
        "mean_evidence_count": average("evidence_count"),
        "overflow_cases": sum(bool(row.get("context_overflow")) for row in rows),
        "by_question_type": grouped,
    }


def _render_report(description: str, methods: list[dict[str, Any]]) -> str:
    lines = [
        "# 上下文选择消融",
        "",
        description,
        "",
        "## 核心结果",
        "",
        "| 方法 | 候选 K | 选择上限 | 选择器 | Accuracy | Groundedness | Hallucination | 平均上下文(字符) | 预估 Token 溢出题数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in methods:
        s = item["summary"]
        params = item["params"]
        overflow = s.get("overflow_cases")
        lines.append(
            f"| {item['label']} | {params.get('candidate_k', '-')} | {params.get('selected_limit', '-')} | "
            f"{'是' if params.get('selector') else '否'} | {s['answer_accuracy']:.4f} | {s['groundedness']:.4f} | "
            f"{s['hallucination_rate']:.4f} | {s['mean_selected_context_chars'] or 0:.0f} | {overflow if overflow is not None else '-'} |"
        )
    lines.extend(["", "## 分题型回答准确率", ""])
    lines.append("| 题型 | Cases | Direct-3 | Direct-20 | Select3 | Select5 | Role5 | Focus | Precision | Oracle |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    by_type: dict[str, dict[str, Any]] = {}
    for item in methods:
        for name, row in item["summary"].get("by_question_type", {}).items():
            by_type.setdefault(name, {})[item["method"]] = row.get("answer_accuracy")
    for name, arms in by_type.items():
        cases = next((v.get("cases") for v in arms.values() if v), 0)
        values = []
        for method, _, _, _ in ARMS:
            value = arms.get(method)
            values.append("n/a" if value is None else f"{value:.4f}")
        lines.append(f"| {name} | {cases} | " + " | ".join(values) + " |")
    lines.extend(
        [
            "",
            "## 可复现性",
            "",
            "- 同一 KG 存储、关键词缓存、候选池（Top-20）与提示模板；选择器输出和原始答案都保留在 run.json 的逐题结果里。",
            "- 每个臂的 `num_ctx`、预估 token 与是否溢出都逐题记录；大上下文臂默认 32K。",
            "- 未修改任何历史产物；本 run 由统一 harness 生成标准信封。",
            "",
        ]
    )
    return "\n".join(lines)


async def _run(context: RunContext) -> dict[str, Any]:
    dataset = context.dataset
    baseline = context.baseline
    max_cases = int(baseline.get("max_cases") or 0)
    num_predict = int(baseline.get("num_predict") or 128)
    temperature = float(baseline.get("temperature") or 0)
    ollama_url = context.environment["ollama_url"]
    model = baseline["model"]
    storage_dir = Path(context.environment.get("storage_dir") or str(DEFAULT_STORAGE))

    oracle = DatasetClient(str(dataset)).oracle()
    questions = list(oracle["questions"])
    if max_cases > 0:
        questions = questions[:max_cases]
    facts_by_id = {fact["fact_id"]: fact for fact in oracle["facts"]}
    objects = {obj["object_id"]: obj for obj in oracle.get("objects", [])}
    relations = oracle.get("relations", [])
    supports = {
        str(rel["target_id"]): str(rel["evidence_text"])
        for rel in relations
        if rel.get("relation_type") == "supports"
        and str(rel.get("target_id", "")).startswith("FACT")
        and rel.get("evidence_text")
    }
    tables = load_sidecar_tables(_sidecar_parsed_dir(dataset))

    cache = _load_keyword_cache(storage_dir)
    missing = [q["id"] for q in questions if q["question"] not in cache]
    if missing:
        raise RuntimeError(f"Missing cached keywords for {len(missing)} questions; run ingest/cache first")
    rag = _find_rag()
    await rag.initialize_storages()
    rag.llm_response_cache.global_config["enable_llm_cache"] = False

    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total = len(questions)

    def arm_num_ctx(method: str) -> int:
        return int(baseline.get("num_ctx") or 16384) if method not in _WIDE_ARMS else 32768

    try:
        for index, question in enumerate(questions, start=1):
            text = str(question["question"])
            high, low = cache[text]
            top20_prompt = await rag.aquery(
                text,
                param=_query_param(top_k=20, high_keywords=high, low_keywords=low, prompt_only=True),
            )
            top3_prompt = await rag.aquery(
                text,
                param=_query_param(top_k=3, high_keywords=high, low_keywords=low, prompt_only=True),
            )
            prefix, user = split_prompt(str(top20_prompt))
            top20 = make_candidates(entity_rows(str(top20_prompt), limit=20))
            top3 = make_candidates(entity_rows(str(top3_prompt), limit=3))
            evidence_facts = [
                facts_by_id[fid]
                for fid in question.get("evidence_fact_ids", [])
                if fid in facts_by_id
            ]
            selections: dict[str, list[dict[str, Any]]] = {
                "direct_top3": top3,
                "direct_top20": top20,
            }
            for method, label, limit, uses_selector in ARMS:
                if not uses_selector or method == "combined_focus" or method == "combined_precision":
                    continue
                prompt = role_prompt(text, top20, limit) if method == "role_select5" else selector_prompt(text, top20, limit)
                raw = chat_ollama(
                    host=ollama_url,
                    model=model,
                    system="Follow the requested JSON schema exactly.",
                    user=prompt,
                    num_predict=128,
                    num_ctx=arm_num_ctx(method),
                    temperature=temperature,
                )
                ids = parse_selection(raw, top20, limit)
                selections[method] = [item for item in top20 if item["evidence_id"] in ids]
            repaired, _additions = role_guaranteed_repair(top20, selections["select5"], evidence_facts)
            target_tbls = target_tables(evidence_facts, tables)
            chunks = [
                {"chunk_id": table_id, "content": table_markdown(str(table.get("content") or ""))}
                for table_id, table in target_tbls
            ]

            for method, label, selected_limit, uses_selector in ARMS:
                num_ctx = arm_num_ctx(method)
                if method == "direct_top3":
                    selected, candidate_pool = top3, top3
                    candidate_context = render_context(top3)
                elif method == "direct_top20":
                    selected, candidate_pool = top20, top20
                    candidate_context = render_context(top20)
                elif method in ("select3", "select5", "role_select5"):
                    selected = selections[method]
                    candidate_pool = top20
                    candidate_context = render_context(top20)
                elif method in ("combined_focus", "combined_precision"):
                    pool_rows = repaired if method == "combined_focus" else [
                        item for item in top20 if any(contains_fact(item, fact) for fact in evidence_facts)
                    ]
                    target_ids = {table_id for table_id, _ in target_tbls}
                    kept = []
                    for item in pool_rows:
                        raw_text = f"{item['entity']} {item['text']}"
                        is_table_row = str(item["entity"]).lower().startswith("table")
                        mentions_target = any(target in raw_text for target in target_ids) or any(
                            contains_fact(item, fact) for fact in evidence_facts
                        )
                        if not is_table_row or mentions_target:
                            kept.append(item)
                    selected = kept
                    candidate_pool = top20
                    candidate_context = render_context(top20)
                    context = render_combined_context(kept, chunks)
                    preflight = context_check(prefix + context, num_ctx, method)
                    answer = chat_ollama(
                        host=ollama_url,
                        model=model,
                        system=prefix + context,
                        user=user,
                        num_predict=256,
                        num_ctx=num_ctx,
                        temperature=temperature,
                    )
                    scores = score_answer(
                        answer_text=answer,
                        expected=str(question.get("answer", "")),
                        question=question,
                        evidence_facts=evidence_facts,
                        references_blob=context,
                    )
                    rows[method].append(
                        {
                            "question_id": question["id"],
                            "method": method,
                            "label": label,
                            "question": text,
                            "question_type": question.get("question_type", ""),
                            "question_group": group(question),
                            "selected_evidence_ids": [item["evidence_id"] for item in kept],
                            "candidate_recall": None,
                            "selected_recall": None,
                            "selection_precision": None,
                            "role_coverage": None,
                            "evidence_count": len(kept),
                            "candidate_context_chars": len(candidate_context),
                            "selected_context_chars": len(context),
                            "estimated_tokens": preflight["estimated_tokens"],
                            "context_overflow": preflight["overflow"],
                            "answer": answer,
                            "expected": question.get("answer", ""),
                            **scores,
                        }
                    )
                    continue
                elif method == "oracle_text":
                    context = build_oracle_context(
                        question=question,
                        facts=evidence_facts,
                        objects=objects,
                        supports=supports,
                        relations=relations,
                        tables=tables,
                        arm="oracle_text",
                    )
                    preflight = context_check(prefix + context, num_ctx, method)
                    answer = chat_ollama(
                        host=ollama_url,
                        model=model,
                        system=prefix + context,
                        user=user,
                        num_predict=256,
                        num_ctx=num_ctx,
                        temperature=temperature,
                    )
                    scores = score_answer(
                        answer_text=answer,
                        expected=str(question.get("answer", "")),
                        question=question,
                        evidence_facts=evidence_facts,
                        references_blob=context,
                    )
                    rows[method].append(
                        {
                            "question_id": question["id"],
                            "method": method,
                            "label": label,
                            "question": text,
                            "question_type": question.get("question_type", ""),
                            "question_group": group(question),
                            "selected_evidence_ids": [],
                            "candidate_recall": None,
                            "selected_recall": None,
                            "selection_precision": None,
                            "role_coverage": None,
                            "evidence_count": len(evidence_facts),
                            "candidate_context_chars": None,
                            "selected_context_chars": len(context),
                            "estimated_tokens": preflight["estimated_tokens"],
                            "context_overflow": preflight["overflow"],
                            "answer": answer,
                            "expected": question.get("answer", ""),
                            **scores,
                        }
                    )
                    continue
                else:
                    raise AssertionError(f"unhandled arm {method}")

                selected_context = render_context(selected)
                preflight = context_check(prefix + selected_context, num_ctx, method)
                answer = chat_ollama(
                    host=ollama_url,
                    model=model,
                    system=prefix + selected_context,
                    user=user,
                    num_predict=256,
                    num_ctx=num_ctx,
                    temperature=temperature,
                )
                selected_ids = [item["evidence_id"] for item in selected]
                oracle_fact_ids = [str(item["fact_id"]) for item in evidence_facts]
                candidate_oracle_ids = oracle_candidate_ids(candidate_pool, evidence_facts)
                matched_candidate = facts_covered(candidate_pool, evidence_facts)
                matched_selected = facts_covered(selected, evidence_facts)
                relevant_selected = [item for item in selected_ids if item in candidate_oracle_ids]
                denominator = len(evidence_facts)
                scores = score_answer(
                    answer_text=answer,
                    expected=str(question.get("answer", "")),
                    question=question,
                    evidence_facts=evidence_facts,
                    references_blob=selected_context,
                )
                rows[method].append(
                    {
                        "question_id": question["id"],
                        "method": method,
                        "label": label,
                        "question": text,
                        "question_type": question.get("question_type", ""),
                        "question_group": group(question),
                        "oracle_evidence_ids": oracle_fact_ids,
                        "candidate_evidence_ids": [item["evidence_id"] for item in candidate_pool],
                        "selected_evidence_ids": selected_ids,
                        "candidate_oracle_evidence_ids": candidate_oracle_ids,
                        "candidate_recall": len(matched_candidate) / denominator if denominator else 1.0,
                        "selected_recall": len(matched_selected) / denominator if denominator else 1.0,
                        "selection_precision": len(relevant_selected) / len(selected_ids) if selected_ids and denominator else None,
                        "role_coverage": len(matched_selected) / denominator if denominator else 1.0,
                        "evidence_count": len(selected_ids),
                        "candidate_context_chars": len(candidate_context),
                        "selected_context_chars": len(selected_context),
                        "estimated_tokens": preflight["estimated_tokens"],
                        "context_overflow": preflight["overflow"],
                        "answer": answer,
                        "expected": question.get("answer", ""),
                        **scores,
                    }
                )
            write_progress(
                context.output_dir,
                status="running",
                done=index,
                total=total,
                phase=f"question {question['id']}",
            )
            print(f"[{index}/{total}] {question['id']}", flush=True)
    finally:
        await rag.finalize_storages()

    methods = []
    for method, label, selected_limit, uses_selector in ARMS:
        method_rows = rows[method]
        candidate_k = 3 if method == "direct_top3" else (None if method == "oracle_text" else 20)
        methods.append(
            {
                "method": method,
                "label": label,
                "params": {
                    "candidate_k": candidate_k,
                    "selected_limit": selected_limit,
                    "selector": uses_selector,
                    "num_ctx": arm_num_ctx(method),
                    "num_predict": 256,
                },
                "summary": normalize_summary(_metrics(method_rows), "selector"),
                "results": method_rows,
            }
        )
    report = _render_report(spec.description, methods)
    return {"methods": methods, "report": report, "status": "complete"}


def _runner(context: RunContext) -> dict[str, Any]:
    import asyncio

    return asyncio.run(_run(context))


spec = ExperimentSpec(
    id="context_selection",
    label="上下文选择消融",
    description=(
        "在同一 KG 索引与 Top-20 候选池上对比 8 种上下文构造方法：直接 Top-3 / Top-20、"
        "LLM 选择（3/5）、角色覆盖选择、表格打包（Focus/Precision）与 Oracle 文本上界。"
        "统一 qwen3:8b、mix 检索、temperature 0；大上下文臂默认 32K 窗口。"
    ),
    default_baseline={
        "model": "qwen3:8b",
        "mode": "mix",
        "top_k": 20,
        "chunk_top_k": 20,
        "num_ctx": 16384,
        "num_predict": 128,
        "temperature": 0,
        "kg": True,
    },
    variables=[
        {
            "axis": "selection_method",
            "label": "选择方法",
            "arms": [
                {"arm": arm, "label": label, "selector": selector}
                for arm, label, _, selector in ARMS
            ],
        }
    ],
    runner=_runner,
)
