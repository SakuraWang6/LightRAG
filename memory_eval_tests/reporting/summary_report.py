"""Aggregate all standard run envelopes into one SUMMARY report.

Replaces the hand-maintained summary document: ``runs/SUMMARY.md`` (+ JSON).
The old document is left untouched as an archive.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
            try:
                found.append((Path(dirpath), _read_json(Path(dirpath) / "run.json")))
            except (OSError, ValueError):
                continue
    return found


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "通过" if value else "未通过"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _metric(summary: dict[str, Any], *keys: str) -> Any:
    """First non-None value among canonical/legacy metric keys."""
    for key in keys:
        value = summary.get(key)
        if value is not None:
            return value
    return None


def _normalized_summary(method: dict[str, Any]) -> dict[str, Any]:
    from memory_eval_tests.experiments.common.metrics import normalize_metric_key

    return {
        normalize_metric_key(key): value
        for key, value in (method.get("summary") or {}).items()
    }


def render_markdown(runs_root: Path, envelopes: list[tuple[Path, dict[str, Any]]]) -> str:
    lines = [
        "# 评测结果总览（自动生成）",
        "",
        f"生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
    ]
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for run_dir, envelope in envelopes:
        by_kind.setdefault(envelope.get("kind", "experiment"), []).append((run_dir, envelope))

    if envelopes:
        lines.extend(["## 运行清单", "", "| Kind | 实验 | 数据集 | 状态 | 更新时间 |", "|---|---|---|---|---|"])
        for run_dir, envelope in envelopes:
            experiment = envelope.get("experiment") or {}
            lines.append(
                f"| {envelope.get('kind')} | {experiment.get('label') or run_dir.name} | "
                f"{envelope.get('baseline', {}).get('dataset') or '-'} | {_fmt(envelope.get('status'))} | "
                f"{str(envelope.get('created_at') or '')[:19]} |"
            )
        lines.append("")

    experiments = by_kind.get("experiment", [])
    if experiments:
        lines.append("## 实验对比")
        for run_dir, envelope in experiments:
            experiment = envelope.get("experiment") or {}
            lines.extend(["", f"### {experiment.get('label') or run_dir.name}", ""])
            description = experiment.get("description")
            if description:
                lines.extend([description, ""])
            methods = envelope.get("methods") or []
            if methods:
                keys = [
                    key
                    for key in (
                        "answer_accuracy",
                        "groundedness",
                        "ungrounded_rate",
                        "abstention_accuracy",
                        "evidence_available",
                        "candidate_recall",
                        "selected_recall",
                        "selection_precision",
                        "role_coverage",
                        "retrieval_recall",
                        "mean_selected_context_chars",
                        "mean_context_chars",
                        "cases",
                    )
                    if any(key in _normalized_summary(m) for m in methods)
                ]
                if keys:
                    lines.append("| 方法 | " + " | ".join(keys) + " |")
                    lines.append("|" + "---|" * (len(keys) + 1))
                    for method in methods:
                        summary = _normalized_summary(method)
                        lines.append(
                            "| "
                            + " | ".join(
                                [method.get("label") or method.get("method") or "?", *[_fmt(summary.get(key)) for key in keys]]
                            )
                            + " |"
                        )
                    lines.append("")

    offline = by_kind.get("offline", [])
    if offline:
        lines.extend(["## 离线审计", "", "| 数据集 | 状态 | Chunk 溯源覆盖 | 事实命中率 | 位置覆盖 |", "|---|---:|---:|---:|---:|"])
        for run_dir, envelope in sorted(offline, key=lambda item: str(item[1].get("baseline", {}).get("dataset"))):
            summary = next(
                (m.get("summary") or {} for m in (envelope.get("methods") or []) if m.get("method") == "offline_summary"),
                {},
            )
            audit = {
                key: value
                for method in (envelope.get("methods") or [])
                if method.get("method") != "offline_summary"
                for key, value in (method.get("summary") or {}).items()
            }
            lines.append(
                f"| {envelope.get('baseline', {}).get('dataset') or run_dir.name} | {_fmt(envelope.get('status'))} | "
                f"{_fmt(audit.get('chunk_sidecar_coverage'))} | {_fmt(audit.get('chunk_fact_hit_rate') or audit.get('fact_evidence_hit_rate'))} | "
                f"{_fmt(audit.get('position_coverage') or audit.get('meaningful_position_coverage'))} |"
            )
        lines.append("")

    online = by_kind.get("online", [])
    if online:
        lines.extend(["## 在线评测", "", "| 运行 | 数据集 | Recall@K | MRR | Accuracy | Groundedness | 未支撑率 |", "|---|---:|---:|---:|---:|---:|---:|"])
        for run_dir, envelope in online:
            methods = {m.get("method"): m for m in (envelope.get("methods") or [])}
            retrieval = (methods.get("retrieval") or {}).get("summary") or {}
            answer = (methods.get("answer") or {}).get("summary") or {}
            lines.append(
                f"| {run_dir.name} | {envelope.get('baseline', {}).get('dataset') or '-'} | "
                f"{_fmt(retrieval.get('average_recall'))} | {_fmt(retrieval.get('mrr'))} | "
                f"{_fmt(answer.get('answer_accuracy') or answer.get('accuracy'))} | {_fmt(answer.get('groundedness'))} | "
                f"{_fmt(_metric(answer, 'ungrounded_rate', 'hallucination_rate'))} |"
            )
        lines.append("")
    return "\n".join(lines)


def build_summary(runs_root: Path, output_stem: str = "SUMMARY") -> dict[str, Any]:
    envelopes = _envelopes(runs_root)
    markdown = render_markdown(runs_root, envelopes)
    json_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_count": len(envelopes),
        "runs": [
            {
                "run_id": envelope.get("run_id"),
                "kind": envelope.get("kind"),
                "label": (envelope.get("experiment") or {}).get("label"),
                "dataset": (envelope.get("baseline") or {}).get("dataset"),
                "status": envelope.get("status"),
                "created_at": envelope.get("created_at"),
            }
            for _, envelope in envelopes
        ],
    }
    (runs_root / f"{output_stem}.md").write_text(markdown, encoding="utf-8")
    (runs_root / f"{output_stem}.json").write_text(
        json.dumps(json_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return json_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate run envelopes into SUMMARY.md/json")
    parser.add_argument("--runs-root", type=Path, default=Path("memory_eval_tests/runs"))
    parser.add_argument("--output-stem", default="SUMMARY")
    args = parser.parse_args(argv)
    payload = build_summary(args.runs_root, args.output_stem)
    print(f"Wrote {args.runs_root / (args.output_stem + '.md')} ({payload['run_count']} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
