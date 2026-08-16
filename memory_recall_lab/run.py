"""Command-line entry point for a recall-only LightRAG experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import threading
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from memory_eval_tests.artifacts import (
    EvaluationDefinition,
    RunContext,
    append_run_event,
    build_execution_manifest,
    build_failure,
    capture_environment,
    read_progress,
    redact_launch_extra,
    redact_sensitive_text,
    selected_case_ids,
    write_envelope,
    write_progress,
)
from memory_eval_tests.execution import (
    allocate_execution_unit,
    finalize_execution_unit,
    load_execution_unit,
    preflight_execution_unit,
    start_execution_unit,
)
from memory_eval_tests.ingestion import upload_dataset_files
from memory_eval_tests.workflow import (
    _allow_partial,
    _confirmed_hashes,
    _effective_vlm,
    _ingestion_wait_progress,
    _profile,
    _receipt,
    _rerank_enabled,
    _runtime_options,
    _source_documents,
)

from memory_recall_lab.retrieval import evaluate_recall
from memory_recall_lab.audit.ranking import write_ranking_audit
from memory_recall_lab.config import (
    ConfigError,
    ExperimentConfig,
    apply_environment,
    default_config_path,
    load_config,
    resolved_to_dict,
    resolved_to_yaml,
)

RECALL_DEFAULTS: dict[str, Any] = {
    "mode": "naive",
    "top_k": 20,
    "chunk_top_k": 20,
    "model": "qwen3:8b",
    "num_ctx": 16384,
    "num_predict": 4096,
    "max_total_tokens": 8192,
    "temperature": 0,
    "extraction_llm_timeout_seconds": 1800,
    "extraction_max_async": 2,
    "query_max_async": 2,
    "kg": False,
    "vlm": False,
    "engine": "native",
    "max_cases": 0,
}

_ACTIVE_CONFIG: ExperimentConfig | None = None


def _recall_process_options(
    baseline: dict[str, Any], extra: dict[str, Any] | None = None
) -> str:
    """Build per-file process options, including ``!`` when KG extraction is off.

    The product workflow relies on ``LIGHTRAG_PARSER=*:native-!`` for its KG
    toggle, but an explicit upload ``process_options`` is the authoritative
    per-file selector.  Recall-only experiments therefore encode ``!`` here
    directly so naive/skip-KG runs truly skip entity/relation extraction.
    """
    override = (extra or {}).get("process_options")
    if override:
        return str(override)
    options = "Fi" if bool(baseline.get("vlm")) else "F"
    if not bool(baseline.get("kg", True)):
        options += "!"
    return options


class _Tee:
    """Write through to the real stream while appending to run.log."""

    def __init__(self, real: Any, log: Any) -> None:
        self.real = real
        self.log = log

    def write(self, data: str) -> int:
        self.real.write(data)
        self.log.write(data)
        return len(data)

    def flush(self) -> None:
        self.real.flush()
        self.log.flush()

    def isatty(self) -> bool:
        return self.real.isatty()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)


@contextmanager
def _tee_log(output_dir: Path) -> Iterator[None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "run.log").open("a", encoding="utf-8") as log:
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = _Tee(old_out, log)
        sys.stderr = _Tee(old_err, log)
        try:
            yield
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            sys.stdout, sys.stderr = old_out, old_err


def _log(output_dir: Path, message: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "run.log").open("a", encoding="utf-8") as handle:
        handle.write(
            f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
            f"{redact_sensitive_text(message)}\n"
        )


def _install_sigterm_handler(output_dir: Path) -> None:
    def _handle(signum: int, frame: Any) -> None:
        write_progress(
            output_dir,
            status="terminating",
            done=0,
            total=1,
            phase="terminating",
            message="SIGTERM received",
        )
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle)


def _prepare(context: RunContext) -> None:
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
            json.dumps(
                profile.get("configuration") or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "effective_configuration": profile.get("configuration"),
    }


def _format_rate(value: Any) -> str:
    return f"{float(value):.1%}" if isinstance(value, (int, float)) else "—"


def _report_markdown(report: dict[str, Any], label: str | None) -> str:
    summary = report.get("summary") or {}
    overall = summary.get("overall") or {}
    by_type = summary.get("by_question_type") or {}
    lines = [
        "# 召回实验报告",
        "",
        f"- 标签：{label or '未命名'}",
        f"- 检索模式：{report.get('mode')}",
        f"- Top-K：{report.get('top_k')} / Chunk Top-K：{report.get('chunk_top_k')}",
        f"- 题数：{report.get('cases')}",
        "",
        "## 总体指标",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| Recall@1 | {_format_rate(overall.get('recall_at_1'))} |",
        f"| Recall@3 | {_format_rate(overall.get('recall_at_3'))} |",
        f"| Recall@5 | {_format_rate(overall.get('recall_at_5'))} |",
        f"| Recall@{report.get('chunk_top_k')} | {_format_rate(overall.get('recall_at_k'))} |",
        f"| MRR（首个证据） | {float(overall.get('mrr') or 0):.3f} |",
        f"| Mean Fact MRR | {float(overall.get('mean_fact_mrr') or 0):.3f} |",
        f"| 全部证据命中@1 | {overall.get('full_recall_at_1')} / {overall.get('cases')} |",
        f"| 全部证据命中@3 | {overall.get('full_recall_at_3')} / {overall.get('cases')} |",
        "",
        "## 按题型",
        "",
        "| 题型 | 题数 | Recall@1 | Recall@3 | Recall@5 | MRR |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for question_type, metrics in by_type.items():
        lines.append(
            f"| {question_type} | {metrics.get('cases', 0)} | "
            f"{_format_rate(metrics.get('recall_at_1'))} | "
            f"{_format_rate(metrics.get('recall_at_3'))} | "
            f"{_format_rate(metrics.get('recall_at_5'))} | "
            f"{float(metrics.get('mrr') or 0):.3f} |"
        )
    lines.extend(["", "## Gold rank 分布", ""])
    distribution = overall.get("gold_rank_distribution") or {}
    labels = {
        "1": "Rank 1",
        "2": "Rank 2",
        "3": "Rank 3",
        "4_5": "Rank 4-5",
        "6_10": "Rank 6-10",
        "11_plus": "Rank 11+",
        "miss": "未命中",
    }
    for key, label in labels.items():
        lines.append(f"- {label}：{distribution.get(key, 0)}")
    lines.append("")
    return "\n".join(lines)


def _runner(context: RunContext) -> dict[str, Any]:
    _prepare(context)
    if _ACTIVE_CONFIG is not None:
        apply_environment(_ACTIVE_CONFIG)
    profile = context.environment_profile
    unit = context.execution_unit
    assert profile is not None and unit is not None
    outcome = "interrupted"
    try:
        source_documents = _source_documents(context.dataset)
        max_cases = int(context.baseline.get("max_cases") or 0) or None
        selected_types = context.baseline.get("question_types") or []
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
        baseline["vlm"] = _effective_vlm(baseline, context.dataset)
        baseline["process_options"] = _recall_process_options(baseline, context.extra)
        ingestion_timeout = 1800
        try:
            ingestion_timeout = int(context.extra.get("ingestion_timeout_seconds") or 1800)
        except (TypeError, ValueError):
            pass
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
            process_options=baseline["process_options"],
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
            raise RuntimeError("required dataset documents did not meet the ingestion success threshold")

        top_k = int(baseline.get("top_k") or 20)
        chunk_top_k = int(baseline.get("chunk_top_k") or 20)
        enable_rerank = _rerank_enabled(profile)
        context.progress(
            "running", 0, 1, "retrieval", "正在评测召回与 ranking"
        )
        recall = evaluate_recall(
            dataset_source=str(context.dataset),
            rag_api_url=unit["runtime_endpoint"],
            mode=str(baseline.get("mode") or "naive"),
            top_k=top_k,
            chunk_top_k=chunk_top_k,
            max_cases=max_cases,
            question_types=list(selected_types) or None,
            api_key=context.environment.get("api_key"),
            access_token=context.environment.get("access_token"),
            enable_rerank=enable_rerank,
            progress_callback=lambda completed, total: context.progress(
                "running",
                completed,
                total,
                "retrieval",
                "正在评测召回与 ranking",
            ),
        )
        (context.output_dir / "recall_report.json").write_text(
            json.dumps(recall, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (context.output_dir / "ranking.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "mode": recall["mode"],
                    "top_k": recall["top_k"],
                    "chunk_top_k": recall["chunk_top_k"],
                    "summary": recall["summary"],
                    "questions": recall["results"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if _ACTIVE_CONFIG is not None and _ACTIVE_CONFIG.evaluation.save_ranking_audit:
            write_ranking_audit(context.output_dir, recall)
            context.progress("running", 1, 1, "report", "ranking audit 已生成")
        report = _report_markdown(recall, context.label)
        context.progress("running", 1, 1, "report", "召回报告已生成")
        methods = [
            {
                "method": "retrieval",
                "label": "召回结果",
                "params": {
                    "mode": baseline.get("mode"),
                    "top_k": top_k,
                    "chunk_top_k": chunk_top_k,
                },
                "summary": recall["summary"]["overall"],
                "results": recall["results"],
            }
        ]
        outcome = "complete"
        return {
            "status": "complete",
            "methods": methods,
            "report": report,
            "extra": {
                "recall_report": "recall_report.json",
                "ranking": "ranking.json",
                "ranking_audit": (
                    "ranking_audit.json"
                    if _ACTIVE_CONFIG is not None
                    and _ACTIVE_CONFIG.evaluation.save_ranking_audit
                    else None
                ),
                "ingestion_receipt": "ingestion_receipt.json",
                "index_receipt": "index_receipt.json",
                "execution_unit": "execution_unit.json",
            },
        }
    except Exception:
        outcome = "failed"
        raise
    finally:
        finalize_execution_unit(output_dir=context.output_dir, unit=unit, outcome=outcome)


definition = EvaluationDefinition(
    id="recall_only",
    label="召回实验",
    description="在独立 LightRAG 工作空间内只完成入库、索引和召回/ranking 评测，不生成回答。",
    default_baseline=dict(RECALL_DEFAULTS),
    extra_schema={
        "allow_partial_ingestion": "bool",
        "ingestion_success_threshold": "float",
        "ingestion_timeout_seconds": "int",
    },
    prepare=_prepare,
    runner=_runner,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recall-only LightRAG experiment")
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="experiment YAML config (default: configs/a2_atomic_context.yaml)",
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--label", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--mode", default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--chunk-top-k", type=int, default=None)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--question-types", default=None)
    parser.add_argument("--skip-kg", action="store_true")
    parser.add_argument("--engine", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--access-token", default=None)
    parser.add_argument("--runs-root", type=Path, default=None)
    parser.add_argument("--extra", action="append", default=[], metavar="KEY=VALUE")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    overrides: dict[str, Any] = {}
    for key, value in (
        ("mode", args.mode),
        ("top_k", args.top_k),
        ("chunk_top_k", args.chunk_top_k),
        ("model", args.model),
        ("engine", args.engine),
    ):
        if value is not None:
            overrides[key] = value
    if args.skip_kg:
        overrides["skip_kg"] = True
    if args.max_cases:
        overrides["max_cases"] = args.max_cases
    if args.question_types:
        overrides["question_types"] = [
            item.strip() for item in args.question_types.split(",") if item.strip()
        ]
    try:
        resolved = load_config(args.config, overrides=overrides or None)
    except ConfigError as exc:
        raise SystemExit(f"configuration error: {exc}") from exc
    if not resolved.experiment.reproducible_from_current_code:
        reference = resolved.experiment.git_commit or "<recorded git commit>"
        raise SystemExit(
            f"experiment {resolved.experiment.name} is historical-only and not "
            f"reproducible from the current code path; reproduce it from "
            f"git commit {reference} instead of running it here"
        )
    global _ACTIVE_CONFIG
    _ACTIVE_CONFIG = resolved

    baseline = dict(definition.default_baseline)
    baseline["mode"] = resolved.runtime.mode
    baseline["top_k"] = resolved.runtime.top_k
    baseline["chunk_top_k"] = resolved.runtime.chunk_top_k
    baseline["model"] = resolved.runtime.model
    baseline["engine"] = resolved.runtime.engine
    baseline["kg"] = not resolved.runtime.skip_kg
    baseline["max_cases"] = resolved.runtime.max_cases
    if resolved.runtime.question_types:
        baseline["question_types"] = list(resolved.runtime.question_types)

    extra: dict[str, str] = {}
    for item in args.extra:
        key, _, value = item.partition("=")
        extra[key.strip()] = value.strip()

    environment = capture_environment(
        api_key=args.api_key, access_token=args.access_token
    )
    run_id = args.run_id or args.output_dir.name
    runs_root = args.runs_root or Path(
        os.getenv(
            "MEMORY_RECALL_RUNS_ROOT",
            str(Path(__file__).resolve().parents[1] / "memory_recall_lab" / "runs"),
        )
    )
    context = RunContext(
        definition=definition,
        dataset=args.dataset,
        output_dir=args.output_dir,
        baseline=baseline,
        environment=environment,
        run_id=run_id,
        label=args.label,
        extra=extra,
        runs_root=runs_root,
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    parameter_sources = {
        key: "user"
        if getattr(args, key, None) is not None
        else "default"
        for key in ("model", "mode", "top_k", "chunk_top_k", "engine")
    }
    context.execution_manifest = build_execution_manifest(
        dataset=args.dataset,
        evaluation_id=definition.id,
        evaluation_type="evaluation",
        parameters=baseline,
        parameter_sources=parameter_sources,
        started_at=context.started_at,
    )
    context.execution_manifest["case_selection"] = {
        "algorithm": "deterministic_even_stride_v1",
        "requested_max_cases": args.max_cases,
        "case_ids": selected_case_ids(args.dataset, args.max_cases),
    }
    context.execution_manifest["experiment"] = {
        "config_file": resolved.config_file,
        "name": resolved.experiment.name,
        "historical": resolved.experiment.historical,
        "legacy_mode": resolved.experiment.legacy_mode,
        "reproducible_from_current_code": resolved.experiment.reproducible_from_current_code,
        "resolved_config": resolved_to_dict(resolved),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "resolved_config.yaml").write_text(
        resolved_to_yaml(resolved), encoding="utf-8"
    )
    _install_sigterm_handler(args.output_dir)
    _log(args.output_dir, f"starting recall_only run_id={run_id} dataset={args.dataset}")
    append_run_event(
        args.output_dir,
        phase="starting",
        severity="info",
        message=f"starting recall-only experiment for {args.dataset.name}",
    )
    write_envelope(
        args.output_dir,
        context=context,
        status="running",
        methods=[],
        runs_root=runs_root,
        write_progress_file=False,
    )
    write_progress(args.output_dir, status="queued", done=0, total=1, phase="starting")
    with _tee_log(args.output_dir):
        try:
            payload = definition.runner(context)
            methods = payload.get("methods", [])
            report_md = payload.get("report", "")
            report_path = args.output_dir / "report.md"
            report_path.write_text(report_md, encoding="utf-8")
            status = payload.get("status", "complete")
            write_envelope(
                args.output_dir,
                context=context,
                status=status,
                methods=methods,
                report_rel_path=report_path.name,
                extra=payload.get("extra") or {},
                runs_root=runs_root,
                write_progress_file=False,
            )
            _log(args.output_dir, f"finished status={status} cases={len(methods)}")
            saved = read_progress(args.output_dir)
            total = int(saved.get("total") or 1)
            context.progress(status, total, total, "complete", f"run finished with status {status}")
        except Exception as exc:
            _log(args.output_dir, f"failed {type(exc).__name__}: {exc}")
            offset = append_run_event(
                args.output_dir,
                phase="execution",
                severity="error",
                message=f"{type(exc).__name__}: {exc}",
                error_type=type(exc).__name__,
            )
            failure = build_failure(
                phase="execution",
                error=exc,
                retryable=True,
                recommendation="inspect run.log, execution_unit.log and ingestion receipts before retrying",
                log_offset=offset,
            )
            write_envelope(
                args.output_dir,
                context=context,
                status="failed",
                methods=[],
                extra={"failure": failure},
                runs_root=runs_root,
                write_progress_file=False,
            )
            write_progress(
                args.output_dir,
                status="failed",
                done=0,
                total=1,
                phase="failed",
                message=f"{type(exc).__name__}: {exc}",
            )
            print(redact_sensitive_text(traceback.format_exc()))
            raise


if __name__ == "__main__":
    main()
