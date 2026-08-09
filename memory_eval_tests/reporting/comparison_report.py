from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


def build_comparison_rows(reports: list[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in reports:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows.append(_row_from_report(path, payload))
    return rows


def render_markdown(rows: list[dict[str, Any]]) -> str:
    columns = [
        "report",
        "dataset_id",
        "engine",
        "backend",
        "mode",
        "top_k",
        "cases",
        "average_recall",
        "mrr",
        "context_precision",
        "object_hit_rate",
        "answer_accuracy",
        "evidence_available",
        "passed",
    ]
    lines = ["# LightRAG Parser / Mode Comparison Report", ""]
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        values = [_format_value(row.get(column)) for column in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines).rstrip() + "\n"


def render_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, ensure_ascii=False, indent=2) + "\n"


def render_csv(rows: list[dict[str, Any]]) -> str:
    from io import StringIO

    fieldnames = sorted({key for row in rows for key in row})
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _row_from_report(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    summary_reports = payload.get("reports") if isinstance(payload.get("reports"), dict) else {}
    dataset_id = payload.get("dataset_id") or _infer_dataset_id(path)
    return {
        "report": str(path),
        "dataset_id": dataset_id,
        "engine": payload.get("engine") or payload.get("parse_engine"),
        "backend": payload.get("backend") or _infer_backend(path, payload),
        "mode": payload.get("mode"),
        "top_k": payload.get("top_k"),
        "max_cases": payload.get("max_cases"),
        "max_facts": payload.get("max_facts"),
        "cases": payload.get("cases"),
        "average_recall": payload.get("average_recall"),
        "mrr": payload.get("mrr"),
        "context_precision": payload.get("context_precision"),
        "object_hit_rate": payload.get("object_hit_rate"),
        "answer_accuracy": payload.get("answer_accuracy"),
        "evidence_available": (
            payload.get("evidence_available")
            if payload.get("evidence_available") is not None
            else payload.get("citation_accuracy")
        ),
        "blocks": payload.get("blocks"),
        "chunks": payload.get("chunks"),
        "position_coverage": payload.get("position_coverage"),
        "chunk_sidecar_coverage": payload.get("chunk_sidecar_coverage"),
        "parse_time_seconds": payload.get("parse_time_seconds"),
        "passed": payload.get("passed"),
        "report_count": len(summary_reports),
    }


def _infer_backend(path: Path, payload: dict[str, Any]) -> str:
    if "retrieval" in path.stem:
        return "api" if payload.get("mode") != "sidecar" else "sidecar"
    if path.stem in {"sidecar", "layout", "cross_reference", "object_traceability"}:
        return path.stem
    return ""


def _infer_dataset_id(path: Path) -> str:
    parts = list(path.parts)
    if "offline" in parts:
        index = parts.index("offline")
        if index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare parser/mode evaluation reports.")
    parser.add_argument("reports", nargs="+", type=Path, help="Evaluation JSON reports.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", choices=("markdown", "json", "csv"), default="markdown")
    args = parser.parse_args(argv)

    rows = build_comparison_rows(args.reports)
    if args.format == "json":
        rendered = render_json(rows)
    elif args.format == "csv":
        rendered = render_csv(rows)
    else:
        rendered = render_markdown(rows)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
