"""Scale online validation, wrapped into the unified harness CLI."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from memory_eval_tests.experiments.common import (
    ExperimentSpec,
    RunContext,
    normalize_summary,
)
from memory_eval_tests.experiments.scale_validation import amain


def _flatten_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in results:
        base = {
            "question_id": case.get("question_id"),
            "question_group": case.get("question_group"),
            "question_type": case.get("question_type"),
            "candidate_recall": case.get("candidate_recall"),
            "candidate_count": case.get("candidate_count"),
        }
        for method in case.get("methods") or []:
            row = dict(base)
            row["method"] = method.get("method")
            row["context_chars"] = method.get("context_chars")
            row["answer"] = method.get("answer")
            row.update(method.get("metrics") or {})
            rows.append(row)
    return rows


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
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
        "ungrounded_rate": rate("ungrounded"),
        "abstention_accuracy": average("abstention_correct"),
        "evidence_available": average("evidence_available"),
        "numeric_unit_accuracy": average("numeric_unit_correct"),
        "formula_accuracy": average("formula_correct"),
        "table_cell_accuracy": average("table_cell_correct"),
        "candidate_recall": average("candidate_recall"),
        "mean_context_chars": average("context_chars"),
    }


def _runner(context: RunContext) -> dict[str, Any]:
    import asyncio

    dataset = context.dataset
    baseline = context.baseline
    stage = context.extra.get("stage", "eval")
    if stage not in ("ingest", "cache", "eval"):
        raise ValueError(
            f"scale --extra stage must be ingest|cache|eval, got {stage!r}"
        )
    output_json = context.output_dir / "scale_eval.json"
    output_md = context.output_dir / "scale_report.md"
    sidecar_tables = context.extra.get("sidecar_tables")
    args = SimpleNamespace(
        stage=stage,
        dataset=dataset,
        storage_dir=Path(
            context.environment.get("storage_dir")
            or (context.output_dir / "rag_storage")
        ),
        ollama_url=context.environment["ollama_url"],
        model=baseline["model"],
        output_json=output_json if stage == "eval" else None,
        output_md=output_md if stage == "eval" else None,
        max_cases=int(baseline.get("max_cases") or 0),
        skip_kg=not bool(baseline.get("kg", True)),
        resume=bool(context.extra.get("resume", "1") in {"1", "true", "yes"}),
        sidecar_tables=Path(sidecar_tables) if sidecar_tables else None,
        extra_arms=False,
    )
    asyncio.run(amain(args))
    if stage != "eval":
        return {
            "methods": [],
            "report": "",
            "status": "complete",
            "extra": {"stage": stage},
        }
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    rows = _flatten_results(payload.get("results", []))
    by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_method[row["method"]].append(row)
    methods = [
        {
            "method": method,
            "label": method,
            "params": {
                "dataset": dataset.name,
                "skip_kg": not baseline.get("kg", True),
            },
            "summary": normalize_summary(_aggregate(method_rows), "selector"),
            "results": method_rows,
        }
        for method, method_rows in sorted(by_method.items())
    ]
    report = output_md.read_text(encoding="utf-8")
    return {
        "methods": methods,
        "report": report,
        "status": payload.get("status", "complete"),
        "extra": {"stage": stage},
    }


spec = ExperimentSpec(
    id="scale",
    label="规模在线验证",
    description=(
        "在不同规模数据集（20p/200p）上跑完整的入库→关键词缓存→检索+回答流水线，"
        "度量规模增长下检索召回与回答质量的退化；KG 默认开启，与历史 scale 口径一致。"
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
            "axis": "dataset_scale",
            "label": "数据集规模",
            "arms": [
                {"arm": "20p"},
                {"arm": "200p"},
            ],
        }
    ],
    runner=_runner,
    extra_schema={"stage": "str"},
)
