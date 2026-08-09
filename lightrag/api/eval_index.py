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

from memory_eval_tests.experiments.common.envelope import build_conditions
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


def _summary_metrics(methods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union of scalar summary metrics across methods, canonical order first."""
    ordered = [
        "answer_accuracy",
        "accuracy",
        "groundedness",
        "ungrounded_rate",
        "abstention_accuracy",
        "evidence_available",
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
                # A canonical key always wins over a legacy alias when both
                # exist, regardless of dict iteration order.
                if normalized not in values or key == normalized:
                    values[normalized] = value
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
        "meta": {},
        "report_md": content,
        "toc": _markdown_toc(content),
        "error": None,
    }


def _run_record(
    runs_root: Path,
    run_dir: Path,
    envelope: dict[str, Any],
    *,
    with_artifacts: bool,
) -> dict[str, Any]:
    kind = envelope.get("kind", "experiment")
    experiment = envelope.get("experiment") or {}
    baseline = envelope.get("baseline") or {}
    dataset = baseline.get("dataset") or envelope.get("dataset")
    methods = envelope.get("methods") or []
    dataset_meta = _dataset_meta(runs_root, dataset)
    conditions = build_conditions(
        envelope.get("environment") or {},
        baseline,
        dataset_meta,
        method_count=len(methods),
    )
    progress = _read_progress(run_dir)
    run_id = envelope.get("run_id") or run_dir.name
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
        "legacy": bool(envelope.get("legacy", False)),
        "restarts": int(envelope.get("restarts") or 0),
        "last_restart_resume": envelope.get("last_restart_resume"),
        "label": experiment.get("label") or run_dir.name,
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
        cases = _flatten_cases(methods)
        if cases["rows"]:
            artifacts.append(
                {
                    "rel_path": "cases",
                    "kind": "cases",
                    "title": "逐题明细",
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
