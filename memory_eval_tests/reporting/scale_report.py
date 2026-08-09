from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from memory_data_service.schemas import DatasetManifest
from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.reporting.report_envelope import write_report_envelope


def build_scale_rows(
    *,
    datasets: list[str],
    runs_root: Path = Path("memory_eval_tests/runs/offline"),
) -> list[dict[str, Any]]:
    rows = []
    for dataset_source in datasets:
        manifest = DatasetManifest.model_validate(DatasetClient(dataset_source).manifest())
        run_dir = runs_root / manifest.dataset_id
        retrieval = _load_optional_json(run_dir / "retrieval_sidecar.json")
        performance = _load_optional_json(run_dir / "performance.json")
        integrity = _load_optional_json(run_dir / "integrity.json")
        object_traceability = _load_optional_json(run_dir / "object_traceability.json")
        chunk_traceability = _load_optional_json(run_dir / "chunk_traceability.json")
        rows.append(
            {
                "dataset_id": manifest.dataset_id,
                "tier": manifest.tier,
                "pages": manifest.pages,
                "generation_time_seconds": manifest.generation_time_seconds,
                "generation_peak_memory_mb": manifest.generation_peak_memory_mb,
                "facts": integrity.get("facts"),
                "questions": integrity.get("questions"),
                "objects": integrity.get("objects"),
                "relations": integrity.get("relations"),
                "dataset_size_bytes": performance.get("dataset_size_bytes"),
                "sidecar_size_bytes": performance.get("sidecar_size_bytes"),
                "parse_time_seconds": performance.get("parse_time_seconds"),
                "blocks": performance.get("blocks"),
                "tables": performance.get("tables"),
                "drawings": performance.get("drawings"),
                "equations": performance.get("equations"),
                "chunks": performance.get("chunks"),
                "chunk_sidecar_coverage": performance.get("chunk_sidecar_coverage"),
                "chunk_fact_hit_rate": chunk_traceability.get(
                    "chunk_fact_hit_rate",
                    performance.get("chunk_fact_hit_rate"),
                ),
                "object_fact_evidence_hit_rate": object_traceability.get(
                    "fact_evidence_hit_rate"
                ),
                "retrieval_cases": retrieval.get("cases"),
                "retrieval_max_cases": retrieval.get("max_cases"),
                "evidence_recall_at_5": retrieval.get("average_recall"),
                "mrr": retrieval.get("mrr"),
                "context_precision": retrieval.get("context_precision"),
                "object_hit_rate": retrieval.get("object_hit_rate"),
            }
        )
    return rows


def render_markdown(rows: list[dict[str, Any]]) -> str:
    columns = [
        "dataset_id",
        "pages",
        "facts",
        "objects",
        "relations",
        "generation_time_seconds",
        "generation_peak_memory_mb",
        "parse_time_seconds",
        "chunks",
        "retrieval_cases",
        "evidence_recall_at_5",
        "mrr",
        "object_hit_rate",
    ]
    lines = ["# LightRAG Memory Evaluation Scale Report", ""]
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

    fieldnames = sorted({key for row in rows for key in row.keys()})
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render scale summary across generated datasets.")
    parser.add_argument("datasets", nargs="+", help="Dataset directories or manifest paths.")
    parser.add_argument("--runs-root", type=Path, default=Path("memory_eval_tests/runs/offline"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", choices=("markdown", "json", "csv"), default="markdown")
    parser.add_argument(
        "--no-envelope",
        action="store_true",
        help="Skip writing the kind=report run.json envelope.",
    )
    args = parser.parse_args(argv)

    rows = build_scale_rows(datasets=args.datasets, runs_root=args.runs_root)
    if args.format == "json":
        rendered = render_json(rows)
    elif args.format == "csv":
        rendered = render_csv(rows)
    else:
        rendered = render_markdown(rows)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        if args.format == "markdown" and not args.no_envelope:
            write_report_envelope(
                output_path=args.output,
                report_type="scale",
                label="规模评测报告",
                description="跨数据集规模对比（生成成本、解析与检索基线）。",
                baseline={"datasets": [str(dataset) for dataset in args.datasets]},
                methods=[
                    {
                        "method": "scale",
                        "label": "规模评测",
                        "params": {},
                        "summary": {},
                        "results": rows,
                    }
                ],
            )
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
