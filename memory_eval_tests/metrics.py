"""Canonical metric vocabulary shared by every evaluation envelope."""

from __future__ import annotations

from typing import Any

METRIC_LABELS: dict[str, str] = {
    "answer_accuracy": "回答准确率",
    "accuracy": "回答准确率",
    "groundedness": "证据支撑率",
    "ungrounded_rate": "未支撑率",
    "hallucination_rate": "未支撑率",
    "abstention_accuracy": "拒答准确率",
    "citation_presence": "引用出现率",
    "citation_correctness": "引用正确率",
    "evidence_available": "证据可得率",
    "final_context_observable_rate": "最终上下文可观测率",
    "final_context_evidence_coverage": "最终上下文证据覆盖率",
    "final_context_evidence_available": "最终上下文证据完整率",
    "numeric_unit_accuracy": "数值/单位准确率",
    "formula_accuracy": "公式准确率",
    "table_cell_accuracy": "表格单元准确率",
    "average_recall": "证据召回@K",
    "evidence_recall_at_5": "证据召回@5",
    "retrieval_recall": "检索召回",
    "mrr": "MRR",
    "context_precision": "上下文精确率",
    "object_hit_rate": "对象命中率",
    "full_recall_cases": "全召回题数",
    "candidate_recall": "候选召回",
    "selected_recall": "选择后召回",
    "selection_precision": "选择精确率",
    "role_coverage": "角色覆盖",
    "full_role_coverage_rate": "完整角色覆盖率",
    "changed_cases": "变更题数",
    "cases": "题数",
    "correct_cases": "正确题数",
    "retrieval_cases": "检索题数",
    "mean_context_chars": "平均上下文字符数",
    "mean_selected_context_chars": "平均选择后字符数",
    "mean_candidate_context_chars": "平均候选字符数",
    "mean_evidence_count": "平均证据数",
    "evidence_count": "证据数",
    "context_chars": "上下文字符数",
    "selected_context_chars": "选择后字符数",
    "candidate_context_chars": "候选字符数",
    "estimated_tokens": "预估 Token 数",
    "num_ctx": "上下文窗口",
    "top_k": "Top-K",
    "chunk_top_k": "Chunk Top-K",
    "exact_match": "精确匹配",
    "grounded": "有证据支撑",
    "ungrounded": "未支撑",
    "hallucinated": "未支撑",
    "abstention_correct": "拒答正确",
    "numeric_unit_correct": "数值/单位正确",
    "formula_correct": "公式正确",
    "table_cell_correct": "表格单元正确",
    "citation_presence_bool": "引用出现",
    "citation_correct": "引用正确",
}

# Compatibility normalization for metric keys emitted by providers.
METRIC_ALIASES: dict[str, str] = {
    "accuracy": "answer_accuracy",
    "abstention_correct": "abstention_accuracy",
    # ``hallucination_rate`` historically measured "not grounded" (answer wrong
    # or evidence missing), i.e. an answer-error rate rather than actual
    # hallucinated content. The canonical key now says what it measures.
    "hallucination_rate": "ungrounded_rate",
    # ``citation_accuracy`` historically measured whether oracle evidence was
    # available in the API references at all, i.e. it duplicated
    # ``evidence_available``. Canonical consumers use the latter.
    "citation_accuracy": "evidence_available",
}

# Canonical metric sets per stage; missing values are filled with null so every
# run of the same kind exposes identical columns.
CANONICAL_SUMMARY_KEYS: dict[str, list[str]] = {
    "answer": [
        "correct_cases",
        "answer_accuracy",
        "groundedness",
        "ungrounded_rate",
        "abstention_accuracy",
        "evidence_available",
        "final_context_observable_rate",
        "final_context_evidence_coverage",
        "final_context_evidence_available",
        "citation_presence",
        "citation_correctness",
        "numeric_unit_accuracy",
        "formula_accuracy",
        "table_cell_accuracy",
        "cases",
    ],
    "retrieval": [
        "average_recall",
        "mrr",
        "context_precision",
        "object_hit_rate",
        "full_recall_cases",
        "cases",
    ],
    "selector": [
        "answer_accuracy",
        "groundedness",
        "ungrounded_rate",
        "abstention_accuracy",
        "evidence_available",
        "numeric_unit_accuracy",
        "formula_accuracy",
        "table_cell_accuracy",
        "candidate_recall",
        "selected_recall",
        "selection_precision",
        "role_coverage",
        "full_role_coverage_rate",
        "mean_candidate_context_chars",
        "mean_selected_context_chars",
        "mean_evidence_count",
        "changed_cases",
        "cases",
    ],
}


def normalize_metric_key(key: str) -> str:
    return METRIC_ALIASES.get(key, key)


def normalize_summary(summary: dict[str, Any], stage: str = "selector") -> dict[str, Any]:
    """Rename aliases, keep only known+extra scalars, and pad canonical keys."""
    normalized: dict[str, Any] = {}
    for key, value in summary.items():
        if isinstance(value, (dict, list)):
            continue
        normalized[normalize_metric_key(key)] = value
    keys = CANONICAL_SUMMARY_KEYS.get(stage, [])
    padded = {key: normalized.get(key) for key in keys}
    for key, value in normalized.items():
        if key not in padded:
            padded[key] = value
    return padded
