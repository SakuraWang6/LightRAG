"""Stage-level run analysis: deterministic cross-tab plus local-LLM narrative.

The deterministic part never depends on a model: every case is classified by
its retrieval outcome (full / partial / miss) against its answer outcome
(pass / fail), and the cross-tab plus a per-case list are always produced.
The LLM part uses the same local Ollama backend as the evaluation to explain
*which* flow failed and why, with a graceful offline fallback.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _string(value: Any) -> str:
    return str(value) if value is not None else ""


def _retrieval_outcome(trace: dict[str, Any]) -> str:
    retrieval = trace.get("retrieval") or {}
    status = retrieval.get("status")
    if status == "not_applicable":
        return "not_applicable"
    if status != "observed":
        return "unavailable"
    expected = [str(i) for i in (retrieval.get("expected_fact_ids") or [])]
    hit = [str(i) for i in (retrieval.get("hit_fact_ids") or [])]
    if not expected:
        return "not_applicable"
    if len(hit) == len(expected):
        return "full"
    if hit:
        return "partial"
    return "miss"


def _answer_outcome(trace: dict[str, Any]) -> str:
    answer = trace.get("answer") or {}
    exact = answer.get("exact_match")
    if exact is True:
        return "pass"
    if exact is False:
        return "fail"
    return "uncertain"


def _context_missing(trace: dict[str, Any]) -> list[str]:
    ctx = trace.get("final_context_evidence") or {}
    missing = ctx.get("missing_fact_ids")
    return [str(i) for i in missing] if isinstance(missing, list) else []


def build_stage_matrix(case_traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify every case into a retrieval x answer cell."""
    rows: list[dict[str, Any]] = []
    cells = {
        "full_pass": 0,
        "full_fail": 0,
        "partial_pass": 0,
        "partial_fail": 0,
        "miss_pass": 0,
        "miss_fail": 0,
        "unavailable": 0,
        "not_applicable": 0,
    }
    for trace in case_traces:
        retrieval = _retrieval_outcome(trace)
        answer = _answer_outcome(trace)
        oracle = trace.get("oracle") or {}
        answer_row = trace.get("answer") or {}
        question = _string(oracle.get("question") or trace.get("question"))
        rows.append(
            {
                "question_id": _string(trace.get("question_id")),
                "question_type": _string(oracle.get("question_type") or trace.get("question_type")),
                "question": question[:140],
                "expected": _string(oracle.get("answer") or answer_row.get("expected"))[:120],
                "answer": _string(answer_row.get("text") or answer_row.get("response"))[:160],
                "retrieval": retrieval,
                "answer_outcome": answer,
                "recall": (trace.get("retrieval") or {}).get("recall_at_k"),
                "hit_facts": [str(i) for i in ((trace.get("retrieval") or {}).get("hit_fact_ids") or [])],
                "expected_facts": [str(i) for i in ((trace.get("retrieval") or {}).get("expected_fact_ids") or [])],
                "missing_in_context": _context_missing(trace),
                "cause": _string((trace.get("diagnosis") or {}).get("primary_cause")),
            }
        )
        if retrieval in {"not_applicable", "unavailable"}:
            cells[retrieval] = cells.get(retrieval, 0) + 1
            continue
        key = f"{retrieval}_{answer}"
        if key in cells:
            cells[key] += 1
        else:
            cells.setdefault(key, 0)
            cells[key] += 1
    return {"cells": cells, "rows": rows}


def render_stage_matrix(matrix: dict[str, Any]) -> str:
    cells = matrix["cells"]
    lines = [
        "## 流程级归因（检索 × 回答）",
        "",
        "| 检索 \\ 回答 | 通过 | 未通过 |",
        "| --- | --- | --- |",
        f"| 全部命中 | {cells['full_pass']} | {cells['full_fail']} |",
        f"| 部分命中 | {cells['partial_pass']} | {cells['partial_fail']} |",
        f"| 未命中 | {cells['miss_pass']} | {cells['miss_fail']} |",
        "",
    ]
    if cells.get("unavailable"):
        lines.append(f"- 检索 trace 不可用：{cells['unavailable']} 题")
    if cells.get("not_applicable"):
        lines.append(f"- 拒答/不适用：{cells['not_applicable']} 题")
    lines.append(
        "按「检索环节 × 回答环节」交叉定位失败发生在哪个流程；"
        "未通过题的逐条归因见报告上方的“失败原因”与“未通过题目”。"
    )
    lines.append("")
    return "\n".join(lines)


def _ollama_client(host: str, timeout: int):
    import ollama

    # trust_env=False keeps loopback calls direct even when the parent process
    # inherited proxy variables (a stalled proxy surfaces as ReadTimeout).
    return ollama.AsyncClient(host=host, timeout=timeout, trust_env=False)


async def _ollama_chat(
    host: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    timeout: int = 900,
) -> str:
    client = _ollama_client(host, timeout)
    try:
        response = await client.chat(
            model=model,
            messages=messages,
            options={"num_ctx": 16384, "num_predict": 4096, "temperature": 0, "think": False},
        )
        return str(response["message"]["content"] or "")
    finally:
        try:
            await client._client.aclose()
        except Exception:
            logger.debug("failed to close Ollama client after analysis call", exc_info=True)


def _overview_prompt(matrix: dict[str, Any], run_summary: dict[str, Any]) -> str:
    cells = matrix["cells"]
    lines = [
        (
            "你是 LightRAG 检索增强问答系统的评测分析员。下面是一次中文文档记忆测评的逐题流程状态汇总，"
            "请用中文给出简洁的总体分析，不超过 500 字："
        ),
        "",
        "运行概要：",
        f"- 正确题数 / 总题数：{run_summary.get('correct')} / {run_summary.get('total')}",
        f"- 回答准确率：{run_summary.get('accuracy')}",
        f"- 证据支撑率：{run_summary.get('groundedness')}",
        "",
        "检索×回答交叉表（全/部分/未命中 × 通过/未通过）：",
        f"- 全部命中且通过：{cells['full_pass']}；全部命中但未通过：{cells['full_fail']}",
        f"- 部分命中且通过：{cells['partial_pass']}；部分命中但未通过：{cells['partial_fail']}",
        f"- 未命中且通过：{cells['miss_pass']}；未命中且未通过：{cells['miss_fail']}",
        "",
        "输出格式：",
        "1. **总体结论**：失败主要发生在哪个流程（检索 / 上下文选择 / 生成）。",
        "2. **证据**：用交叉表数字说明判断依据。",
        "3. **重点风险**：最值得优先处理的一到两条。",
        "",
    ]
    lines.append("逐题流程状态：")
    for row in matrix["rows"]:
        lines.append(
            f"- {row['question_id']}（{row['question_type']}）：判定={row['answer_outcome']}，"
            f"检索={row['retrieval']}，上下文缺失={','.join(row['missing_in_context']) or '无'}，归因={row['cause'] or '无'}"
        )
    return "\n".join(lines)


def _case_prompt(rows: list[dict[str, Any]]) -> str:
    lines = [
        (
            "你是 LightRAG 检索增强问答系统的评测分析员。下面列出若干道未通过评测的题目，"
            "请逐题指出失败发生在哪个流程（检索 / 上下文选择与截断 / 生成与提示词 / 拒答），并给出原因与改进建议。"
            "每题用三行以内，格式：`题号：流程 → 原因 → 建议`。不要猜测不存在的信息。"
        ),
        "",
    ]
    for row in rows:
        lines.append(f"### {row['question_id']}（{row['question_type']}）")
        lines.append(f"- 问题：{row['question']}")
        lines.append(f"- 期望答案：{row['expected']}")
        lines.append(f"- 模型回答：{row['answer']}")
        lines.append(f"- 检索：期望 {','.join(row['expected_facts']) or '无'}，命中 {','.join(row['hit_facts']) or '无'}（recall={row['recall']}）")
        lines.append(f"- 最终上下文缺失：{','.join(row['missing_in_context']) or '无'}")
        lines.append("")
    return "\n".join(lines)


def _case_analysis_markdown(output: str) -> str:
    return (
        "### 未通过题目分析（本地 LLM）\n\n"
        "以下分析由本地模型生成，仅供定位问题参考：\n\n"
        f"{output.strip()}\n"
    )


async def generate_run_analysis(
    *,
    case_traces: list[dict[str, Any]],
    run_summary: dict[str, Any],
    model: str,
    host: str = "http://127.0.0.1:11434",
    timeout: int = 900,
    max_detail_cases: int = 12,
) -> tuple[str, dict[str, Any]]:
    """Return (analysis_markdown, metadata).  Never raises for LLM failures."""
    matrix = build_stage_matrix(case_traces)
    meta: dict[str, Any] = {"matrix": matrix["cells"], "llm": {"status": "ok"}}
    try:
        overview = await _ollama_chat(
            host,
            model,
            [{"role": "user", "content": _overview_prompt(matrix, run_summary)}],
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - analysis is best-effort
        meta["llm"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        return (
            "\n".join(
                [
                    render_stage_matrix(matrix),
                    "",
                    "> 本地 LLM 分析未生成（模型不可用或超时），以上为确定性流程归因。",
                    "",
                ]
            ),
            meta,
        )

    failed_rows = [
        row
        for row in matrix["rows"]
        if row["answer_outcome"] == "fail" and row["retrieval"] not in {"not_applicable", "unavailable"}
    ]
    sections = ["## 总体分析（本地 LLM）", "", overview.strip(), ""]
    if failed_rows:
        selected = failed_rows[:max_detail_cases]
        try:
            detail = await _ollama_chat(
                host,
                model,
                [{"role": "user", "content": _case_prompt(selected)}],
                timeout=timeout,
            )
            sections.append(_case_analysis_markdown(detail))
        except Exception as exc:  # noqa: BLE001
            meta["llm"] = {
                "status": "partial",
                "overview_ok": True,
                "detail_error": f"{type(exc).__name__}: {exc}",
            }
            sections.append(
                "> 逐题分析未生成（模型调用失败），保留以上总体分析与确定性归因。"
            )
            sections.append("")
    return "\n".join(sections), meta


def write_analysis_artifacts(
    output_dir: Path, analysis_md: str, meta: dict[str, Any]
) -> dict[str, Any]:
    (output_dir / "analysis.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "analysis_report": "analysis_report.md",
        "analysis": "analysis.json",
        "analysis_matrix": meta.get("matrix", {}),
        "analysis_llm_status": (meta.get("llm") or {}).get("status"),
    }


async def analyze_run(
    *,
    output_dir: Path,
    case_traces: list[dict[str, Any]],
    run_summary: dict[str, Any],
    model: str,
    host: str = "http://127.0.0.1:11434",
) -> tuple[str, dict[str, Any]]:
    started = time.perf_counter()
    # Merge the deterministic diagnosis causes into the per-case rows so the
    # stage matrix can show the authoritative attribution next to retrieval
    # and answer outcomes.
    traces = list(case_traces)
    try:
        diagnosis = json.loads(
            (output_dir / "diagnosis.json").read_text(encoding="utf-8")
        )
        causes = {
            str(case.get("question_id")): case
            for case in diagnosis.get("cases") or []
            if isinstance(case, dict)
        }
        for trace in traces:
            cause = causes.get(str(trace.get("question_id")))
            if cause and cause.get("primary_cause"):
                trace.setdefault("diagnosis", {})["primary_cause"] = cause.get(
                    "primary_cause"
                )
    except (OSError, ValueError):
        pass
    analysis_md, meta = await generate_run_analysis(
        case_traces=traces, run_summary=run_summary, model=model, host=host
    )
    meta["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    (output_dir / "analysis_report.md").write_text(
        analysis_md, encoding="utf-8"
    )
    extra = write_analysis_artifacts(output_dir, analysis_md, meta)
    return analysis_md, extra
