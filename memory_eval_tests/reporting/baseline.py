"""Baseline / regression comparison across runs with repeated-measure variance.

Repeated runs of the same experiment + dataset + method form a group.  For
every scalar metric the report shows the group size, mean, standard deviation,
min/max and the delta against a chosen baseline run.  When a group has at
least three runs, a simple effect-size heuristic (|d| >= 0.8) flags a
"significant" change; smaller samples are explicitly marked as insufficient
instead of claiming significance.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path
from typing import Any

from memory_eval_tests.experiments.common.metrics import normalize_metric_key
from memory_eval_tests.reporting.report_envelope import write_report_envelope


def _envelopes(runs_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    found = []
    for dirpath, dirnames, filenames in os.walk(str(runs_root), followlinks=False):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in {"rag_storage", "sidecar", "inputs", ".git", "__pycache__"}
            and not d.endswith(".parsed")
            and not d.endswith("__parsed__")
            and not d.startswith(".")
        ]
        if "run.json" in filenames:
            try:
                found.append(
                    (
                        Path(dirpath),
                        json.loads(
                            (Path(dirpath) / "run.json").read_text(encoding="utf-8")
                        ),
                    )
                )
            except (OSError, ValueError):
                continue
    return found


def _summary_metrics(envelope: dict[str, Any]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for method in envelope.get("methods") or []:
        for key, value in (method.get("summary") or {}).items():
            normalized = normalize_metric_key(key)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and normalized not in metrics
            ):
                metrics[normalized] = float(value)
    return metrics


def _record(run_dir: Path, envelope: dict[str, Any]) -> dict[str, Any]:
    experiment = envelope.get("experiment") or {}
    baseline = envelope.get("baseline") or {}
    return {
        "id": str(envelope.get("run_id") or run_dir.name),
        "kind": envelope.get("kind", "experiment"),
        "label": experiment.get("label") or run_dir.name,
        "dataset": baseline.get("dataset"),
        "updated_at": envelope.get("created_at", ""),
        "metrics": _summary_metrics(envelope),
    }


def _significance_label(delta: float, group_std: float, n: int) -> str:
    if n < 3:
        return "样本不足"
    if group_std <= 0:
        return "差异较大（启发式）" if abs(delta) > 0 else "无差异"
    effect = abs(delta) / group_std
    return "差异较大（启发式）" if effect >= 0.8 else "差异不明显"


def build_baseline_table(
    records: list[dict[str, Any]],
    *,
    baseline_run_id: str | None = None,
) -> dict[str, Any]:
    """Group runs by (label, dataset, kind) and compare metrics to a baseline."""
    baseline = next(
        (record for record in records if record["id"] == baseline_run_id),
        None,
    )
    if baseline is None and records:
        baseline = max(records, key=lambda record: record["updated_at"])
    baseline_id = baseline["id"] if baseline else None

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (
            str(record["label"]),
            str(record.get("dataset") or ""),
            str(record["kind"]),
        )
        groups.setdefault(key, []).append(record)

    group_rows: list[dict[str, Any]] = []
    for (label, dataset, kind), members in sorted(groups.items()):
        metric_keys = sorted({key for member in members for key in member["metrics"]})
        for metric in metric_keys:
            values = [
                member["metrics"][metric]
                for member in members
                if metric in member["metrics"]
            ]
            n = len(values)
            mean = statistics.fmean(values) if values else None
            std = statistics.stdev(values) if n >= 2 else (0.0 if n == 1 else None)
            baseline_value = baseline["metrics"].get(metric) if baseline else None
            delta = (
                (mean - baseline_value)
                if (mean is not None and baseline_value is not None)
                else None
            )
            group_rows.append(
                {
                    "label": label,
                    "dataset": dataset,
                    "kind": kind,
                    "metric": metric,
                    "n": n,
                    "mean": mean,
                    "std": std,
                    "min": min(values) if values else None,
                    "max": max(values) if values else None,
                    "baseline": baseline_value,
                    "delta": delta,
                    "significance": (
                        _significance_label(delta, std or 0.0, n)
                        if delta is not None
                        else "n/a"
                    ),
                }
            )
    return {
        "baseline_run_id": baseline_id,
        "baseline_label": baseline["label"] if baseline else None,
        "groups": group_rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 基线/回归对比报告",
        "",
        f"基线运行：`{payload.get('baseline_run_id') or '—'}`"
        + (f"（{payload['baseline_label']}）" if payload.get("baseline_label") else ""),
        "",
        "| 分组 | 数据集 | 指标 | n | 均值 | 标准差 | 最小值 | 最大值 | 基线 | Δ | 标记 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload["groups"]:
        lines.append(
            f"| {row['label']} | {row['dataset'] or '—'} | {row['metric']} | {row['n']} | "
            f"{_fmt(row['mean'])} | {_fmt(row['std'])} | {_fmt(row['min'])} | {_fmt(row['max'])} | "
            f"{_fmt(row['baseline'])} | {_fmt(row['delta'])} | {row['significance']} |"
        )
    lines.extend(
        [
            "",
            "标记口径：`差异较大（启发式）` = 组均值与基线差值的效应量 |d| ≥ 0.8 且 "
            "n ≥ 3；`差异不明显` = 效应量低于阈值；`样本不足` = n < 3。该标记是"
            "效应量启发式，不是统计显著性检验；正式结论需要指定检验与样本量。",
        ]
    )
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}".rstrip("0").rstrip(".")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs-root", type=Path, default=Path("memory_eval_tests/runs")
    )
    parser.add_argument(
        "--baseline-run-id",
        default=None,
        help="Run id used as the baseline (default: newest run).",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("memory_eval_tests/runs/baseline_report.md")
    )
    parser.add_argument(
        "--no-envelope", action="store_true", help="Skip the kind=report envelope."
    )
    args = parser.parse_args(argv)

    records = [
        _record(run_dir, envelope) for run_dir, envelope in _envelopes(args.runs_root)
    ]
    payload = build_baseline_table(records, baseline_run_id=args.baseline_run_id)
    markdown = render_markdown(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown, encoding="utf-8")
    if not args.no_envelope:
        write_report_envelope(
            output_path=args.output,
            report_type="baseline",
            label="基线/回归对比报告",
            description="跨重复运行的基线对比与方差摘要（启发式显著性）。",
            baseline={"baseline_run_id": payload.get("baseline_run_id")},
        )
    print(f"Wrote {args.output} ({len(payload['groups'])} metric groups)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
