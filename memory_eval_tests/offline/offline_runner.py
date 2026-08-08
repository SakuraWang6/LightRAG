from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from memory_data_service.schemas import DatasetManifest
from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.offline.chunk_traceability import audit_chunk_traceability
from memory_eval_tests.offline.cross_reference_audit import audit_cross_references
from memory_eval_tests.offline.integrity import audit_dataset_integrity
from memory_eval_tests.offline.layout_audit import audit_layout_preservation
from memory_eval_tests.offline.object_traceability import audit_object_traceability
from memory_eval_tests.offline.performance_audit import audit_performance
from memory_eval_tests.offline.sidecar_audit import audit_sidecar, run_parser_cli
from memory_eval_tests.online.retrieval_eval import evaluate_sidecar
from memory_eval_tests.reporting.report import render_markdown


def run_offline_evaluation(
    *,
    dataset_source: str,
    engine: str = "native",
    output_dir: Path = Path("memory_eval_tests/runs/offline"),
    top_k: int = 5,
    chunk_token_size: int = 800,
    force_reparse: bool = False,
    max_cases: int | None = None,
    max_facts: int | None = None,
) -> dict[str, Any]:
    client = DatasetClient(dataset_source)
    manifest = DatasetManifest.model_validate(client.manifest())
    dataset_output_dir = output_dir / manifest.dataset_id
    dataset_output_dir.mkdir(parents=True, exist_ok=True)

    input_file = _select_local_file(client, manifest, preferred_format="docx")
    parsed_parent = dataset_output_dir / "sidecar"
    parsed_dir = run_parser_cli(
        input_file,
        engine=engine,
        output_dir=parsed_parent,
        force_reparse=force_reparse,
    )

    object_traceability = audit_object_traceability(
        dataset_source,
        parsed_dir,
        max_facts=max_facts,
        chunk_token_size=chunk_token_size,
    )
    chunk_traceability = audit_chunk_traceability(
        dataset_source=dataset_source,
        parsed_dir=parsed_dir,
        chunk_token_size=chunk_token_size,
        max_facts=max_facts,
    )
    reports = {
        "integrity": audit_dataset_integrity(dataset_source),
        "sidecar": audit_sidecar(parsed_dir),
        "layout": audit_layout_preservation(
            dataset_source=dataset_source,
            parsed_dir=parsed_dir,
        ),
        "cross_reference": audit_cross_references(
            dataset_source=dataset_source,
            parsed_dir=parsed_dir,
            chunk_token_size=chunk_token_size,
        ),
        "object_traceability": object_traceability,
        "chunk_traceability": chunk_traceability,
        "retrieval_sidecar": evaluate_sidecar(
            dataset_source=dataset_source,
            parsed_dir=parsed_dir,
            mode="sidecar",
            top_k=top_k,
            max_cases=max_cases,
        ),
        "performance": audit_performance(
            dataset_source=dataset_source,
            parsed_dir=parsed_dir,
            chunk_token_size=chunk_token_size,
            max_facts=max_facts,
            chunk_report=chunk_traceability,
        ),
    }

    for name, report in reports.items():
        _write_json(dataset_output_dir / f"{name}.json", report)

    markdown_reports = []
    for name, report in reports.items():
        item = dict(report)
        item["_source"] = name
        markdown_reports.append(item)
    (dataset_output_dir / "report.md").write_text(
        render_markdown(markdown_reports),
        encoding="utf-8",
    )

    summary = {
        "dataset_id": manifest.dataset_id,
        "dataset_source": dataset_source,
        "engine": engine,
        "input_file": str(input_file),
        "parsed_dir": str(parsed_dir),
        "output_dir": str(dataset_output_dir),
        "top_k": top_k,
        "chunk_token_size": chunk_token_size,
        "max_cases": max_cases,
        "max_facts": max_facts,
        "passed": _all_required_passed(reports),
        "reports": {
            name: str(dataset_output_dir / f"{name}.json")
            for name in reports
        },
        "markdown_report": str(dataset_output_dir / "report.md"),
    }
    _write_json(dataset_output_dir / "summary.json", summary)
    return summary


def _select_local_file(
    client: DatasetClient,
    manifest: DatasetManifest,
    *,
    preferred_format: str,
) -> Path:
    for generated_file in manifest.files:
        if generated_file.format == preferred_format and generated_file.status == "created":
            return client.local_file(generated_file.name)
    raise FileNotFoundError(
        f"dataset {manifest.dataset_id} has no created {preferred_format} file"
    )


def _all_required_passed(reports: dict[str, dict[str, Any]]) -> bool:
    for name in (
        "integrity",
        "sidecar",
        "layout",
        "cross_reference",
        "object_traceability",
        "chunk_traceability",
    ):
        if not reports[name].get("passed", False):
            return False
    retrieval = reports["retrieval_sidecar"]
    return retrieval.get("cases", 0) > 0 and retrieval.get("average_recall", 0.0) > 0.0


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline LightRAG memory evaluation suite.")
    parser.add_argument("--dataset", required=True, help="Local dataset directory or manifest path.")
    parser.add_argument("--engine", default="native")
    parser.add_argument("--output-dir", type=Path, default=Path("memory_eval_tests/runs/offline"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--max-facts", type=int)
    parser.add_argument("--chunk-token-size", type=int, default=800)
    parser.add_argument("--force-reparse", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    summary = run_offline_evaluation(
        dataset_source=args.dataset,
        engine=args.engine,
        output_dir=args.output_dir,
        top_k=args.top_k,
        chunk_token_size=args.chunk_token_size,
        force_reparse=args.force_reparse,
        max_cases=args.max_cases,
        max_facts=args.max_facts,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"Dataset: {summary['dataset_id']}")
        print(f"Passed: {summary['passed']}")
        print(f"Report: {summary['markdown_report']}")
    _write_envelope(args, summary)
    return 0 if summary["passed"] else 1


def _write_envelope(args, summary: dict) -> None:
    """Write the standard run.json envelope beside the offline artifacts."""
    from memory_eval_tests.experiments.common import (
        capture_environment,
        write_simple_envelope,
    )

    output_dir = Path(summary["output_dir"])
    environment = capture_environment()
    dataset_meta = {
        "dataset": summary.get("dataset_id"),
        "pages": summary.get("pages"),
        "tier": summary.get("tier"),
        "profile": summary.get("profile"),
    }
    baseline = {
        "engine": summary.get("engine"),
        "top_k": summary.get("top_k"),
        "chunk_token_size": summary.get("chunk_token_size"),
        "max_cases": summary.get("max_cases"),
        "max_facts": summary.get("max_facts"),
    }
    methods = []
    for name, path in (summary.get("reports") or {}).items():
        audit_path = Path(path)
        if not audit_path.exists():
            continue
        try:
            payload = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        methods.append(
            {
                "method": name,
                "label": name,
                "params": {},
                "summary": {
                    key: value
                    for key, value in payload.items()
                    if isinstance(value, (int, float, bool))
                },
                "results": [],
            }
        )
    methods.append(
        {
            "method": "offline_summary",
            "label": "离线审计汇总",
            "params": {},
            "summary": {
                key: value
                for key, value in summary.items()
                if isinstance(value, (int, float, bool))
            },
            "results": [],
        }
    )
    write_simple_envelope(
        output_dir,
        kind="offline",
        run_id=output_dir.name,
        experiment={
            "id": "offline_audit",
            "label": "离线审计",
            "description": "完整性、sidecar、版式、交叉引用、对象/Chunk 可追溯性与词法检索基线（无 LLM）。",
        },
        baseline=baseline,
        environment=environment,
        methods=methods,
        status="complete" if summary.get("passed") else "failed",
        report_rel_path=Path(summary.get("markdown_report", "")).name or None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
