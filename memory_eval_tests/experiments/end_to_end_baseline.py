"""Isolated dataset-ingest → retrieval → answer baseline for I1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lightrag.api import eval_profiles
from memory_eval_tests.experiments.common import ExperimentSpec, RunContext, normalize_summary
from memory_eval_tests.experiments.diagnosis import build_case_traces, build_diagnosis
from memory_eval_tests.experiments.execution_unit import (
    allocate_execution_unit,
    finalize_execution_unit,
    load_execution_unit,
    start_execution_unit,
)
from memory_eval_tests.online.answer_eval import evaluate_answers
from memory_eval_tests.online.index_runner import upload_dataset_files
from memory_eval_tests.online.retrieval_eval import evaluate_api


class IngestionFailure(RuntimeError):
    phase = "ingestion_failed"
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
    documents = []
    for item in uploaded:
        track = item.get("track_status") or {}
        documents.append(
            {
                "file_name": item.get("file_name", {"value": "unknown", "reason": "upload response omitted file name"}),
                "content_sha256": item.get("content_sha256", {"value": "unknown", "reason": "upload did not record a content hash"}),
                "upload_id": item.get("track_id") or item.get("id") or {"value": "unknown", "reason": "upload response omitted an id"},
                "upload_status": item.get("status"),
                "processing_status": track,
                "parse_time": track.get("parse_time") or {"value": "unknown", "reason": "track status omits parse time"},
                "failure_reason": item.get("message") or track.get("error") or track.get("message"),
                "reused": bool(item.get("reused")),
            }
        )
    ingestion = {
        "workspace_id": unit["workspace_id"],
        "storage_id": unit["storage_id"],
        "documents": documents,
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


def _confirmed_hashes(output_dir: Path) -> set[str]:
    """Reuse only receipts that already reached a confirmed processed state."""
    try:
        receipt = json.loads((output_dir / "ingestion_receipt.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    confirmed: set[str] = set()
    for document in receipt.get("documents") or []:
        digest = document.get("content_sha256")
        status = document.get("processing_status") or {}
        if isinstance(digest, str) and status.get("passed") is True:
            confirmed.add(digest)
    return confirmed


def _allow_partial(context: RunContext) -> tuple[bool, float]:
    allow = str(context.extra.get("allow_partial_ingestion") or "false").lower() in {
        "1", "true", "yes", "on"
    }
    try:
        threshold = float(context.extra.get("ingestion_success_threshold") or 1.0)
    except (TypeError, ValueError) as exc:
        raise IngestionFailure("ingestion_success_threshold must be a number") from exc
    if not 0 < threshold <= 1:
        raise IngestionFailure("ingestion_success_threshold must be in (0, 1]")
    return allow, threshold


def _dataset_formats(dataset: Path) -> list[str]:
    """Use every created manifest document, never a runner-global default."""
    try:
        manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["docx"]
    formats = {
        str(item.get("format")).lower()
        for item in manifest.get("files") or []
        if isinstance(item, dict) and item.get("status") == "created" and item.get("format")
    }
    return sorted(formats) or ["docx"]


def _prepare(context: RunContext) -> None:
    """Allocate once before the initial envelope makes the manifest immutable."""
    profile = context.environment_profile or _profile(context)
    unit = context.execution_unit or load_execution_unit(context.output_dir)
    if unit is None:
        unit = allocate_execution_unit(
            run_id=context.run_id, output_dir=context.output_dir, profile=profile
        )
    context.environment_profile = profile
    context.execution_unit = unit
    context.execution_manifest["execution_unit"] = {
        "workspace_id": unit.get("workspace_id"),
        "storage_id": unit.get("storage_id"),
        "mode": unit.get("mode"),
        "profile": unit.get("profile"),
        "retention_policy": unit.get("retention_policy"),
    }


def _runner(context: RunContext) -> dict[str, Any]:
    _prepare(context)
    profile = context.environment_profile
    unit = context.execution_unit
    assert profile is not None and unit is not None
    outcome = "interrupted"
    try:
        unit = start_execution_unit(
            output_dir=context.output_dir,
            profile=profile,
            unit=unit,
            api_key=context.environment.get("api_key"),
            access_token=context.environment.get("access_token"),
        )
        context.execution_unit = unit
        context.environment["rag_api_url"] = unit["runtime_endpoint"]
        context.runtime_snapshot = unit["runtime_snapshot"]
        baseline = context.baseline
        context.progress("running", 1, 7, "upload", "uploading isolated dataset files")
        upload = upload_dataset_files(
            dataset_source=str(context.dataset),
            rag_api_url=unit["runtime_endpoint"],
            formats=_dataset_formats(context.dataset),
            wait=True,
            timeout_seconds=int(baseline.get("ingestion_timeout_seconds") or 5400),
            api_key=context.environment.get("api_key"),
            access_token=context.environment.get("access_token"),
            confirmed_hashes=_confirmed_hashes(context.output_dir),
        )
        ingestion, index = _receipt(upload, unit)
        allow_partial, threshold = _allow_partial(context)
        total_documents = len(ingestion["documents"])
        success_rate = ingestion["successful_documents"] / total_documents if total_documents else 0.0
        ingestion.update(
            {
                "allow_partial_ingestion": allow_partial,
                "success_threshold": threshold,
                "success_rate": success_rate,
                "meets_success_threshold": success_rate >= threshold,
            }
        )
        (context.output_dir / "ingestion_receipt.json").write_text(
            json.dumps(ingestion, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (context.output_dir / "index_receipt.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if not ingestion["passed"] and not (
            allow_partial and ingestion["meets_success_threshold"]
        ):
            raise IngestionFailure("required dataset documents did not meet the ingestion success threshold")
        context.progress("running", 3, 7, "retrieval", "evaluating retrieval")
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
        context.progress("running", 5, 7, "answer", "evaluating answers")
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
        oracle = json.loads((context.dataset / "oracle.json").read_text(encoding="utf-8"))
        case_traces = build_case_traces(
            oracle=oracle,
            retrieval_results=retrieval.get("results") or [],
            answer_results=answer.get("results") or [],
        )
        diagnosis = build_diagnosis(case_traces)
        (context.output_dir / "case_trace.json").write_text(
            json.dumps(
                {"schema_version": "1.0", "cases": case_traces},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (context.output_dir / "diagnosis.json").write_text(
            json.dumps(diagnosis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
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
        outcome = "complete"
        return {
            "status": "complete",
            "methods": methods,
            "report": "# 隔离端到端基线\n\n数据集已在独立执行单元中入库、索引、检索与评分。\n",
            "extra": {
                "ingestion_receipt": "ingestion_receipt.json",
                "index_receipt": "index_receipt.json",
                "execution_unit": "execution_unit.json",
                "case_trace": "case_trace.json",
                "diagnosis": "diagnosis.json",
                "diagnosis_coverage": diagnosis["diagnosis_coverage"],
                "cause_distribution": diagnosis["cause_distribution"],
                "trace_availability": diagnosis["trace_availability"],
            },
        }
    except Exception:
        outcome = "failed"
        raise
    finally:
        finalize_execution_unit(output_dir=context.output_dir, unit=unit, outcome=outcome)


spec = ExperimentSpec(
    id="end_to_end_baseline",
    label="隔离端到端基线",
    description="在已发布环境档案分配的独立 LightRAG 工作空间内完成受控入库、检索和回答评测。",
    default_baseline={"mode": "mix", "top_k": 5, "chunk_top_k": 5, "max_total_tokens": 8192},
    extra_schema={
        "environment_profile_id": "str",
        "environment_profile_version": "int",
        "allow_partial_ingestion": "bool",
        "ingestion_success_threshold": "float",
    },
    prepare=_prepare,
    runner=_runner,
)
