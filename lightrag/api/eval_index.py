"""Read-only evaluation-console index over standard ``run.json`` envelopes.

Every evaluation run writes a ``run.json`` envelope (see
``memory_eval_tests/experiments/common/envelope.py``). This module scans
``memory_eval_tests/runs`` for envelopes and normalizes them for the WebUI.
There is no SQLite cache: envelopes are small, and reading them directly keeps
the console always fresh, including in-flight progress files.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from memory_eval_tests.experiments.common.envelope import (
    build_conditions,
    redact_sensitive_text,
)
from memory_eval_tests.experiments.common.metrics import normalize_metric_key

_OFFLINE_LABELS = {
    "integrity": "完整性校验",
    "sidecar": "Sidecar 解析",
    "layout": "版式审计",
    "cross_reference": "交叉引用",
    "object_traceability": "对象可追溯性",
    "chunk_traceability": "Chunk 可追溯性",
    "performance": "性能基线",
    "retrieval_sidecar": "词法检索",
}

_SCAN_SKIP_DIRS = {
    "rag_storage",
    "sidecar",
    "inputs",
    ".git",
    "__pycache__",
}

# In-process scan cache: runs_root -> (timestamp, mtime signature, records).
# Within the TTL the cached list is served without walking the tree again; on
# expiry the mtime signature is re-checked so new/changed runs appear
# automatically, and POST /eval/refresh forces an immediate rebuild.
_SCAN_TTL_SECONDS = 15.0
_SCAN_INDEX_NAME = ".eval_index.json"
_scan_cache: dict[
    Path,
    tuple[float, tuple[tuple[str, int, int], ...], list[dict[str, Any]]],
] = {}


def default_runs_root() -> Path:
    configured = os.getenv("MEMORY_EVAL_RUNS_ROOT")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "memory_eval_tests" / "runs"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dataset_meta(runs_root: Path, dataset: str | None) -> dict[str, Any]:
    if not dataset:
        return {}
    candidates: list[Path] = []
    env_root = os.getenv("MEMORY_EVAL_DATASETS_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    candidates.append(
        Path(runs_root).parent.parent / "memory_data_service" / "generated"
    )
    candidates.append(Path.cwd() / "memory_data_service" / "generated")
    for root in dict.fromkeys(candidates):
        try:
            payload = _read_json(root / dataset / "manifest.json")
        except (OSError, ValueError):
            continue
        return {
            "dataset": payload.get("dataset_id") or dataset,
            "pages": payload.get("pages"),
            "tier": payload.get("tier"),
            "profile": payload.get("profile"),
            "formats": payload.get("formats"),
            "title": payload.get("title"),
        }
    return {}


def _scalar_rows(methods: list[dict[str, Any]]) -> dict[str, Any]:
    """Method comparison table: one row per method, scalar summary metrics."""
    rows: list[dict[str, Any]] = []
    columns: list[dict[str, str]] = []
    for method in methods:
        row: dict[str, Any] = {
            "method": method.get("method"),
            "label": method.get("label"),
        }
        for key, value in (method.get("summary") or {}).items():
            if isinstance(value, (int, float, bool, str)) and value is not None:
                key = normalize_metric_key(key)
                row[key] = value
                if key not in {c["key"] for c in columns}:
                    columns.append({"key": key, "label": _humanize(key)})
        rows.append(row)
    return {"columns": columns, "rows": rows}


def _flatten_cases(methods: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    columns: list[dict[str, str]] = []
    for method in methods:
        for case in method.get("results") or []:
            if not isinstance(case, dict):
                continue
            row: dict[str, Any] = {}
            detail: dict[str, Any] = {}
            for key, value in case.items():
                if isinstance(value, dict):
                    if key in {"retrieval", "final_context_evidence", "scorer"}:
                        detail[key] = value
                    continue
                if (
                    isinstance(value, (int, float, bool, str, list))
                    and value is not None
                ):
                    cell_value: Any = value
                    if isinstance(value, list):
                        # Structured retrieval evidence stays fully available
                        # for the per-case detail view; only the table cell is
                        # capped to keep payloads small.
                        if key in {
                            "hit_fact_ids",
                            "expected_fact_ids",
                            "top_contexts",
                            "hit_evidence",
                        }:
                            detail[key] = value
                        cell_value = ", ".join(str(item) for item in value[:5])
                    elif isinstance(value, str) and len(value) > 300:
                        detail[key] = value
                        cell_value = value[:300]
                    row[key] = cell_value
                    if key not in {c["key"] for c in columns}:
                        columns.append({"key": key, "label": _humanize(key)})
            if detail:
                row["detail"] = detail
            if "method" not in row:
                row["method"] = method.get("method")
            rows.append(row)
    return {"columns": columns, "rows": rows}


def _case_methods_for_run(
    experiment: dict[str, Any], methods: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return the rows a reviewer expects to see for a single evaluation.

    End-to-end runs generate both retrieval diagnostics and answer scoring
    internally.  They are complementary pipeline stages, not alternative
    methods.  The case review should therefore contain one answer sheet per
    question rather than two mixed rows per question.
    """
    if experiment.get("id") != "end_to_end_baseline":
        return methods
    answer_methods = [method for method in methods if method.get("method") == "answer"]
    retrieval = next(
        (method for method in methods if method.get("method") == "retrieval"), None
    )
    if not answer_methods:
        return methods
    if retrieval is None:
        return answer_methods
    return [_merge_end_to_end_case_methods(answer_methods[0], retrieval)]


def _merge_end_to_end_case_methods(
    answer_method: dict[str, Any], retrieval_method: dict[str, Any]
) -> dict[str, Any]:
    """Attach retrieval diagnostics to each answer sheet, never as duplicate rows."""
    retrieval_by_question = {
        str(row.get("question_id")): row
        for row in retrieval_method.get("results") or []
        if isinstance(row, dict) and row.get("question_id") is not None
    }
    merged_rows: list[dict[str, Any]] = []
    for answer_row in answer_method.get("results") or []:
        if not isinstance(answer_row, dict):
            continue
        row = dict(answer_row)
        question_id = str(row.get("question_id") or "")
        retrieval = retrieval_by_question.get(question_id)
        if row.get("expected_behavior") == "abstain":
            row["retrieval"] = {"status": "not_applicable"}
        elif retrieval is None:
            row["retrieval"] = {
                "status": "unavailable",
                "reason": "no retrieval trace was produced for this question",
            }
        else:
            hit_evidence = retrieval.get("hit_evidence") or []
            ranks = [
                item.get("rank")
                for item in hit_evidence
                if isinstance(item, dict) and isinstance(item.get("rank"), int)
            ]
            row["retrieval"] = {
                "status": "observed",
                "recall_at_k": retrieval.get("recall_at_k"),
                "reciprocal_rank": retrieval.get("reciprocal_rank"),
                "context_precision": retrieval.get("context_precision"),
                "first_evidence_rank": min(ranks) if ranks else None,
                "expected_fact_ids": retrieval.get("expected_fact_ids") or [],
                "hit_fact_ids": retrieval.get("hit_fact_ids") or [],
                "hit_evidence": hit_evidence,
                "top_contexts": retrieval.get("top_contexts") or [],
            }
        merged_rows.append(row)
    return {**answer_method, "results": merged_rows}


def _hydrate_case_questions_from_trace(
    run_dir: Path, cases: dict[str, Any]
) -> None:
    """Backfill question text for runs created before answer rows stored it."""
    try:
        traces = _read_json(run_dir / "case_trace.json").get("cases") or []
    except (OSError, ValueError):
        return
    oracle_by_question_id = {
        str(trace.get("question_id")): trace.get("oracle") or {}
        for trace in traces
        if isinstance(trace, dict)
    }
    hydrated = False
    for row in cases.get("rows") or []:
        if not isinstance(row, dict):
            continue
        oracle = oracle_by_question_id.get(str(row.get("question_id") or ""))
        if not isinstance(oracle, dict):
            continue
        question = str(oracle.get("question") or "")
        if question and not row.get("question"):
            row["question"] = question
            hydrated = True
        if row.get("expected_behavior") is None and oracle.get("expected_behavior"):
            row["expected_behavior"] = oracle["expected_behavior"]
        if row.get("expected_behavior") == "abstain":
            detail = row.get("detail")
            retrieval = detail.get("retrieval") if isinstance(detail, dict) else None
            if isinstance(retrieval, dict) and retrieval.get("status") == "unavailable":
                retrieval.clear()
                retrieval["status"] = "not_applicable"
    if hydrated and not any(column.get("key") == "question" for column in cases.get("columns") or []):
        cases["columns"].insert(0, {"key": "question", "label": "Question"})


def _summary_metrics(methods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union of scalar summary metrics across methods, canonical order first."""
    ordered = [
        "correct_cases",
        "retrieval_cases",
        "answer_accuracy",
        "accuracy",
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
        "average_recall",
        "mrr",
        "context_precision",
        "object_hit_rate",
        "full_recall_cases",
        "candidate_recall",
        "selected_recall",
        "selection_precision",
        "role_coverage",
        "full_role_coverage_rate",
        "retrieval_recall",
        "mean_context_chars",
        "mean_selected_context_chars",
        "changed_cases",
        "cases",
    ]
    values: dict[str, Any] = {}
    for method in methods:
        for key, value in (method.get("summary") or {}).items():
            if isinstance(value, (int, float, bool)):
                normalized = normalize_metric_key(key)
                if normalized == "cases" and method.get("method") == "retrieval":
                    values["retrieval_cases"] = value
                    continue
                # A canonical key always wins over a legacy alias when both
                # exist, regardless of dict iteration order.
                if normalized not in values or key == normalized:
                    values[normalized] = value
    answer_rows = [
        row
        for method in methods
        if method.get("method") == "answer"
        for row in (method.get("results") or [])
        if isinstance(row, dict)
    ]
    if answer_rows:
        values.setdefault("correct_cases", sum(bool(row.get("exact_match")) for row in answer_rows))
        values["cases"] = len(answer_rows)
    metrics = []
    for key in ordered:
        if key in values:
            metrics.append(
                {
                    "key": key,
                    "label": _humanize(key),
                    "value": values[key],
                    "type": "bool" if isinstance(values[key], bool) else "number",
                }
            )
    for key, value in values.items():
        if key not in ordered:
            metrics.append(
                {
                    "key": key,
                    "label": _humanize(key),
                    "value": value,
                    "type": "bool" if isinstance(value, bool) else "number",
                }
            )
    return metrics


def _humanize(key: str) -> str:
    from memory_eval_tests.experiments.common.metrics import METRIC_LABELS

    return METRIC_LABELS.get(key, key.replace("_", " ").title())


def _read_progress(run_dir: Path) -> dict[str, Any]:
    try:
        return _read_json(run_dir / "progress.json")
    except (OSError, ValueError):
        return {}


def _read_events(run_dir: Path, limit: int = 500) -> list[dict[str, Any]]:
    """Read bounded, well-formed lifecycle events for a run detail view."""
    try:
        lines = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("message"), str):
            event["message"] = redact_sensitive_text(event["message"])
        events.append(event)
    return events


def _duration_seconds(envelope: dict[str, Any]) -> float | None:
    started = envelope.get("started_at")
    finished = envelope.get("finished_at")
    if not started or not finished:
        return None
    try:
        return round(
            (
                datetime.fromisoformat(finished) - datetime.fromisoformat(started)
            ).total_seconds(),
            3,
        )
    except ValueError:
        return None


def _markdown_toc(content: str) -> list[dict[str, Any]]:
    import re

    toc = []
    for line in content.splitlines():
        match = re.match(r"^(#{1,4})\s+(.+?)\s*#*\s*$", line)
        if match:
            toc.append({"level": len(match.group(1)), "title": match.group(2).strip()})
    return toc[:200]


def _report_artifact(run_dir: Path, envelope: dict[str, Any]) -> dict[str, Any] | None:
    rel = (envelope.get("reports") or {}).get("report.md")
    if not rel:
        return None
    path = run_dir / rel
    try:
        content = path.read_text(encoding="utf-8")[:2_000_000]
    except OSError:
        return None
    is_end_to_end = (envelope.get("experiment") or {}).get("id") == "end_to_end_baseline"
    if is_end_to_end:
        content = _end_to_end_report_content(run_dir, envelope)
    first = next(
        (
            line.strip().lstrip("# ")
            for line in content.splitlines()
            if line.strip().startswith("#")
        ),
        path.name,
    )
    return {
        "rel_path": str(rel),
        "kind": "markdown_report",
        "title": first or path.name,
        "updated_at": envelope.get("created_at"),
        "metrics": [],
        "table": {"columns": [], "rows": []},
        "meta": {
            "generated_by": "evaluation_program" if is_end_to_end else "stored_report",
            "uses_llm": False if is_end_to_end else None,
        },
        "report_md": content,
        "toc": _markdown_toc(content),
        "error": None,
    }


def _end_to_end_report_content(run_dir: Path, envelope: dict[str, Any]) -> str:
    """Present historical single-run reports in the same readable format.

    These reports are deterministic score summaries.  Re-rendering the view
    from the stored methods also removes the obsolete “isolated baseline”
    terminology without mutating an existing run directory.
    """
    methods = envelope.get("methods") or []
    answer = next(
        (method for method in methods if method.get("method") == "answer"),
        {},
    )
    summary = answer.get("summary") or {}
    rows = [row for row in answer.get("results") or [] if isinstance(row, dict)]
    total = len(rows) or int(summary.get("cases") or 0)
    correct = summary.get("correct_cases")
    if not isinstance(correct, int):
        correct = sum(bool(row.get("exact_match")) for row in rows)
    diagnosis: dict[str, Any] = {}
    try:
        value = _read_json(run_dir / "diagnosis.json")
        if isinstance(value, dict):
            diagnosis = value
    except (OSError, ValueError):
        pass
    def rate(value: Any) -> str:
        return f"{float(value):.1%}" if isinstance(value, (int, float)) else "—"
    lines = [
        "# 测评报告",
        "",
        "本报告由评测程序根据评分结果自动生成，不调用 LLM。",
        "",
        "## 结果概览",
        "",
        f"- 正确题数 / 总题数：{correct} / {total}",
        f"- 回答准确率：{rate(summary.get('answer_accuracy'))}",
        f"- 证据支撑率：{rate(summary.get('groundedness'))}",
        "",
        "## 失败归因",
        "",
    ]
    distribution = diagnosis.get("cause_distribution") or {}
    actionable = {
        str(cause): count
        for cause, count in distribution.items()
        if cause not in {"not_applicable", "unclassified"} and count
    }
    if not actionable:
        lines.append("本次没有需要归因的失败题。")
    else:
        labels = {
            "abstention_failure": "拒答结果不正确",
            "retrieval_miss": "检索未命中",
            "selection_or_truncation_miss": "上下文选择或截断不足",
            "generation_or_prompt_failure": "回答与标准答案不符",
        }
        lines.append(f"- 可归因覆盖率：{rate(diagnosis.get('diagnosis_coverage'))}")
        for cause, count in actionable.items():
            lines.append(f"- {labels.get(cause, cause)}：{count} 题")
    return "\n".join(lines) + "\n"


def _run_record(
    runs_root: Path,
    run_dir: Path,
    envelope: dict[str, Any],
    *,
    with_artifacts: bool,
) -> dict[str, Any]:
    persisted_kind = envelope.get("kind", "experiment")
    experiment = envelope.get("experiment") or {}
    # Older end-to-end runs were persisted as ``experiment`` even though each
    # run evaluates one document set with one configuration.  Normalize them
    # while indexing so historic results receive the same single-run review
    # screen as newly created evaluations.
    kind = (
        "online"
        if experiment.get("id") == "end_to_end_baseline"
        else persisted_kind
    )
    baseline = envelope.get("baseline") or {}
    execution_dataset = ((envelope.get("execution_manifest") or {}).get("dataset") or {}).get(
        "dataset_id"
    )
    dataset = baseline.get("dataset") or envelope.get("dataset") or (
        execution_dataset if isinstance(execution_dataset, str) else None
    )
    methods = envelope.get("methods") or []
    dataset_meta = _dataset_meta(runs_root, dataset)
    conditions = build_conditions(
        envelope.get("environment") or {},
        baseline,
        dataset_meta,
        method_count=(
            None
            if experiment.get("id") == "end_to_end_baseline"
            else len(methods)
        ),
    )
    progress = _read_progress(run_dir)
    run_id = envelope.get("run_id") or run_dir.name
    has_trust_contract = isinstance(envelope.get("execution_manifest"), dict) and isinstance(
        envelope.get("runtime_snapshot"), dict
    )
    compatibility_level = envelope.get("compatibility_level")
    if not isinstance(compatibility_level, str):
        compatibility_level = "current" if has_trust_contract else "legacy"
    legacy = bool(envelope.get("legacy", False)) or not has_trust_contract
    if legacy:
        compatibility_level = "legacy"
    failed_checks: list[str] = []
    if kind == "offline":
        failed_checks = [
            _OFFLINE_LABELS.get(
                m.get("method") or "", m.get("label") or m.get("method") or ""
            )
            for m in methods
            if m.get("method") != "offline_summary"
            and (m.get("summary") or {}).get("passed") is False
        ]
    record: dict[str, Any] = {
        "id": run_id,
        "run_dir": str(run_dir),
        "kind": kind,
        "legacy": legacy,
        "compatibility_level": compatibility_level,
        "restarts": int(envelope.get("restarts") or 0),
        "last_restart_resume": envelope.get("last_restart_resume"),
        "launch_params": envelope.get("launch_params"),
        "label": envelope.get("label") or experiment.get("label") or run_dir.name,
        "experiment": experiment.get("id"),
        "description": experiment.get("description") or "",
        "dataset": dataset or dataset_meta.get("dataset"),
        "updated_at": envelope.get("created_at"),
        "started_at": envelope.get("started_at"),
        "finished_at": envelope.get("finished_at"),
        "duration_seconds": _duration_seconds(envelope),
        "status": envelope.get("status"),
        "conditions": conditions,
        "progress": progress,
        "failed_checks": failed_checks,
        "headline": {}
        if kind == "experiment"
        else {metric["key"]: metric for metric in _summary_metrics(methods)},
        "variables": envelope.get("variables") or [],
        "artifact_titles": [],
        "execution_manifest": envelope.get("execution_manifest"),
        "runtime_snapshot": envelope.get("runtime_snapshot"),
        "diagnosis_coverage": envelope.get("diagnosis_coverage"),
        "cause_distribution": envelope.get("cause_distribution"),
        "trace_availability": envelope.get("trace_availability"),
        "declared_model": envelope.get("declared_model"),
        "effective_model": envelope.get("effective_model"),
        "configuration_mismatch": envelope.get("configuration_mismatch"),
        "failure": envelope.get("failure"),
        "events_path": envelope.get("events_path"),
    }
    if not with_artifacts:
        record["artifact_titles"] = [
            experiment.get("label") or run_dir.name,
            *(["报告"] if (envelope.get("reports") or {}).get("report.md") else []),
        ]
        return record

    artifacts: list[dict[str, Any]] = []
    if kind == "experiment":
        artifacts.append(
            {
                "rel_path": "methods",
                "kind": "experiment",
                "title": experiment.get("label") or run_dir.name,
                "updated_at": envelope.get("created_at"),
                "metrics": [],
                "table": _scalar_rows(methods),
                "meta": {
                    "description": experiment.get("description") or "",
                    "variables": envelope.get("variables") or [],
                    "cases": _flatten_cases(methods),
                },
                "report_md": None,
                "toc": [],
                "error": None,
            }
        )
    else:
        metrics = _summary_metrics(methods)
        artifacts.append(
            {
                "rel_path": "summary",
                "kind": "summary",
                "title": "结果摘要",
                "updated_at": envelope.get("created_at"),
                "metrics": metrics,
                "table": _scalar_rows(methods),
                "meta": {"description": experiment.get("description") or ""},
                "report_md": None,
                "toc": [],
                "error": None,
            }
        )
        cases = _flatten_cases(_case_methods_for_run(experiment, methods))
        if experiment.get("id") == "end_to_end_baseline":
            _hydrate_case_questions_from_trace(run_dir, cases)
        if cases["rows"]:
            artifacts.append(
                {
                    "rel_path": "cases",
                    "kind": "cases",
                    "title": "逐题详情",
                    "updated_at": envelope.get("created_at"),
                    "metrics": [],
                    "table": cases,
                    "meta": {},
                    "report_md": None,
                    "toc": [],
                    "error": None,
                }
            )
    report = _report_artifact(run_dir, envelope)
    if report:
        artifacts.append(report)
    record["artifacts"] = artifacts
    record["events"] = _read_events(run_dir)
    record["artifact_titles"] = [artifact["title"] for artifact in artifacts]
    return record


def _iter_envelope_dirs(runs_root: Path) -> list[Path]:
    """All directories containing a run.json, excluding heavy data dirs."""
    found = []
    for dirpath, dirnames, filenames in os.walk(str(runs_root), followlinks=False):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _SCAN_SKIP_DIRS
            and not d.endswith(".parsed")
            and not d.endswith("__parsed__")
            and not d.startswith(".")
        ]
        if "run.json" in filenames:
            found.append(Path(dirpath))
    return found


def _envelope_signature(runs_root: Path) -> tuple[tuple[str, int, int], ...]:
    entries: list[tuple[str, int, int]] = []
    for run_dir in _iter_envelope_dirs(runs_root):
        run_path = run_dir / "run.json"
        progress_path = run_dir / "progress.json"
        try:
            run_mtime = run_path.stat().st_mtime_ns
        except OSError:
            continue
        progress_mtime = (
            progress_path.stat().st_mtime_ns if progress_path.exists() else 0
        )
        entries.append((str(run_dir.relative_to(runs_root)), run_mtime, progress_mtime))
    return tuple(sorted(entries))


def clear_scan_cache(runs_root: Path | None = None) -> None:
    """Drop the scan cache (used by tests and the refresh endpoint)."""
    if runs_root is None:
        _scan_cache.clear()
    else:
        _scan_cache.pop(runs_root, None)
        try:
            (runs_root / _SCAN_INDEX_NAME).unlink()
        except FileNotFoundError:
            pass


def _scan_index_path(runs_root: Path) -> Path:
    return runs_root / _SCAN_INDEX_NAME


def _write_scan_index(
    runs_root: Path,
    signature: tuple[tuple[str, int, int], ...],
    records: list[dict[str, Any]],
) -> None:
    try:
        _scan_index_path(runs_root).write_text(
            json.dumps(
                {
                    "signature": [list(item) for item in signature],
                    "records": records,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _read_scan_index(
    runs_root: Path,
) -> tuple[tuple[tuple[str, int, int], ...], list[dict[str, Any]]] | None:
    try:
        payload = json.loads(_scan_index_path(runs_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    signature = tuple(tuple(item) for item in payload.get("signature", []))
    return signature, payload.get("records", [])


def _signature_matches_paths(
    runs_root: Path,
    signature: tuple[tuple[str, int, int], ...],
) -> bool:
    """Verify a persisted signature by stat'ing only the recorded paths."""
    for rel, run_mtime, progress_mtime in signature:
        run_dir = runs_root / rel
        try:
            if (run_dir / "run.json").stat().st_mtime_ns != run_mtime:
                return False
        except OSError:
            return False
        progress_path = run_dir / "progress.json"
        try:
            current_progress_mtime = progress_path.stat().st_mtime_ns
        except OSError:
            current_progress_mtime = 0
        if current_progress_mtime != progress_mtime:
            return False
    return True


def scan_runs(runs_root: Path, *, force: bool = False) -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _scan_cache.get(runs_root)
    if cached is not None and not force:
        cached_at, signature, records = cached
        if now - cached_at < _SCAN_TTL_SECONDS:
            return records
        if signature == _envelope_signature(runs_root):
            _scan_cache[runs_root] = (now, signature, records)
            return records
    if not force:
        persisted = _read_scan_index(runs_root)
        if persisted is not None:
            persisted_signature, records = persisted
            if _signature_matches_paths(runs_root, persisted_signature):
                _scan_cache[runs_root] = (now, persisted_signature, records)
                return records
    signature = _envelope_signature(runs_root)
    records = []
    for run_dir in _iter_envelope_dirs(runs_root):
        try:
            envelope = _read_json(run_dir / "run.json")
        except (OSError, ValueError):
            continue
        records.append(_run_record(runs_root, run_dir, envelope, with_artifacts=False))
    records.sort(key=lambda r: r["updated_at"] or "", reverse=True)
    _scan_cache[runs_root] = (now, signature, records)
    _write_scan_index(runs_root, signature, records)
    return records


def load_run(runs_root: Path, run_id: str) -> dict[str, Any] | None:
    for record in scan_runs(runs_root):
        if record["id"] == run_id:
            run_dir = Path(record["run_dir"])
            envelope = _read_json(run_dir / "run.json")
            return _run_record(runs_root, run_dir, envelope, with_artifacts=True)
    return None
