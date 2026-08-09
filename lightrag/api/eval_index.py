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
import sys
from pathlib import Path
from typing import Any

# The server console script does not put the repository root on sys.path;
# ``memory_eval_tests`` is a repo-local package, so expose it explicitly.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from memory_eval_tests.experiments.common.envelope import build_conditions

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


def default_runs_root() -> Path:
    return Path(__file__).resolve().parents[2] / "memory_eval_tests" / "runs"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dataset_meta(runs_root: Path, dataset: str | None) -> dict[str, Any]:
    if not dataset:
        return {}
    manifest = Path(runs_root).parent.parent / "memory_data_service" / "generated" / dataset / "manifest.json"
    try:
        payload = _read_json(manifest)
    except (OSError, ValueError):
        return {}
    return {
        "dataset": payload.get("dataset_id") or dataset,
        "pages": payload.get("pages"),
        "tier": payload.get("tier"),
        "profile": payload.get("profile"),
        "formats": payload.get("formats"),
        "title": payload.get("title"),
    }


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
            for key, value in case.items():
                if isinstance(value, (int, float, bool, str, list)) and value is not None:
                    if isinstance(value, list):
                        value = ", ".join(str(item) for item in value[:5])
                    elif isinstance(value, str) and len(value) > 300:
                        value = value[:300]
                    row[key] = value
                    if key not in {c["key"] for c in columns}:
                        columns.append({"key": key, "label": _humanize(key)})
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
        "hallucination_rate",
        "abstention_accuracy",
        "citation_accuracy",
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
            if isinstance(value, (int, float, bool)) and key not in values:
                values[key] = value
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
        (line.strip().lstrip("# ") for line in content.splitlines() if line.strip().startswith("#")),
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
            _OFFLINE_LABELS.get(m.get("method") or "", m.get("label") or m.get("method") or "")
            for m in methods
            if m.get("method") != "offline_summary"
            and (m.get("summary") or {}).get("passed") is False
        ]
    record: dict[str, Any] = {
        "id": run_id,
        "run_dir": str(run_dir),
        "kind": kind,
        "label": experiment.get("label") or run_dir.name,
        "description": experiment.get("description") or "",
        "dataset": dataset or dataset_meta.get("dataset"),
        "updated_at": envelope.get("created_at"),
        "status": envelope.get("status"),
        "conditions": conditions,
        "progress": progress,
        "failed_checks": failed_checks,
        "headline": {} if kind == "experiment" else {
            metric["key"]: metric for metric in _summary_metrics(methods)
        },
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


def _envelopes(runs_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    found = []
    for dirpath, dirnames, filenames in os.walk(str(runs_root), followlinks=False):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in {"rag_storage", "sidecar", ".git", "__pycache__"}
            and not d.endswith(".parsed")
            and not d.startswith(".")
        ]
        if "run.json" in filenames:
            run_dir = Path(dirpath)
            try:
                found.append((run_dir, _read_json(run_dir / "run.json")))
            except (OSError, ValueError):
                continue
    return found


def scan_runs(runs_root: Path) -> list[dict[str, Any]]:
    records = [
        _run_record(runs_root, run_dir, envelope, with_artifacts=False)
        for run_dir, envelope in _envelopes(runs_root)
    ]
    records.sort(key=lambda r: r["updated_at"] or "", reverse=True)
    return records


def load_run(runs_root: Path, run_id: str) -> dict[str, Any] | None:
    for run_dir, envelope in _envelopes(runs_root):
        record = _run_record(runs_root, run_dir, envelope, with_artifacts=False)
        if record["id"] == run_id:
            return _run_record(runs_root, run_dir, envelope, with_artifacts=True)
    return None
