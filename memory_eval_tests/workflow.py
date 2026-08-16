"""Isolated dataset-ingest → retrieval → answer baseline for I1."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from lightrag.utils import logger
from memory_eval_tests.answer import (
    CONCISE_ANSWER_USER_PROMPT,
    evaluate_answers,
)
from memory_eval_tests.artifacts import EvaluationDefinition, RunContext
from memory_eval_tests.diagnosis import build_case_traces, build_diagnosis
from memory_eval_tests.execution import (
    allocate_execution_unit,
    finalize_execution_unit,
    load_execution_unit,
    preflight_execution_unit,
    start_execution_unit,
)
from memory_eval_tests.ingestion import (
    source_document_names,
    upload_dataset_files,
)
from memory_eval_tests.llm_analysis import analyze_run
from memory_eval_tests.metrics import normalize_summary
from memory_eval_tests.retrieval import evaluate_api


class IngestionFailure(RuntimeError):
    phase = "ingestion_failed"
    retryable = True


def _int_option(
    extra: dict[str, Any] | None, key: str, default: int
) -> int:
    raw = (extra or {}).get(key)
    try:
        return int(raw) if raw is not None else int(default)
    except (TypeError, ValueError):
        return int(default)


_FIGURE_FILE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _effective_vlm(baseline: dict[str, Any], dataset: Path) -> bool:
    """Resolve whether VLM image analysis should run for this evaluation.

    An explicit run parameter wins; otherwise the dataset manifest decides:
    datasets that declare ``figures`` modality or ship figure assets are
    processed with the VLM.  This mirrors what the generator actually embeds
    in the source documents and keeps ``run.json`` truthful about the run.
    """
    explicit = baseline.get("vlm")
    if explicit is not None:
        return bool(explicit)
    try:
        manifest = json.loads(
            (dataset / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return False
    if "figures" in (manifest.get("modalities") or []):
        return True
    for item in manifest.get("files") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").lower()
        if name.endswith(tuple(_FIGURE_FILE_EXTENSIONS)):
            return True
    return False


def _ingestion_process_options(
    baseline: dict[str, Any], extra: dict[str, Any] | None = None
) -> str:
    """Per-file ``process_options`` sent with each source document upload.

    VLM-enabled runs request image analysis (``Fi``); other runs keep plain
    fixed chunking.  An explicit ``extra.process_options`` override wins so
    callers can add tables/equations (e.g. ``Fit``) without code changes.
    """
    override = (extra or {}).get("process_options")
    if override:
        return str(override)
    return "Fi" if bool(baseline.get("vlm")) else "F"


def _dataset_pages(dataset: Path) -> int:
    """Return the source-document page count from the dataset manifest."""
    try:
        manifest = json.loads(
            (dataset / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return 0
    pages = manifest.get("pages")
    try:
        return int(pages) if pages is not None and int(pages) > 0 else 0
    except (TypeError, ValueError):
        return 0


def _dataset_figure_count(dataset: Path) -> int:
    """Return the number of figure assets declared by the dataset manifest."""
    try:
        manifest = json.loads(
            (dataset / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return 0
    count = 0
    for item in manifest.get("files") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").lower()
        if name.endswith(tuple(_FIGURE_FILE_EXTENSIONS)):
            count += 1
    return count


def _ingestion_timeout_seconds(
    baseline: dict[str, Any],
    extra: dict[str, Any] | None,
    dataset: Path,
) -> int:
    """Resolve the per-run document ingestion wait budget.

    An explicit ``extra.ingestion_timeout_seconds`` or baseline value wins.
    Otherwise the default scales with the dataset so large documents are not
    killed mid-extraction: a 200-page dataset takes several hours of
    entity/relation extraction on local 8B models, far beyond the old flat
    5400s ceiling, and every embedded figure adds a VLM analysis of roughly
    2-4 minutes (50 figures ≈ 2-3 extra hours).
    """
    override = (extra or {}).get("ingestion_timeout_seconds")
    if override is not None:
        try:
            return max(60, int(override))
        except (TypeError, ValueError):
            pass
    explicit = baseline.get("ingestion_timeout_seconds")
    if explicit is not None:
        try:
            return max(60, int(explicit))
        except (TypeError, ValueError):
            pass
    pages = _dataset_pages(dataset)
    figures = _dataset_figure_count(dataset)
    # ~90s/page of parsing/extraction plus ~180s per VLM figure analysis,
    # with a generous 12h ceiling for very large stress documents.
    return min(max(5400, pages * 90 + figures * 180), 43200)


def _ingestion_wait_progress(
    context: RunContext, total_documents: int
) -> Any:
    """Return a live-progress callback for the ingestion wait loop.

    The upload itself reports per-file progress, but the wait for parsing and
    KG extraction can run for hours (large datasets, VLM image analysis).  The
    track-status payload lets us publish a meaningful message during that wait
    instead of leaving ``progress.json`` frozen at the last upload step.
    """
    started = time.monotonic()
    last_publish = 0.0
    last_summary: tuple[tuple[str, ...], int] | None = None

    def publish(payload: dict[str, Any]) -> None:
        documents = payload.get("documents") or []
        statuses: list[str] = []
        chunk_counts: list[int] = []
        for doc in documents:
            status = str(doc.get("status") or "unknown")
            if status not in statuses:
                statuses.append(status)
            try:
                chunk_counts.append(int(doc.get("chunks_count") or 0))
            except (TypeError, ValueError):
                pass
        elapsed_minutes = int((time.monotonic() - started) / 60)
        elapsed = (
            f"{elapsed_minutes // 60} 小时 {elapsed_minutes % 60} 分"
            if elapsed_minutes >= 60
            else f"{elapsed_minutes} 分"
        )
        parts = [f"文档状态: {', '.join(statuses)}", f"已等待 {elapsed}"]
        if chunk_counts and any(chunk_counts):
            parts.append(f"已生成 {sum(chunk_counts)} 个 chunk")
        summary = (tuple(statuses), sum(chunk_counts))
        now = time.monotonic()
        nonlocal last_publish, last_summary
        state_changed = summary != last_summary and now - last_publish >= 15
        heartbeat_due = now - last_publish >= 300
        if state_changed or heartbeat_due:
            last_publish = now
            last_summary = summary
            context.progress(
                "running",
                0,
                total_documents,
                "ingestion",
                "正在上传、解析并建立文档索引（" + "；".join(parts) + "）",
            )

    return publish


def _runtime_options(
    baseline: dict[str, Any], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return answer controls plus extraction-specific integrity safeguards."""
    answer_num_predict = int(baseline.get("num_predict") or 4096)
    answer_num_ctx = int(baseline.get("num_ctx") or 16384)
    extraction_timeout = int(
        baseline.get("extraction_llm_timeout_seconds") or 1800
    )
    extraction_max_async = _int_option(
        extra, "extraction_max_async", int(baseline.get("extraction_max_async") or 2)
    )
    query_max_async = _int_option(
        extra, "query_max_async", int(baseline.get("query_max_async") or 2)
    )
    generation = {
        key: baseline[key]
        for key in ("num_ctx", "num_predict", "temperature")
        if key in baseline and baseline[key] is not None
    }
    generation["max_async"] = max(1, query_max_async)
    return {
        "skip_kg": not bool(baseline.get("kg", True)),
        "generation": generation,
        # A KG extraction response contains many structured rows and must not
        # inherit a smaller answer-output budget.  JSON removes the fragile
        # delimiter parser, while the record cap keeps the response bounded.
        # Large documents (e.g. 200-page) routinely exceed a 16K window on a
        # dense chunk, so extraction gets a 32K window and a 16K output
        # budget instead of the answer-level 8K cap.
        "extraction_generation": {
            "num_ctx": max(answer_num_ctx, 32768),
            "num_predict": max(answer_num_predict, 16384),
            "temperature": float(baseline.get("temperature") or 0),
        },
        "extraction_execution": {
            "timeout_seconds": max(1, extraction_timeout),
            "max_async": max(1, extraction_max_async),
        },
        "extraction_safeguards": {
            "use_json": True,
            "max_records": 40,
            "max_entities": 16,
            # Do not run a continuation pass: a truncated first response is
            # not recoverable without risking partial/invented rows, so the
            # document fails closed instead of indexing an incomplete graph.
            # The 32K window / 16K output budget keeps truncation exceptional.
            "max_gleaning": 0,
        },
    }


def _profile(context: RunContext) -> dict[str, Any]:
    """Build the one supported product runtime from server configuration.

    The WebUI exposes only controls that are applied to this isolated process.
    Historical versioned environment profiles and comparison arms were hidden
    from users but still changed execution, so they are deliberately not part
    of a normal evaluation any more.
    """
    return {
        "id": "server-default",
        "name": "当前服务器配置",
        "version": 1,
        "configuration": {
            "execution_mode": "managed_local",
            "retention_policy": "retain",
            "query": {
                "provider": context.environment.get("llm_binding") or "ollama",
                "model": context.baseline.get("model")
                or context.environment.get("llm_model")
                or "qwen3:8b",
            },
            "embedding": {
                "provider": context.environment.get("embedding_binding") or "ollama",
                "model": context.environment.get("embedding_model") or "bge-m3:latest",
            },
            "parser_engine": context.baseline.get("engine") or "native",
        },
    }


def _receipt(upload: dict[str, Any], unit: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    uploaded = upload.get("uploaded") or []
    terminal = [item.get("track_status") or {} for item in uploaded]
    waited = bool(upload.get("waited"))

    def processing_succeeded(item: dict[str, Any]) -> bool:
        if not waited:
            return item.get("status") == "success"
        track = item.get("track_status") or {}
        return track.get("passed") is True

    succeeded = [item for item in uploaded if processing_succeeded(item)]
    failed = [item for item in uploaded if item not in succeeded]
    documents = []
    for item in uploaded:
        track = item.get("track_status") or {}
        failure_reason = (
            track.get("error")
            or track.get("message")
            or item.get("message")
        )
        documents.append(
            {
                "file_name": item.get("file_name", {"value": "unknown", "reason": "upload response omitted file name"}),
                "content_sha256": item.get("content_sha256", {"value": "unknown", "reason": "upload did not record a content hash"}),
                "upload_id": item.get("track_id") or item.get("id") or {"value": "unknown", "reason": "upload response omitted an id"},
                "upload_status": item.get("status"),
                "processing_status": track,
                "parse_time": track.get("parse_time") or {"value": "unknown", "reason": "track status omits parse time"},
                "failure_reason": failure_reason,
                "reused": bool(item.get("reused")),
            }
        )
    chunk_count = 0
    for track in terminal:
        for document in track.get("documents") or []:
            try:
                chunk_count += int(document.get("chunks_count") or 0)
            except (TypeError, ValueError):
                pass
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
        "chunk_count": (
            chunk_count
            if chunk_count
            else {"value": "unknown", "reason": "current API track status omits chunk count"}
        ),
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


def _source_documents(dataset: Path) -> list[str]:
    """Return the dataset's source documents, never its scoring artefacts."""
    try:
        manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise IngestionFailure("dataset manifest is unreadable; cannot identify source documents")
    names = source_document_names(manifest)
    if not names:
        raise IngestionFailure("dataset manifest declares no source documents to ingest")
    return names


def _rerank_enabled(profile: dict[str, Any]) -> bool:
    """Use reranking only when this server profile actually configures it."""
    reranker = (profile.get("configuration") or {}).get("reranker")
    return isinstance(reranker, dict) and str(reranker.get("provider") or "").lower() not in {
        "",
        "null",
        "none",
    }


def _format_rate(value: Any) -> str:
    return f"{float(value):.1%}" if isinstance(value, (int, float)) else "—"


def _diagnosis_markdown(diagnosis: dict[str, Any]) -> str:
    distribution = diagnosis.get("cause_distribution") or {}
    actionable = {
        str(cause): count
        for cause, count in distribution.items()
        if cause not in {"not_applicable", "unclassified"} and count
    }
    lines = [
        "## 失败归因",
        "",
    ]
    if not actionable:
        lines.extend(["本次没有需要归因的失败题。", ""])
        return "\n".join(lines)
    labels = {
        "abstention_failure": "拒答结果不正确",
        "retrieval_miss": "检索未命中",
        "selection_or_truncation_miss": "上下文选择或截断不足",
        "generation_or_prompt_failure": "回答与标准答案不符",
    }
    lines.append(f"- 可归因覆盖率：{_format_rate(diagnosis.get('diagnosis_coverage'))}")
    for cause, count in actionable.items():
        lines.append(f"- {labels.get(cause, cause)}：{count} 题")
    unavailable = (diagnosis.get("trace_availability") or {}).get("context_unavailable", 0)
    if unavailable:
        lines.append(f"- {unavailable} 题缺少最终上下文记录，需人工复核")
    lines.extend(
        [
            "",
            "归因只在最终上下文可观测时给出；逐题证据与归因记录可在“逐题详情”中查看。",
            "",
        ]
    )
    return "\n".join(lines)


def _report_markdown(answer: dict[str, Any], diagnosis: dict[str, Any]) -> str:
    total = int(answer.get("cases") or 0)
    correct = answer.get("correct_cases")
    if not isinstance(correct, int):
        correct = sum(bool(row.get("exact_match")) for row in answer.get("results") or [])
    uncertain = int(answer.get("uncertain_answers") or 0)
    lines = [
        "# 测评报告",
        "",
        "## 结果概览",
        "",
        f"- 正确题数 / 总题数：{correct} / {total}",
        f"- 回答准确率：{_format_rate(answer.get('answer_accuracy'))}",
        f"- 证据支撑率：{_format_rate(answer.get('groundedness'))}",
    ]
    if uncertain:
        lines.append(f"- 待复核题数：{uncertain}")
    lines.extend(["", _diagnosis_markdown(diagnosis)])
    return "\n".join(lines)


def _prepare(context: RunContext) -> None:
    """Allocate once before the initial envelope makes the manifest immutable."""
    # Fail before any model call if the manifest contains only evaluation
    # artefacts or is otherwise unable to identify a source document.
    _source_documents(context.dataset)
    profile = context.environment_profile or _profile(context)
    preflight_execution_unit(profile)
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
        "configuration_fingerprint": hashlib.sha256(
            json.dumps(profile.get("configuration") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "effective_configuration": profile.get("configuration"),
    }


def _runner(context: RunContext) -> dict[str, Any]:
    _prepare(context)
    profile = context.environment_profile
    unit = context.execution_unit
    assert profile is not None and unit is not None
    outcome = "interrupted"
    try:
        source_documents = _source_documents(context.dataset)
        oracle = json.loads((context.dataset / "oracle.json").read_text(encoding="utf-8"))
        max_cases = int(context.baseline.get("max_cases") or 0) or None
        all_questions = list(oracle.get("questions") or [])
        selected_types = context.baseline.get("question_types") or []
        if selected_types:
            all_questions = [
                question
                for question in all_questions
                if question.get("question_type") in selected_types
            ]
        if max_cases is not None:
            from memory_eval_tests.sampling import sample_evenly

            all_questions = sample_evenly(all_questions, max_cases)
        selected_types_value = list(selected_types) or None
        retrieval_question_count = sum(
            question.get("expected_behavior") != "abstain"
            for question in all_questions
            if isinstance(question, dict)
        )
        answer_question_count = len(all_questions)
        context.progress("running", 0, 1, "runtime", "正在准备独立运行环境")
        unit = start_execution_unit(
            output_dir=context.output_dir,
            profile=profile,
            unit=unit,
            api_key=context.environment.get("api_key"),
            access_token=context.environment.get("access_token"),
        runtime_options=_runtime_options(context.baseline, context.extra),
        )
        context.execution_unit = unit
        context.environment["rag_api_url"] = unit["runtime_endpoint"]
        context.runtime_snapshot = unit["runtime_snapshot"]
        baseline = context.baseline
        # VLM image analysis: an explicit run parameter wins, otherwise
        # auto-enable for datasets whose manifest declares figures.  Persist
        # the effective value so run.json / reports reflect what actually ran.
        baseline["vlm"] = _effective_vlm(baseline, context.dataset)
        process_options = _ingestion_process_options(baseline, context.extra)
        baseline["process_options"] = process_options
        # Keep answers short and grounded: a single concise paragraph with an
        # explicit no-boilerplate instruction.  Slower local 8B models then
        # spend their output budget on the answer, not on preamble.
        baseline["response_type"] = str(
            baseline.get("response_type") or "Single Paragraph"
        )
        baseline["answer_user_prompt"] = str(
            baseline.get("answer_user_prompt") or CONCISE_ANSWER_USER_PROMPT
        )
        ingestion_timeout = _ingestion_timeout_seconds(
            baseline, context.extra, context.dataset
        )
        baseline["ingestion_timeout_seconds"] = ingestion_timeout
        context.progress("running", 1, 1, "runtime", "独立运行环境已就绪")
        context.progress(
            "running", 0, len(source_documents), "ingestion", "正在上传、解析并建立文档索引"
        )
        upload = upload_dataset_files(
            dataset_source=str(context.dataset),
            rag_api_url=unit["runtime_endpoint"],
            wait=True,
            timeout_seconds=ingestion_timeout,
            api_key=context.environment.get("api_key"),
            access_token=context.environment.get("access_token"),
            confirmed_hashes=_confirmed_hashes(context.output_dir),
            file_names=source_documents,
            process_options=process_options,
            progress_callback=lambda completed, total: context.progress(
                "running",
                completed,
                total,
                "ingestion",
                "正在上传、解析并建立文档索引",
            ),
            wait_progress_callback=_ingestion_wait_progress(
                context, len(source_documents)
            ),
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
        if retrieval_question_count:
            context.progress(
                "running", 0, retrieval_question_count, "retrieval", "正在评测检索结果"
            )
        top_k = int(baseline.get("top_k") or 5)
        chunk_top_k = int(baseline.get("chunk_top_k") or 5)
        enable_rerank = _rerank_enabled(profile)
        retrieval = evaluate_api(
            dataset_source=str(context.dataset),
            rag_api_url=unit["runtime_endpoint"],
            mode=str(baseline.get("mode") or "mix"),
            top_k=top_k,
            chunk_top_k=chunk_top_k,
            max_cases=max_cases,
            api_key=context.environment.get("api_key"),
            access_token=context.environment.get("access_token"),
            enable_rerank=enable_rerank,
            question_types=selected_types_value,
            progress_callback=lambda completed, total: context.progress(
                "running",
                completed,
                total,
                "retrieval",
                "正在评测检索结果",
            ),
        )
        if answer_question_count:
            context.progress(
                "running", 0, answer_question_count, "answer", "正在生成回答并评分"
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
            evaluation_trace=True,
            enable_rerank=enable_rerank,
            question_types=selected_types_value,
            response_type=str(baseline.get("response_type") or "Single Paragraph"),
            user_prompt=str(
                baseline.get("answer_user_prompt") or CONCISE_ANSWER_USER_PROMPT
            ),
            max_concurrency=_int_option(
                context.extra,
                "query_max_async",
                int(baseline.get("query_max_async") or 2),
            ),
            progress_callback=lambda completed, total: context.progress(
                "running",
                completed,
                total,
                "answer",
                "正在生成回答并评分",
            ),
        )
        context.progress("running", 0, 1, "report", "正在汇总评分结果并生成报告")
        case_traces = build_case_traces(
            oracle=oracle,
            retrieval_results=retrieval.get("results") or [],
            answer_results=answer.get("results") or [],
            retrieval_mode=str(baseline.get("mode") or "mix"),
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
        report = _report_markdown(answer, diagnosis)
        analysis_extra: dict[str, Any] = {}
        try:
            answer_rows = answer.get("results") or []
            analysis_md, analysis_extra = asyncio.run(
                analyze_run(
                    output_dir=context.output_dir,
                    case_traces=case_traces,
                    run_summary={
                        "correct": answer.get("correct_cases")
                        or sum(bool(row.get("exact_match")) for row in answer_rows),
                        "total": int(answer.get("cases") or len(answer_rows)),
                        "accuracy": answer.get("answer_accuracy"),
                        "groundedness": answer.get("groundedness"),
                    },
                    model=str(baseline.get("model") or "qwen3:8b"),
                    host=os.getenv("LLM_BINDING_HOST") or "http://127.0.0.1:11434",
                )
            )
            report = report + "\n" + analysis_md
        except Exception as exc:  # noqa: BLE001 - analysis must never fail the run
            logger.warning(f"LLM run analysis skipped: {type(exc).__name__}: {exc}")
        context.progress("running", 1, 1, "report", "评分结果与报告已生成")
        methods = [
            {
                "method": "retrieval",
                "label": "检索结果",
                "params": {
                    "mode": baseline.get("mode"),
                    "top_k": top_k,
                    "chunk_top_k": chunk_top_k,
                },
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
            "report": report,
            "extra": {
                "ingestion_receipt": "ingestion_receipt.json",
                "index_receipt": "index_receipt.json",
                "execution_unit": "execution_unit.json",
                "case_trace": "case_trace.json",
                "diagnosis": "diagnosis.json",
                "diagnosis_coverage": diagnosis["diagnosis_coverage"],
                "cause_distribution": diagnosis["cause_distribution"],
                "trace_availability": diagnosis["trace_availability"],
                **analysis_extra,
            },
        }
    except Exception:
        outcome = "failed"
        raise
    finally:
        finalize_execution_unit(output_dir=context.output_dir, unit=unit, outcome=outcome)


definition = EvaluationDefinition(
    id="end_to_end_baseline",
    label="端到端测评",
    description="在独立 LightRAG 工作空间内完成文档入库、索引、检索、回答与评分。",
    default_baseline={
        "mode": "mix",
        "top_k": 5,
        "chunk_top_k": 5,
        "max_total_tokens": 8192,
        # This controls answer generation. Extraction gets an independent 8K
        # integrity budget in ``_runtime_options``.
        "num_predict": 4096,
    },
    extra_schema={
        "allow_partial_ingestion": "bool",
        "ingestion_success_threshold": "float",
        "ingestion_timeout_seconds": "int",
    },
    prepare=_prepare,
    runner=_runner,
)
