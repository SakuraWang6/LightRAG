from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from memory_data_service.schemas import DatasetManifest
from memory_eval_tests.chunk_traceability import audit_chunk_traceability
from memory_eval_tests.dataset_client import DatasetClient
from memory_eval_tests.sidecar_audit import audit_sidecar


def audit_performance(
    *,
    dataset_source: str,
    parsed_dir: Path | None = None,
    chunk_token_size: int = 800,
    max_facts: int | None = None,
    chunk_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = DatasetClient(dataset_source)
    manifest = DatasetManifest.model_validate(client.manifest())
    dataset_dir = _local_dataset_dir(dataset_source)
    dataset_size = _directory_size(dataset_dir) if dataset_dir else 0

    report: dict[str, Any] = {
        "dataset_source": dataset_source,
        "dataset_id": manifest.dataset_id,
        "tier": manifest.tier,
        "profile": manifest.profile,
        "pages": manifest.pages,
        "generation_time_seconds": manifest.generation_time_seconds,
        "generation_peak_memory_mb": manifest.generation_peak_memory_mb,
        "generation_resource_estimate": manifest.generation_resource_estimate,
        "dataset_size_bytes": dataset_size,
        "files": [
            {
                "name": item.name,
                "format": item.format,
                "status": item.status,
                "size_bytes": item.size_bytes,
            }
            for item in manifest.files
        ],
    }
    if parsed_dir:
        report["parsed_dir"] = str(parsed_dir)
        report["sidecar_size_bytes"] = _directory_size(parsed_dir)
        report["parse_time_seconds"] = _read_parse_time(parsed_dir)
        sidecar = audit_sidecar(parsed_dir)
        report.update(
            {
                "blocks": sidecar["blocks"],
                "position_coverage": sidecar["position_coverage"],
                "tables": sidecar["modalities"]["tables"]["count"],
                "drawings": sidecar["modalities"]["drawings"]["count"],
                "equations": sidecar["modalities"]["equations"]["count"],
            }
        )
        if chunk_report is None:
            chunk_report = audit_chunk_traceability(
                dataset_source=dataset_source,
                parsed_dir=parsed_dir,
                chunk_token_size=chunk_token_size,
                max_facts=max_facts,
            )
        report.update(
            {
                "chunks": chunk_report["chunks"],
                "chunk_sidecar_coverage": chunk_report["chunk_sidecar_coverage"],
                "chunk_fact_hit_rate": chunk_report["chunk_fact_hit_rate"],
            }
        )
    return report


def _local_dataset_dir(dataset_source: str) -> Path | None:
    if dataset_source.startswith("http://") or dataset_source.startswith("https://"):
        return None
    path = Path(dataset_source)
    return path.parent if path.is_file() else path


def _directory_size(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _read_parse_time(parsed_dir: Path) -> float | None:
    path = parsed_dir / "parse_time_seconds.txt"
    if not path.exists():
        return None
    try:
        return float(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit dataset, sidecar, and chunker performance metrics.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--parsed-dir", type=Path)
    parser.add_argument("--chunk-token-size", type=int, default=800)
    parser.add_argument("--max-facts", type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = audit_performance(
        dataset_source=args.dataset,
        parsed_dir=args.parsed_dir,
        chunk_token_size=args.chunk_token_size,
        max_facts=args.max_facts,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for key, value in report.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
