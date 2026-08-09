from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


def load_reports(paths: list[Path]) -> list[dict[str, Any]]:
    reports = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["_source"] = str(path)
        reports.append(payload)
    return reports


def render_markdown(reports: list[dict[str, Any]]) -> str:
    lines = ["# LightRAG Memory Evaluation Report", ""]
    for report in reports:
        lines.append(f"## {report.get('_source', 'report')}")
        lines.append("")
        for key in (
            "mode",
            "passed",
            "top_k",
            "max_cases",
            "max_facts",
            "cases",
            "average_recall",
            "mrr",
            "context_precision",
            "object_hit_rate",
            "answer_accuracy",
            "numeric_unit_accuracy",
            "formula_accuracy",
            "table_cell_accuracy",
            "abstention_accuracy",
            "evidence_available",
            "groundedness",
            "ungrounded_rate",
            "blocks",
            "chunks",
            "objects",
            "relations",
            "position_coverage",
            "meaningful_position_coverage",
            "page_or_bbox_position_coverage",
            "layout_accuracy_evaluable",
            "complex_layout_hit_rate",
            "textbox_hit_rate",
            "complex_layout_missed",
            "chunk_sidecar_coverage",
            "chunk_fact_hit_rate",
            "caption_chunk_hit_rate",
            "reference_chunk_hit_rate",
            "docx_ref_fields",
            "ref_field_target_rate",
            "ref_field_sidecar_hit_rate",
            "ref_field_chunk_hit_rate",
            "oracle_cross_reference_objects",
            "oracle_cross_reference_block_hit_rate",
            "oracle_cross_reference_chunk_hit_rate",
            "fact_evidence_hit_rate",
            "dataset_size_bytes",
            "generation_time_seconds",
            "generation_peak_memory_mb",
            "sidecar_size_bytes",
            "parse_time_seconds",
        ):
            display_values = _report_display_values(report)
            if key in display_values:
                value = display_values[key]
                if isinstance(value, float):
                    value = f"{value:.4f}"
                lines.append(f"- **{key}**: {value}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def summarize_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = (
        "_source",
        "dataset_id",
        "mode",
        "top_k",
        "max_cases",
        "max_facts",
        "cases",
        "average_recall",
        "mrr",
        "context_precision",
        "object_hit_rate",
        "answer_accuracy",
        "numeric_unit_accuracy",
        "formula_accuracy",
        "table_cell_accuracy",
        "abstention_accuracy",
        "evidence_available",
        "groundedness",
        "ungrounded_rate",
        "blocks",
        "chunks",
        "objects",
        "relations",
        "position_coverage",
        "meaningful_position_coverage",
        "page_or_bbox_position_coverage",
        "layout_accuracy_evaluable",
        "complex_layout_hit_rate",
        "textbox_hit_rate",
        "complex_layout_missed",
        "chunk_sidecar_coverage",
        "chunk_fact_hit_rate",
        "caption_chunk_hit_rate",
        "reference_chunk_hit_rate",
        "docx_ref_fields",
        "ref_field_target_rate",
        "ref_field_sidecar_hit_rate",
        "ref_field_chunk_hit_rate",
        "oracle_cross_reference_objects",
        "oracle_cross_reference_block_hit_rate",
        "oracle_cross_reference_chunk_hit_rate",
        "fact_evidence_hit_rate",
        "dataset_size_bytes",
        "generation_time_seconds",
        "generation_peak_memory_mb",
        "sidecar_size_bytes",
        "parse_time_seconds",
        "passed",
    )
    rows = []
    for report in reports:
        display_values = _report_display_values(report)
        rows.append({key: display_values[key] for key in keys if key in display_values})
    return rows


def _report_display_values(report: dict[str, Any]) -> dict[str, Any]:
    from memory_eval_tests.experiments.common.metrics import normalize_metric_key

    values = {normalize_metric_key(key): value for key, value in report.items()}
    complex_layout = report.get("complex_layout_text_preservation")
    if isinstance(complex_layout, dict):
        values["complex_layout_hit_rate"] = complex_layout.get("hit_rate")
        values["textbox_hit_rate"] = complex_layout.get("textbox_hit_rate")
        missed = complex_layout.get("missed") or []
        values["complex_layout_missed"] = ", ".join(
            str(item.get("object_id") or item.get("title"))
            for item in missed
            if isinstance(item, dict)
        )
    return values


def render_json(reports: list[dict[str, Any]]) -> str:
    return json.dumps(summarize_reports(reports), ensure_ascii=False, indent=2) + "\n"


def render_csv(reports: list[dict[str, Any]]) -> str:
    rows = summarize_reports(reports)
    fieldnames = sorted({key for row in rows for key in row.keys()})
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render evaluation JSON reports as Markdown.")
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", choices=("markdown", "json", "csv"), default="markdown")
    args = parser.parse_args(argv)
    reports = load_reports(args.reports)
    if args.format == "json":
        rendered = render_json(reports)
    elif args.format == "csv":
        rendered = render_csv(reports)
    else:
        rendered = render_markdown(reports)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
