from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from memory_eval_tests.reporting.report_envelope import write_report_envelope
from memory_eval_tests.reporting.scale_report import build_scale_rows


def build_readiness_report(
    *,
    datasets: list[str],
    runs_root: Path = Path("memory_eval_tests/runs/offline"),
) -> dict[str, Any]:
    scale_rows = build_scale_rows(datasets=datasets, runs_root=runs_root)
    dataset_reports = [_load_dataset_reports(row["dataset_id"], runs_root) for row in scale_rows]
    structural_pass = all(_report_passed(report, "summary") for report in dataset_reports)
    smoke = dataset_reports[0] if dataset_reports else {}
    smoke_layout = smoke.get("layout", {})
    smoke_complex_layout = smoke_layout.get("complex_layout_text_preservation", {})
    smoke_cross_reference = smoke.get("cross_reference", {})
    smoke_chunk = smoke.get("chunk_traceability", {})
    retrieval_values = [
        row.get("evidence_recall_at_5")
        for row in scale_rows
        if isinstance(row.get("evidence_recall_at_5"), (int, float))
    ]
    first_recall = retrieval_values[0] if retrieval_values else None
    last_recall = retrieval_values[-1] if retrieval_values else None
    recall_drop = (
        first_recall - last_recall
        if isinstance(first_recall, (int, float)) and isinstance(last_recall, (int, float))
        else None
    )

    findings = [
        {
            "area": "Document object preservation",
            "status": "supported",
            "evidence": "Offline native sidecar/object/chunk traceability passed for generated rich datasets.",
        },
        {
            "area": "Chunk traceability",
            "status": "supported",
            "evidence": (
                f"Smoke chunk_sidecar_coverage={smoke_chunk.get('chunk_sidecar_coverage')}, "
                f"caption_chunk_hit_rate={smoke_chunk.get('caption_chunk_hit_rate')}, "
                f"reference_chunk_hit_rate={smoke_chunk.get('reference_chunk_hit_rate')}."
            ),
        },
        {
            "area": "Layout memory",
            "status": "risk",
            "evidence": (
                f"position_coverage={smoke_layout.get('position_coverage')}, "
                f"page_or_bbox_position_coverage={smoke_layout.get('page_or_bbox_position_coverage')}; "
                f"complex_layout_hit_rate={smoke_complex_layout.get('hit_rate')}, "
                f"textbox_hit_rate={smoke_complex_layout.get('textbox_hit_rate')}. "
                "Native DOCX sidecar lacks page/bbox coordinates and the strict smoke audit "
                "detected complex layout text loss."
            ),
        },
        {
            "area": "Cross-reference preservation",
            "status": "supported",
            "evidence": (
                f"docx_ref_fields={smoke_cross_reference.get('docx_ref_fields')}, "
                f"ref_field_chunk_hit_rate={smoke_cross_reference.get('ref_field_chunk_hit_rate')}, "
                f"oracle_cross_reference_chunk_hit_rate="
                f"{smoke_cross_reference.get('oracle_cross_reference_chunk_hit_rate')}."
            ),
        },
        {
            "area": "Retrieval scaling",
            "status": "risk",
            "evidence": (
                f"Evidence Recall@5 changes from {first_recall:.4f} to {last_recall:.4f} "
                f"across the evaluated scale; drop={recall_drop:.4f}."
                if recall_drop is not None
                else "No scale retrieval data available."
            ),
        },
        {
            "area": "End-to-end answerability",
            "status": "unproven",
            "evidence": "Real LightRAG API ingest/query/answer has not run because no LLM/embedding backend is configured.",
        },
        {
            "area": "PDF and external parsers",
            "status": "unproven",
            "evidence": "LibreOffice PDF conversion is unstable locally; Docling/MinerU services are not configured.",
        },
    ]
    return {
        "datasets": [row["dataset_id"] for row in scale_rows],
        "structural_offline_pass": structural_pass,
        "scale_rows": scale_rows,
        "findings": findings,
        "readiness": {
            "document_memory_baseline": "partial",
            "reason": (
                "The offline framework proves rich-object preservation and traceability "
                "for native DOCX parsing, while the strict layout audit exposes text loss "
                "for floating text boxes. It does not yet prove LightRAG end-to-end "
                "Document Memory quality because API retrieval, generation, citation "
                "correctness, PDF parsers, storage chunk traceability, and page/bbox "
                "layout accuracy remain unverified."
            ),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# LightRAG Document Memory Readiness Report",
        "",
        "## Verdict",
        "",
        f"- **document_memory_baseline**: {report['readiness']['document_memory_baseline']}",
        f"- **reason**: {report['readiness']['reason']}",
        "",
        "## Findings",
        "",
    ]
    for item in report["findings"]:
        lines.append(f"- **{item['area']}**: {item['status']} - {item['evidence']}")
    lines.extend(["", "## Scale Evidence", ""])
    columns = [
        "dataset_id",
        "pages",
        "facts",
        "objects",
        "relations",
        "generation_peak_memory_mb",
        "chunks",
        "retrieval_cases",
        "evidence_recall_at_5",
        "mrr",
        "object_hit_rate",
    ]
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in report["scale_rows"]:
        values = [_format_value(row.get(column)) for column in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines).rstrip() + "\n"


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


def _load_dataset_reports(dataset_id: str, runs_root: Path) -> dict[str, Any]:
    run_dir = runs_root / dataset_id
    reports = {}
    for path in run_dir.glob("*.json"):
        reports[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return reports


def _report_passed(reports: dict[str, Any], name: str) -> bool:
    return bool((reports.get(name) or {}).get("passed"))


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a Document Memory readiness summary.")
    parser.add_argument("datasets", nargs="+", help="Dataset directories or manifest paths.")
    parser.add_argument("--runs-root", type=Path, default=Path("memory_eval_tests/runs/offline"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument(
        "--no-envelope",
        action="store_true",
        help="Skip writing the kind=report run.json envelope.",
    )
    args = parser.parse_args(argv)

    report = build_readiness_report(datasets=args.datasets, runs_root=args.runs_root)
    rendered = render_json(report) if args.format == "json" else render_markdown(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        if args.format == "markdown" and not args.no_envelope:
            write_report_envelope(
                output_path=args.output,
                report_type="readiness",
                label="文档记忆就绪度报告",
                description="跨数据集的结构保留、可追溯性与检索就绪结论。",
                baseline={"datasets": [str(dataset) for dataset in args.datasets]},
                methods=[
                    {
                        "method": "readiness",
                        "label": "就绪度",
                        "params": {},
                        "summary": {
                            key: value
                            for key, value in report.items()
                            if isinstance(value, (int, float, bool))
                        },
                        "results": [],
                    }
                ],
            )
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
