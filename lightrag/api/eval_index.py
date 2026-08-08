"""Read-only evaluation-console index for ``memory_eval_tests/runs``.

The evaluation framework emits one JSON/Markdown artifact per check into
``memory_eval_tests/runs/`` (gitignored). This module scans that tree and
normalizes the heterogeneous schemas into a small SQLite index so the WebUI
can list runs, compare metrics and drill into per-question cases without
rescanning and re-parsing every request.

The JSON/Markdown files remain the source of truth; the SQLite database is a
derived cache and can be rebuilt at any time.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Directories that hold LightRAG raw storage / parser sidecars rather than
# evaluation results. They can be large and are not interesting for browsing.
_IGNORED_DIR_NAMES = {"rag_storage", "sidecar", ".git", "__pycache__"}

# Metric labels used by the frontend. Unknown keys fall back to a prettified
# snake_case name.
METRIC_LABELS: dict[str, str] = {
    "answer_accuracy": "回答准确率",
    "groundedness": "证据支撑率",
    "hallucination_rate": "幻觉率",
    "abstention_accuracy": "拒答准确率",
    "citation_accuracy": "引用准确率",
    "citation_presence": "引用出现率",
    "citation_correctness": "引用正确率",
    "numeric_unit_accuracy": "数值/单位准确率",
    "formula_accuracy": "公式准确率",
    "table_cell_accuracy": "表格单元准确率",
    "average_recall": "证据召回@K",
    "evidence_recall_at_5": "证据召回@5",
    "mrr": "MRR",
    "context_precision": "上下文精确率",
    "object_hit_rate": "对象命中率",
    "full_recall_cases": "全召回题数",
    "cases": "题数",
    "passed": "通过",
    "status": "状态",
    "chunk_sidecar_coverage": "Chunk 溯源覆盖率",
    "chunk_fact_hit_rate": "事实命中率",
    "fact_evidence_hit_rate": "事实证据命中率",
    "object_fact_evidence_hit_rate": "对象证据命中率",
    "position_coverage": "位置覆盖率",
    "meaningful_position_coverage": "有效位置覆盖率",
    "page_or_bbox_position_coverage": "页码/bbox 覆盖率",
    "oracle_page_metadata_coverage": "Oracle 页码覆盖率",
    "parse_time_seconds": "解析耗时 (s)",
    "generation_time_seconds": "生成耗时 (s)",
    "generation_peak_memory_mb": "生成峰值内存 (MB)",
    "blocks": "Block 数",
    "headings": "标题数",
    "chunks": "Chunk 数",
    "top_k": "Top-K",
    "chunk_top_k": "Chunk Top-K",
    "context_chars": "上下文字符数",
    "selected_context_chars": "选择后上下文字符数",
    "mean_context_chars": "平均上下文字符数",
    "mean_selected_context_chars": "平均选择后字符数",
    "mean_candidate_context_chars": "平均候选字符数",
    "candidate_recall": "候选召回",
    "selected_recall": "选择后召回",
    "selection_precision": "选择精确率",
    "role_coverage": "角色覆盖",
    "full_role_coverage_rate": "完整角色覆盖率",
    "changed_cases": "变更题数",
    "evidence_available": "证据可得率",
    "ready_for_online_eval": "在线评测就绪",
    "llm_ready": "LLM 就绪",
    "embedding_ready": "Embedding 就绪",
    "api_ready": "API 就绪",
    "retrieval_recall": "检索召回",
    "exact_match": "精确匹配",
    "exact_match_rate": "精确匹配率",
    "grounded": "有证据支撑",
    "grounded_rate": "证据支撑率",
    "hallucinated": "幻觉",
    "hallucinated_rate": "幻觉率",
    "citation_correct": "引用正确",
    "citation_rate": "引用正确率",
    "numeric_unit_correct": "数值/单位正确",
    "formula_correct": "公式正确",
    "table_cell_correct": "表格单元正确",
    "abstention_correct": "拒答正确",
    "recall_at_k": "召回@K",
    "reciprocal_rank": "倒数排名",
    "pages": "页数",
    "facts": "事实数",
    "questions": "问题数",
    "objects": "对象数",
    "relations": "关系数",
    "tier": "规模档",
    "profile": "生成档案",
    "engine": "解析引擎",
    "mode": "检索模式",
    "model": "生成模型",
    "old_answer_accuracy": "旧回答准确率",
    "new_answer_accuracy": "新回答准确率",
    "old_groundedness": "旧证据支撑率",
    "new_groundedness": "新证据支撑率",
    "selector": "选择器",
    "candidate_k": "候选 K",
    "selected_limit": "选择上限",
    "method": "方法",
    "arm": "实验臂",
}

_AUDIT_TITLES = {
    "integrity.json": "完整性校验",
    "sidecar.json": "Sidecar 解析",
    "layout.json": "版式审计",
    "cross_reference.json": "交叉引用",
    "object_traceability.json": "对象可追溯性",
    "chunk_traceability.json": "Chunk 可追溯性",
    "performance.json": "性能基线",
}

# Canonical metric sets per artifact kind so the WebUI shows the same columns
# for the same kind even when an artifact lacks a value (missing -> null).
_CANONICAL_METRICS: dict[str, list[str]] = {
    "retrieval": [
        "average_recall",
        "mrr",
        "context_precision",
        "object_hit_rate",
        "full_recall_cases",
        "cases",
        "top_k",
    ],
    "answer": [
        "answer_accuracy",
        "groundedness",
        "hallucination_rate",
        "abstention_accuracy",
        "citation_accuracy",
        "citation_presence",
        "citation_correctness",
        "numeric_unit_accuracy",
        "formula_accuracy",
        "table_cell_accuracy",
        "cases",
        "top_k",
    ],
    "offline_summary": ["passed", "top_k", "chunk_token_size"],
    "context_size": [
        "answer_accuracy",
        "groundedness",
        "hallucination_rate",
        "abstention_accuracy",
        "citation_accuracy",
        "retrieval_recall",
        "mean_context_chars",
        "cases",
        "top_k",
        "chunk_top_k",
    ],
    "scale": ["exact_match_rate", "grounded_rate", "hallucinated_rate", "citation_rate", "cases"],
    "preflight": ["ready_for_online_eval", "llm_ready", "embedding_ready", "api_ready"],
}

_OFFLINE_AUDIT_METRICS: dict[str, list[str]] = {
    "integrity.json": ["passed", "pages", "facts", "questions", "objects", "relations"],
    "sidecar.json": ["passed", "blocks", "headings", "position_coverage"],
    "layout.json": [
        "passed",
        "position_coverage",
        "meaningful_position_coverage",
        "page_or_bbox_position_coverage",
        "oracle_page_metadata_coverage",
        "blocks",
    ],
    "cross_reference.json": [
        "passed",
        "ref_field_target_rate",
        "ref_field_sidecar_hit_rate",
        "ref_field_chunk_hit_rate",
        "oracle_cross_reference_block_hit_rate",
        "oracle_cross_reference_chunk_hit_rate",
    ],
    "object_traceability.json": [
        "passed",
        "fact_evidence_hit_rate",
        "facts",
        "total_facts",
        "blocks",
    ],
    "chunk_traceability.json": [
        "passed",
        "chunk_sidecar_coverage",
        "chunk_fact_hit_rate",
        "caption_chunk_hit_rate",
        "reference_chunk_hit_rate",
        "chunks",
    ],
    "performance.json": [
        "parse_time_seconds",
        "generation_time_seconds",
        "generation_peak_memory_mb",
        "dataset_size_bytes",
        "sidecar_size_bytes",
        "blocks",
        "chunks",
        "chunk_sidecar_coverage",
    ],
}

# Canonical columns for experiment method/arm comparison tables.
_EXPERIMENT_COLUMNS = [
    "method",
    "arm",
    "label",
    "answer_accuracy",
    "groundedness",
    "hallucination_rate",
    "abstention_accuracy",
    "citation_accuracy",
    "citation_presence",
    "citation_correctness",
    "numeric_unit_accuracy",
    "formula_accuracy",
    "table_cell_accuracy",
    "candidate_recall",
    "selected_recall",
    "selection_precision",
    "role_coverage",
    "full_role_coverage_rate",
    "mean_selected_context_chars",
    "mean_candidate_context_chars",
    "changed_cases",
    "cases",
]

_MAX_CELL_LEN = 300
_MAX_ROWS = 2000
_MAX_MD_BYTES = 2_000_000


def default_runs_root() -> Path:
    """Resolve ``memory_eval_tests/runs`` relative to the repository root."""
    return Path(__file__).resolve().parents[2] / "memory_eval_tests" / "runs"


def default_db_path(runs_root: Path | None = None) -> Path:
    return (runs_root or default_runs_root()) / ".eval_index.sqlite3"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mtime_str(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _humanize(key: str) -> str:
    pretty = re.sub(r"_+", " ", key).strip().title()
    return METRIC_LABELS.get(key, pretty)


def _scalar(value: Any, max_len: int = _MAX_CELL_LEN) -> Any:
    """Turn arbitrary JSON values into a small table cell."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:max_len]
    if isinstance(value, (list, tuple)):
        parts = [str(v) for v in value if not isinstance(v, (dict, list))]
        joined = ", ".join(parts)
        return joined[:max_len] or None
    return None


def _metric(key: str, value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return {"key": key, "label": _humanize(key), "value": value, "type": "bool"}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"key": key, "label": _humanize(key), "value": value, "type": "number"}
    return None


def _numeric_metrics(data: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = []
    for key, value in data.items():
        m = _metric(key, value)
        if m is not None:
            metrics.append(m)
    return metrics


def _rows_from_list(rows: list[Any]) -> dict[str, Any]:
    cleaned = []
    columns: list[dict[str, str]] = []
    for row in rows[: _MAX_ROWS]:
        if not isinstance(row, dict):
            continue
        item = {k: _scalar(v) for k, v in row.items()}
        cleaned.append(item)
        for key in item:
            if key not in {c["key"] for c in columns}:
                columns.append({"key": key, "label": _humanize(key)})
    return {"columns": columns, "rows": cleaned}


def _row_summaries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Summaries of experiment methods/arms, dropping nested aggregates."""
    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        row = {}
        for key, value in entry.items():
            if key in {"by_question_type", "results"}:
                continue
            if isinstance(value, dict):
                # Flatten one level (e.g. ``summary``) so score columns are
                # comparable across experiments regardless of nesting.
                for sub_key, sub_value in value.items():
                    if sub_key in {"by_question_type", "results"}:
                        continue
                    cell = _scalar(sub_value)
                    if cell is not None and sub_key not in row:
                        row[sub_key] = cell
                continue
            cell = _scalar(value)
            if cell is not None:
                row[key] = cell
        rows.append(row)
    return _rows_from_list(rows)


def _canonicalize_metrics(metrics: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    """Order metrics by a canonical key list and fill missing values with null."""
    by_key = {m["key"]: m for m in metrics}
    canonical = []
    for key in keys:
        existing = by_key.get(key)
        if existing is not None:
            canonical.append(existing)
        else:
            canonical.append({"key": key, "label": _humanize(key), "value": None, "type": "null"})
    # Keep any non-canonical metrics the artifact carried (extra context).
    for key, metric in by_key.items():
        if key not in keys:
            canonical.append(metric)
    return canonical


def _canonicalize_experiment_table(table: dict[str, Any]) -> dict[str, Any]:
    """Force experiment method tables onto the shared column set."""
    rows = table.get("rows", [])
    canonical_rows = []
    extra_columns: list[dict[str, str]] = []
    for row in rows:
        new_row: dict[str, Any] = {}
        for key in _EXPERIMENT_COLUMNS:
            new_row[key] = row.get(key)
        # structure ablation reports use ``accuracy`` for answer accuracy.
        if new_row.get("answer_accuracy") is None and row.get("accuracy") is not None:
            new_row["answer_accuracy"] = row["accuracy"]
        for key, value in row.items():
            if key not in _EXPERIMENT_COLUMNS and key not in {"by_question_type", "results"}:
                new_row[key] = value
                if key not in {c["key"] for c in extra_columns}:
                    extra_columns.append({"key": key, "label": _humanize(key)})
        canonical_rows.append(new_row)
    columns = [
        {"key": key, "label": _humanize(key)}
        for key in _EXPERIMENT_COLUMNS
        if any(row.get(key) is not None for row in canonical_rows)
    ]
    columns.extend(extra_columns)
    return {"columns": columns, "rows": canonical_rows}


def _flatten_case_rows(entries: list[dict[str, Any]], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = []
    for entry in entries[: _MAX_ROWS]:
        if not isinstance(entry, dict):
            continue
        merged = dict(extra or {})
        merged.update({k: _scalar(v) for k, v in entry.items()})
        rows.append(merged)
    return _rows_from_list(rows)


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [r[key] for r in rows if isinstance(r.get(key), bool)]
    if not values:
        return None
    return sum(values) / len(values)


def _extract_retrieval(data: dict[str, Any], title: str) -> dict[str, Any]:
    return {
        "kind": "retrieval",
        "title": title,
        "metrics": _numeric_metrics(data),
        "table": _rows_from_list(data.get("results") or []),
        "meta": {
            k: data[k]
            for k in ("mode", "top_k", "backend", "max_cases")
            if k in data and not isinstance(data[k], (dict, list))
        },
    }


def _extract_answer(data: dict[str, Any], title: str) -> dict[str, Any]:
    return {
        "kind": "answer",
        "title": title,
        "metrics": _numeric_metrics(data),
        "table": _rows_from_list(data.get("results") or []),
        "meta": {
            k: data[k]
            for k in ("mode", "top_k", "chunk_top_k", "max_total_tokens", "max_cases")
            if k in data and not isinstance(data[k], (dict, list))
        },
    }


def _extract_methods_experiment(data: dict[str, Any], title: str) -> dict[str, Any]:
    methods = data.get("methods") or []
    cases: list[dict[str, Any]] = []
    for method in methods:
        if isinstance(method, dict):
            for case in method.get("results") or []:
                if isinstance(case, dict):
                    cases.append({"method": method.get("method"), **case})
    return {
        "kind": "experiment",
        "title": title,
        "metrics": [],
        "table": _row_summaries(methods),
        "meta": {
            "dataset": data.get("dataset"),
            "storage_dir": data.get("storage_dir"),
            "retrieval_mode": data.get("retrieval_mode"),
            "model": data.get("model"),
            "ollama_url": data.get("ollama_url"),
            "generation": data.get("generation"),
            "status": data.get("status"),
            "cases": _flatten_case_rows(cases),
        },
    }


def _extract_structure_ablation(data: dict[str, Any], title: str) -> dict[str, Any]:
    arms = []
    for name in ("native", "oracle_full"):
        block = data.get(name)
        if isinstance(block, dict):
            arms.append({"arm": name, **{k: v for k, v in block.items() if k != "by_question_type"}})
    return {
        "kind": "experiment",
        "title": title,
        "metrics": [],
        "table": _row_summaries(arms),
        "meta": {
            "dataset": data.get("dataset"),
            "status": data.get("status"),
            "cases": _flatten_case_rows(data.get("results") or []),
        },
    }


def _extract_oracle_upper_bound(data: dict[str, Any], title: str) -> dict[str, Any]:
    arms = data.get("arms") or []
    return {
        "kind": "experiment",
        "title": title,
        "metrics": [],
        "table": _row_summaries(arms),
        "meta": {
            "dataset": data.get("dataset"),
            "model": data.get("model"),
            "status": data.get("status"),
            "baseline": data.get("selector_baseline"),
            "cases": _flatten_case_rows(data.get("results") or []),
        },
    }


def _extract_scale(data: dict[str, Any], title: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in data.get("results") or []:
        if not isinstance(case, dict):
            continue
        base = {
            "question_id": case.get("question_id"),
            "question_group": case.get("question_group"),
            "question_type": case.get("question_type"),
            "candidate_count": case.get("candidate_count"),
            "candidate_recall": case.get("candidate_recall"),
        }
        for method in case.get("methods") or []:
            if not isinstance(method, dict):
                continue
            row = dict(base)
            row["method"] = method.get("method")
            row["context_chars"] = method.get("context_chars")
            row["answer"] = _scalar(method.get("answer"))
            for mkey, mvalue in (method.get("metrics") or {}).items():
                row[mkey] = _scalar(mvalue)
            rows.append(row)
    metrics = [
        {"key": "cases", "label": "题数", "value": len(rows), "type": "number"},
        *[
            m
            for m in [
                _metric("exact_match_rate", _rate(rows, "exact_match")),
                _metric("grounded_rate", _rate(rows, "grounded")),
                _metric("hallucinated_rate", _rate(rows, "hallucinated")),
                _metric("citation_rate", _rate(rows, "citation_correct")),
            ]
            if m is not None
        ],
    ]
    return {
        "kind": "scale",
        "title": title,
        "metrics": metrics,
        "table": _rows_from_list(rows),
        "meta": {
            "dataset": data.get("dataset"),
            "status": data.get("status"),
            "skip_kg": data.get("skip_kg"),
        },
    }


def _extract_context_size(data: dict[str, Any], title: str) -> dict[str, Any]:
    reports = data.get("reports") or []
    cases: list[dict[str, Any]] = []
    for report in reports:
        if not isinstance(report, dict):
            continue
        for case in report.get("results") or []:
            if isinstance(case, dict):
                cases.append(
                    {
                        "model": report.get("model"),
                        "top_k": report.get("top_k"),
                        **case,
                    }
                )
    return {
        "kind": "context_size",
        "title": title,
        "metrics": [m for m in (_metric("cases", len(cases)),) if m is not None],
        "table": _row_summaries(reports),
        "meta": {
            "dataset": data.get("dataset"),
            "cases": _flatten_case_rows(cases),
        },
    }


def _extract_preflight(data: dict[str, Any], title: str) -> dict[str, Any]:
    metrics = []
    for key in ("ready_for_online_eval", "llm_ready", "embedding_ready", "api_ready"):
        if key in data:
            metrics.append(_metric(key, data[key]))
    return {
        "kind": "preflight",
        "title": title,
        "metrics": [m for m in metrics if m is not None],
        "table": {"columns": [], "rows": []},
        "meta": {
            "rag_api_url": data.get("rag_api_url"),
            "ollama_url": data.get("ollama_url"),
            "blockers": data.get("blockers"),
        },
    }


def _extract_evaluator_recheck(data: dict[str, Any], title: str) -> dict[str, Any]:
    reports = data.get("reports") or []
    cases = [case for r in reports if isinstance(r, dict) for case in (r.get("changed_questions") or [])]
    return {
        "kind": "evaluator_recheck",
        "title": title,
        "metrics": [],
        "table": _row_summaries(reports),
        "meta": {
            "dataset": data.get("dataset"),
            "cases": _flatten_case_rows(cases),
        },
    }


def _extract_comparison(data: Any, title: str) -> dict[str, Any]:
    if isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
        return {
            "kind": "comparison",
            "title": title,
            "metrics": [],
            "table": _rows_from_list(rows),
            "meta": {"rows": len(rows)},
        }
    return {
        "kind": "comparison",
        "title": title,
        "metrics": _numeric_metrics(data),
        "table": {"columns": [], "rows": []},
        "meta": {},
    }


def _extract_readiness(data: dict[str, Any], title: str) -> dict[str, Any]:
    return {
        "kind": "readiness",
        "title": title,
        "metrics": [m for m in (_metric("structural_offline_pass", data.get("structural_offline_pass")),) if m],
        "table": _rows_from_list(data.get("scale_rows") or []),
        "meta": {"datasets": data.get("datasets"), "findings": data.get("findings")},
    }


def _extract_offline_summary(data: dict[str, Any], title: str) -> dict[str, Any]:
    return {
        "kind": "offline_summary",
        "title": title,
        "metrics": _numeric_metrics(data),
        "table": {"columns": [], "rows": []},
        "meta": {
            "dataset_id": data.get("dataset_id"),
            "engine": data.get("engine"),
            "reports": data.get("reports"),
            "markdown_report": data.get("markdown_report"),
        },
    }


def _extract_offline_audit(data: dict[str, Any], title: str) -> dict[str, Any]:
    return {
        "kind": "offline_audit",
        "title": title,
        "metrics": _numeric_metrics(data),
        "table": {"columns": [], "rows": []},
        "meta": {
            "dataset_source": data.get("dataset_source"),
            "dataset_id": data.get("dataset_id"),
            "parsed_dir": data.get("parsed_dir"),
        },
    }


def _extract_generic(data: Any, title: str) -> dict[str, Any]:
    if isinstance(data, list):
        return {
            "kind": "generic",
            "title": title,
            "metrics": [],
            "table": _rows_from_list(data),
            "meta": {},
        }
    if not isinstance(data, dict):
        return {"kind": "generic", "title": title, "metrics": [], "table": {"columns": [], "rows": []}, "meta": {}}
    metrics = _numeric_metrics(data)
    table = {"columns": [], "rows": []}
    meta = {}
    for key, value in data.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            table = _rows_from_list(value)
        elif not isinstance(value, (dict, list)) and key not in {m["key"] for m in metrics}:
            meta[key] = _scalar(value, 500)
    return {"kind": "generic", "title": title, "metrics": metrics, "table": table, "meta": meta}


def _extract_artifact(rel_path: Path, data: Any, mtime: str) -> dict[str, Any]:
    name = rel_path.name
    if name.endswith(".md"):
        return {"kind": "markdown_report", "title": name, "error": None}
    title = name
    try:
        if name.startswith("retrieval_"):
            artifact = _extract_retrieval(data, "检索结果")
            artifact["metrics"] = _canonicalize_metrics(artifact["metrics"], _CANONICAL_METRICS["retrieval"])
            return artifact
        if name.startswith("answer_"):
            artifact = _extract_answer(data, "回答评测")
            artifact["metrics"] = _canonicalize_metrics(artifact["metrics"], _CANONICAL_METRICS["answer"])
            return artifact
        if name == "summary.json":
            artifact = _extract_offline_summary(data, "离线审计汇总")
            artifact["metrics"] = _canonicalize_metrics(artifact["metrics"], _CANONICAL_METRICS["offline_summary"])
            return artifact
        if name.startswith("context_size_"):
            artifact = _extract_context_size(data, "上下文大小实验")
            artifact["metrics"] = _canonicalize_metrics(artifact["metrics"], _CANONICAL_METRICS["context_size"])
            return artifact
        if name.startswith("scale_eval"):
            artifact = _extract_scale(data, "规模评测")
            artifact["metrics"] = _canonicalize_metrics(artifact["metrics"], _CANONICAL_METRICS["scale"])
            return artifact
        if name == "structure_ablation_results.json":
            artifact = _extract_structure_ablation(data, "结构消融")
            artifact["table"] = _canonicalize_experiment_table(artifact["table"])
            return artifact
        if name == "oracle_upper_bound_results.json":
            artifact = _extract_oracle_upper_bound(data, "Oracle 上界")
            artifact["table"] = _canonicalize_experiment_table(artifact["table"])
            return artifact
        if name in {"evidence_selector_results.json", "relation_selector_results.json",
                    "table_packing_results.json", "combined_pipeline_results.json"}:
            artifact = _extract_methods_experiment(data, "实验方法对比")
            artifact["table"] = _canonicalize_experiment_table(artifact["table"])
            return artifact
        if name in {"api_preflight.json", "local_vlm_api_preflight.json"}:
            artifact = _extract_preflight(data, "环境预检")
            artifact["metrics"] = _canonicalize_metrics(artifact["metrics"], _CANONICAL_METRICS["preflight"])
            return artifact
        if name == "evaluator_recheck.json":
            return _extract_evaluator_recheck(data, "评估器复核")
        if name == "comparison_report.json":
            return _extract_comparison(data, "对比报告")
        if name == "readiness_report.json":
            return _extract_readiness(data, "就绪度报告")
        if name in _AUDIT_TITLES:
            artifact = _extract_offline_audit(data, _AUDIT_TITLES[name])
            artifact["metrics"] = _canonicalize_metrics(
                artifact["metrics"], _OFFLINE_AUDIT_METRICS.get(name, [])
            )
            return artifact
        if name.endswith("_results.json"):
            artifact = _extract_methods_experiment(data, "实验结果")
            artifact["table"] = _canonicalize_experiment_table(artifact["table"])
            return artifact
        return _extract_generic(data, title)
    except Exception as exc:  # keep the index alive when one artifact is odd
        return {"kind": "unparsed", "title": title, "error": f"{type(exc).__name__}: {exc}"}


def _candidate_files(runs_root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(str(runs_root), followlinks=False):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _IGNORED_DIR_NAMES and not d.endswith(".parsed") and not d.startswith(".")
        ]
        for name in filenames:
            if name.startswith("."):
                continue
            if name.endswith((".json", ".md")):
                files.append(Path(dirpath) / name)
    return files


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _run_kind(rel_dir: str) -> str:
    if rel_dir.startswith("offline"):
        return "offline"
    if rel_dir.startswith("online"):
        return "online"
    if rel_dir == ".":
        return "report"
    return "experiment"


def _dataset_from_artifact(meta: dict[str, Any], rel_dir: str) -> str | None:
    for key in ("dataset_id",):
        value = meta.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("dataset", "dataset_source"):
        value = meta.get(key)
        if isinstance(value, str) and value:
            return Path(value).name
    return None


def _known_datasets(runs_root: Path) -> list[str]:
    generated = Path(runs_root).parent.parent / "memory_data_service" / "generated"
    try:
        return sorted({p.name for p in generated.iterdir() if p.is_dir()})
    except OSError:
        return []


def _infer_dataset(runs_root: Path, label: str, known: list[str]) -> str | None:
    """Match the longest known dataset id that prefixes the run label."""
    best = None
    for name in known:
        if name in label and (best is None or len(name) > len(best)):
            best = name
    return best


def _markdown_toc(content: str) -> list[dict[str, Any]]:
    toc: list[dict[str, Any]] = []
    for line in content.splitlines():
        match = re.match(r"^(#{1,4})\s+(.+?)\s*#*\s*$", line)
        if match:
            toc.append({"level": len(match.group(1)), "title": match.group(2).strip()})
    return toc[:200]


_CONDITION_ORDER = [
    "dataset",
    "pages",
    "tier",
    "profile",
    "title",
    "formats",
    "engine",
    "model",
    "mode",
    "top_k",
    "chunk_top_k",
    "methods",
    "num_ctx",
    "num_predict",
    "max_total_tokens",
]

_CONDITION_LABELS = {
    "dataset": "数据集",
    "pages": "文档页数",
    "tier": "规模档",
    "profile": "生成档案",
    "title": "文档标题",
    "formats": "格式",
    "engine": "解析引擎",
    "model": "生成模型",
    "mode": "检索模式",
    "top_k": "Top-K",
    "chunk_top_k": "Chunk Top-K",
    "methods": "方法数",
    "num_ctx": "上下文窗口",
    "num_predict": "最大输出",
    "max_total_tokens": "最大输出",
}


def _run_conditions(run: dict[str, Any], artifacts: list[dict[str, Any]], runs_root: Path) -> list[dict[str, str]]:
    """Collect the experimental conditions of a run for display chips."""
    conds: dict[str, str] = {}
    dataset = run.get("dataset")
    if dataset:
        conds["dataset"] = dataset
        manifest_path = Path(runs_root).parent.parent / "memory_data_service" / "generated" / dataset / "manifest.json"
        try:
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for key in ("pages", "tier", "profile", "title", "formats"):
                    if manifest.get(key):
                        conds[key] = (
                            str(manifest[key])
                            if not isinstance(manifest[key], list)
                            else ",".join(manifest[key])
                        )
        except (OSError, ValueError):
            pass

    for artifact in artifacts:
        meta = artifact.get("meta") or {}
        if artifact["kind"] == "offline_summary" and not conds.get("engine"):
            engine = meta.get("engine")
            if engine:
                conds["engine"] = str(engine)
        if artifact["kind"] == "experiment":
            for key in ("model", "retrieval_mode", "generation"):
                value = meta.get(key)
                if value and key not in conds:
                    if key == "generation" and isinstance(value, dict):
                        for gen_key in ("num_ctx", "num_predict"):
                            if value.get(gen_key) is not None:
                                conds.setdefault(gen_key, str(value[gen_key]))
                    elif isinstance(value, str):
                        conds[key if key != "retrieval_mode" else "mode"] = value
            rows = artifact.get("table", {}).get("rows", [])
            if rows:
                conds["methods"] = str(len(rows))
        for metric in artifact.get("metrics", []):
            if metric["key"] in {"mode", "top_k", "chunk_top_k"} and metric["value"] is not None:
                conds.setdefault(metric["key"], str(metric["value"]))
    return [
        {"key": key, "label": _CONDITION_LABELS[key], "value": conds[key]}
        for key in _CONDITION_ORDER
        if key in conds
    ]


def _headline_for(run: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick a small set of headline metrics for the run list."""
    headline: dict[str, Any] = {}
    kind = run["kind"]
    if kind == "offline":
        for artifact in artifacts:
            metrics = {m["key"]: m for m in artifact.get("metrics", [])}
            if artifact.get("kind") == "offline_summary" and "passed" in metrics:
                headline["passed"] = metrics["passed"]
            if artifact.get("kind") == "retrieval":
                for key in ("average_recall", "mrr", "object_hit_rate"):
                    if key in metrics and key not in headline:
                        headline[key] = metrics[key]
            if artifact.get("kind") == "offline_audit":
                for key in ("chunk_sidecar_coverage", "position_coverage", "fact_evidence_hit_rate"):
                    if key in metrics and key not in headline:
                        headline[key] = metrics[key]
    elif kind == "online":
        for artifact in artifacts:
            metrics = {m["key"]: m for m in artifact.get("metrics", [])}
            if artifact.get("kind") == "answer":
                for key in ("answer_accuracy", "groundedness", "hallucination_rate", "citation_accuracy"):
                    if key in metrics and key not in headline:
                        headline[key] = metrics[key]
            if artifact.get("kind") == "retrieval":
                for key in ("average_recall", "mrr"):
                    if key in metrics and key not in headline:
                        headline[key] = metrics[key]
    return headline


def build_index(
    runs_root: Path,
    db_path: Path | None = None,
    known_datasets: list[str] | None = None,
) -> dict[str, Any]:
    """Scan ``runs_root`` and rebuild the SQLite index. Returns a summary."""
    runs_root = Path(runs_root)
    db_path = Path(db_path or default_db_path(runs_root))
    if known_datasets is None:
        known_datasets = _known_datasets(runs_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    files = _candidate_files(runs_root)

    # Group by run directory (relative to runs_root).
    grouped: dict[str, list[Path]] = {}
    for path in files:
        rel_dir = str(path.parent.relative_to(runs_root))
        grouped.setdefault(rel_dir, []).append(path)

    runs: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    for rel_dir, paths in sorted(grouped.items()):
        run_id = rel_dir if rel_dir != "." else "_root"
        kind = _run_kind(rel_dir)
        artifact_records = []
        updated_at: str | None = None
        for path in sorted(paths):
            mtime = _mtime_str(path)
            if updated_at is None or mtime > updated_at:
                updated_at = mtime
            rel = str(path.relative_to(runs_root))
            artifact: dict[str, Any]
            if path.suffix == ".md":
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")[:_MAX_MD_BYTES]
                except OSError as exc:
                    content = ""
                    artifact = {"kind": "unparsed", "title": path.name, "error": str(exc)}
                else:
                    first = next(
                        (line.strip().lstrip("# ") for line in content.splitlines() if line.strip().startswith("#")),
                        path.name,
                    )
                    artifact = {
                        "kind": "markdown_report",
                        "title": first or path.name,
                        "toc": _markdown_toc(content),
                    }
                artifact["rel_path"] = rel
                artifact["updated_at"] = mtime
                artifact["report_md"] = content
                artifact["metrics"] = []
                artifact["table"] = {"columns": [], "rows": []}
                artifact["meta"] = {}
                artifact["error"] = None
            else:
                try:
                    data = _load_json(path)
                except Exception as exc:
                    artifact = {
                        "kind": "unparsed",
                        "title": path.name,
                        "error": f"{type(exc).__name__}: {exc}",
                        "rel_path": rel,
                        "updated_at": mtime,
                        "metrics": [],
                        "table": {"columns": [], "rows": []},
                        "meta": {},
                        "report_md": None,
                        "toc": [],
                    }
                else:
                    artifact = _extract_artifact(path, data, mtime)
                    artifact["rel_path"] = rel
                    artifact["updated_at"] = mtime
                    artifact["report_md"] = None
                    artifact["toc"] = []
            artifact_records.append(artifact)

        dataset = next(
            (
                d
                for a in artifact_records
                if (d := _dataset_from_artifact(a.get("meta") or {}, rel_dir))
            ),
            None,
        )
        if dataset is None and kind in {"online", "experiment"}:
            dataset = _infer_dataset(runs_root, rel_dir, known_datasets)
        status = next(
            (
                str(a["meta"].get("status"))
                for a in artifact_records
                if a.get("meta") and a["meta"].get("status")
            ),
            None,
        )
        if kind == "offline" and status is None:
            passed = next(
                (a for a in artifact_records if a["kind"] == "offline_summary" and "passed" in {m["key"] for m in a["metrics"]}),
                None,
            )
            if passed is not None:
                status = "passed" if next(m for m in passed["metrics"] if m["key"] == "passed")["value"] else "failed"
        run = {
            "id": run_id,
            "kind": kind,
            "label": rel_dir if rel_dir != "." else "报告",
            "dataset": dataset,
            "updated_at": updated_at,
            "status": status,
        }
        conditions = (
            []
            if kind == "report"
            else _run_conditions({"dataset": dataset, "kind": kind}, artifact_records, runs_root)
        )
        run["conditions"] = conditions
        headline = _headline_for(run, artifact_records)
        runs.append({**run, "headline": headline, "artifact_titles": [a["title"] for a in artifact_records]})
        for artifact in artifact_records:
            artifacts.append({**artifact, "run_id": run_id})

    indexed_at = _utcnow()
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("DROP TABLE IF EXISTS artifacts")
        conn.execute("DROP TABLE IF EXISTS runs")
        conn.execute("DROP TABLE IF EXISTS meta")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                label TEXT,
                dataset TEXT,
                updated_at TEXT,
                status TEXT,
                conditions_json TEXT,
                headline_json TEXT,
                artifact_titles_json TEXT
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                run_id TEXT NOT NULL,
                rel_path TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT,
                updated_at TEXT,
                metrics_json TEXT,
                table_json TEXT,
                meta_json TEXT,
                report_md TEXT,
                toc_json TEXT,
                error TEXT,
                PRIMARY KEY (run_id, rel_path)
            );
            """
        )
        conn.executemany(
            "INSERT INTO runs (id, kind, label, dataset, updated_at, status, conditions_json, headline_json, artifact_titles_json) "
            "VALUES (:id, :kind, :label, :dataset, :updated_at, :status, :conditions_json, :headline_json, :artifact_titles_json)",
            [
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "label": r["label"],
                    "dataset": r["dataset"],
                    "updated_at": r["updated_at"],
                    "status": r["status"],
                    "conditions_json": json.dumps(r.get("conditions", []), ensure_ascii=False),
                    "headline_json": json.dumps(r["headline"], ensure_ascii=False),
                    "artifact_titles_json": json.dumps(r["artifact_titles"], ensure_ascii=False),
                }
                for r in runs
            ],
        )
        conn.executemany(
            "INSERT INTO artifacts (run_id, rel_path, kind, title, updated_at, metrics_json, table_json, meta_json, report_md, toc_json, error) "
            "VALUES (:run_id, :rel_path, :kind, :title, :updated_at, :metrics_json, :table_json, :meta_json, :report_md, :toc_json, :error)",
            [
                {
                    "run_id": a["run_id"],
                    "rel_path": a["rel_path"],
                    "kind": a["kind"],
                    "title": a["title"],
                    "updated_at": a["updated_at"],
                    "metrics_json": json.dumps(a.get("metrics", []), ensure_ascii=False),
                    "table_json": json.dumps(a.get("table", {"columns": [], "rows": []}), ensure_ascii=False),
                    "meta_json": json.dumps(a.get("meta", {}), ensure_ascii=False),
                    "report_md": a.get("report_md"),
                    "toc_json": json.dumps(a.get("toc", []), ensure_ascii=False),
                    "error": a.get("error"),
                }
                for a in artifacts
            ],
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('indexed_at', ?), ('file_count', ?), ('run_count', ?)",
            (indexed_at, str(len(files)), str(len(runs))),
        )
        conn.commit()
    return {"indexed_at": indexed_at, "file_count": len(files), "run_count": len(runs)}


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def load_runs(db_path: Path) -> list[dict[str, Any]]:
    with _open_db(db_path) as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY updated_at DESC").fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "label": row["label"],
                "dataset": row["dataset"],
                "updated_at": row["updated_at"],
                "status": row["status"],
                "conditions": json.loads(row["conditions_json"] or "[]"),
                "headline": json.loads(row["headline_json"] or "{}"),
                "artifact_titles": json.loads(row["artifact_titles_json"] or "[]"),
            }
        )
    return result


def load_run(db_path: Path, run_id: str) -> dict[str, Any] | None:
    with _open_db(db_path) as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        artifact_rows = conn.execute(
            "SELECT * FROM artifacts WHERE run_id = ? ORDER BY updated_at DESC, rel_path", (run_id,)
        ).fetchall()
    artifacts = []
    for a in artifact_rows:
        artifacts.append(
            {
                "rel_path": a["rel_path"],
                "kind": a["kind"],
                "title": a["title"],
                "updated_at": a["updated_at"],
                "metrics": json.loads(a["metrics_json"] or "[]"),
                "table": json.loads(a["table_json"] or '{"columns":[],"rows":[]}'),
                "meta": json.loads(a["meta_json"] or "{}"),
                "report_md": a["report_md"],
                "toc": json.loads(a["toc_json"] or "[]"),
                "error": a["error"],
            }
        )
    return {
        "id": row["id"],
        "kind": row["kind"],
        "label": row["label"],
        "dataset": row["dataset"],
        "updated_at": row["updated_at"],
        "status": row["status"],
        "conditions": json.loads(row["conditions_json"] or "[]"),
        "headline": json.loads(row["headline_json"] or "{}"),
        "artifacts": artifacts,
    }


def index_status(db_path: Path, runs_root: Path) -> dict[str, Any]:
    """Return cached status, plus whether the on-disk tree looks newer."""
    try:
        with _open_db(db_path) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'indexed_at'").fetchone()
            indexed_at = row["value"] if row else None
    except sqlite3.Error:
        indexed_at = None
    file_count = len(_candidate_files(runs_root))
    return {"indexed_at": indexed_at, "file_count": file_count, "stale": indexed_at is None}
