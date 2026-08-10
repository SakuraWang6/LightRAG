"""Isolated dataset-ingest → retrieval → answer baseline for I1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lightrag.api import eval_profiles
from memory_eval_tests.experiments.common import ExperimentSpec, RunContext, normalize_summary
from memory_eval_tests.experiments.execution_unit import (
    allocate_execution_unit,
    start_execution_unit,
)
from memory_eval_tests.online.answer_eval import evaluate_answers
from memory_eval_tests.online.index_runner import upload_dataset_files
from memory_eval_tests.online.retrieval_eval import evaluate_api


class IngestionFailure(RuntimeError):
    phase = "ingestion"
    retryable = True


def _profile(context: RunContext) -> dict[str, Any]:
    profile_id = context.extra.get("environment_profile_id")
    raw_version = context.extra.get("environment_profile_version")
    if not profile_id or not raw_version:
        raise IngestionFailure(
            "end_to_end_baseline requires environment_profile_id and environment_profile_version"
        )
    try:
        version = int(raw_version)
    except ValueError as exc:
        raise IngestionFailure("environment_profile_version must be an integer") from exc
    if context.runs_root is None:
        raise IngestionFailure("runs_root is required to load environment profiles")
    profile = eval_profiles.get_profile_version(context.runs_root, profile_id, version)
    if profile is None:
        raise IngestionFailure("environment profile version was not found")
    if profile.get("status") != "published":
        raise IngestionFailure("end-to-end runs may only use a published environment profile")
    return profile


def _receipt(upload: dict[str, Any], unit: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    uploaded = upload.get("uploaded") or []
    succeeded = [item for item in uploaded if item.get("status") == "success"]
    terminal = [item.get("track_status") or {} for item in uploaded]
    failed = [item for item in uploaded if item not in succeeded]
    ingestion = {
        "workspace_id": unit["workspace_id"],
        "storage_id": unit["storage_id"],
        "documents": uploaded,
        "successful_documents": len(succeeded),
        "failed_documents": len(failed),
        "passed": bool(upload.get("passed")),
        "elapsed_seconds": upload.get("elapsed_seconds"),
    }
    index = {
        "workspace_id": unit["workspace_id"],
        "storage_id": unit["storage_id"],
        "index_completed_at": unit.get("started_at"),
        "successful_documents": len(succeeded),
        "failed_documents": len(failed),
        "processing_statuses": terminal,
        "chunk_count": {"value": "unknown", "reason": "current API track status omits chunk count"},
        "entity_count": {"value": "unknown", "reason": "current API track status omits entity count"},
        "relation_count": {"value": "unknown", "reason": "current API track status omits relation count"},
    }
    return ingestion, index


def _runner(context: RunContext) -> dict[str, Any]:
    profile = _profile(context)
    unit = allocate_execution_unit(run_id=context.run_id, output_dir=context.output_dir, profile=profile)
    unit = start_execution_unit(
        output_dir=context.output_dir,
        profile=profile,
        unit=unit,
        api_key=context.environment.get("api_key"),
        access_token=context.environment.get("access_token"),
    )
    context.environment["rag_api_url"] = unit["runtime_endpoint"]
    context.runtime_snapshot = unit["runtime_snapshot"]
    baseline = context.baseline
    upload = upload_dataset_files(
        dataset_source=str(context.dataset),
        rag_api_url=unit["runtime_endpoint"],
        formats=["docx"],
        wait=True,
        timeout_seconds=int(baseline.get("ingestion_timeout_seconds") or 5400),
        api_key=context.environment.get("api_key"),
        access_token=context.environment.get("access_token"),
    )
    ingestion, index = _receipt(upload, unit)
    (context.output_dir / "ingestion_receipt.json").write_text(
        json.dumps(ingestion, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (context.output_dir / "index_receipt.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not ingestion["passed"]:
        raise IngestionFailure("required dataset documents did not all reach processed state")
    top_k = int(baseline.get("top_k") or 5)
    chunk_top_k = int(baseline.get("chunk_top_k") or 5)
    max_cases = int(baseline.get("max_cases") or 0) or None
    retrieval = evaluate_api(
        dataset_source=str(context.dataset),
        rag_api_url=unit["runtime_endpoint"],
        mode=str(baseline.get("mode") or "mix"),
        top_k=top_k,
        max_cases=max_cases,
        api_key=context.environment.get("api_key"),
        access_token=context.environment.get("access_token"),
    )
    answer = evaluate_answers(
        dataset_source=str(context.dataset),
        rag_api_url=unit["runtime_endpoint"],
        mode=str(baseline.get("mode") or "mix"),
        top_k=top_k,
        chunk_top_k=chunk_top_k,
        max_total_tokens=int(baseline.get("max_total_tokens") or 8192),
        max_cases=max_cases,
        api_key=context.environment.get("api_key"),
        access_token=context.environment.get("access_token"),
    )
    methods = [
        {
            "method": "retrieval",
            "label": "检索结果",
            "params": {"mode": baseline.get("mode"), "top_k": top_k},
            "summary": normalize_summary(retrieval, "retrieval"),
            "results": retrieval.get("results", []),
        },
        {
            "method": "answer",
            "label": "回答结果",
            "params": {"mode": baseline.get("mode"), "top_k": top_k},
            "summary": normalize_summary(answer, "answer"),
            "results": answer.get("results", []),
        },
    ]
    return {
        "status": "complete",
        "methods": methods,
        "report": "# 隔离端到端基线\n\n数据集已在独立执行单元中入库、索引、检索与评分。\n",
        "extra": {"ingestion_receipt": "ingestion_receipt.json", "index_receipt": "index_receipt.json", "execution_unit": "execution_unit.json"},
    }


spec = ExperimentSpec(
    id="end_to_end_baseline",
    label="隔离端到端基线",
    description="在已发布环境档案分配的独立 LightRAG 工作空间内完成受控入库、检索和回答评测。",
    default_baseline={"mode": "mix", "top_k": 5, "chunk_top_k": 5, "max_total_tokens": 8192},
    extra_schema={"environment_profile_id": "str", "environment_profile_version": "int"},
    runner=_runner,
)
