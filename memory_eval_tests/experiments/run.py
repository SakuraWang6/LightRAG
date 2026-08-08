"""Unified experiment CLI: ``python -m memory_eval_tests.experiments.run``.

Every experiment shares the same conditions, envelope writer and progress
supervision; each registered spec only implements its runner and defaults.
"""

from __future__ import annotations

import argparse
import traceback
from pathlib import Path

from memory_eval_tests.experiments.common import (
    BASELINE_DEFAULTS,
    RunContext,
    capture_environment,
    write_envelope,
    write_progress,
)
from memory_eval_tests.experiments.registry import get_spec


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Standardized evaluation harness")
    parser.add_argument("--experiment", required=True, help="Registered experiment id")
    parser.add_argument("--dataset", type=Path, required=True, help="Dataset directory under memory_data_service/generated")
    parser.add_argument("--output-dir", type=Path, required=True, help="Run directory (run.json + report.md + progress.json are written here)")
    parser.add_argument("--run-id", default=None, help="Optional run id (default: output-dir name)")
    parser.add_argument("--model", default=None)
    parser.add_argument("--mode", default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--chunk-top-k", type=int, default=None)
    parser.add_argument("--num-ctx", type=int, default=None)
    parser.add_argument("--num-predict", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--rag-api-url", default="http://127.0.0.1:9621")
    parser.add_argument("--storage-dir", type=Path, default=None)
    parser.add_argument("--engine", default=None)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--skip-kg", action="store_true", help="Disable KG extraction (isolated storage required)")
    parser.add_argument("--extra", action="append", default=[], metavar="KEY=VALUE", help="Experiment-specific option")

    args = parser.parse_args()
    spec = get_spec(args.experiment)
    baseline = dict(spec.default_baseline)
    baseline.update({k: v for k, v in BASELINE_DEFAULTS.items() if k not in baseline})
    for key, value in (
        ("model", args.model),
        ("mode", args.mode),
        ("top_k", args.top_k),
        ("chunk_top_k", args.chunk_top_k),
        ("num_ctx", args.num_ctx),
        ("num_predict", args.num_predict),
        ("temperature", args.temperature),
        ("engine", args.engine),
    ):
        if value is not None:
            baseline[key] = value
    if args.skip_kg:
        baseline["kg"] = False
    baseline["max_cases"] = args.max_cases

    extra: dict[str, str] = {}
    for item in args.extra:
        key, _, value = item.partition("=")
        extra[key.strip()] = value.strip()

    storage_dir = args.storage_dir or (args.output_dir / "rag_storage")
    environment = capture_environment(
        rag_api_url=args.rag_api_url,
        ollama_url=args.ollama_url,
        storage_dir=str(storage_dir),
    )
    run_id = args.run_id or args.output_dir.name
    context = RunContext(
        spec=spec,
        dataset=args.dataset,
        output_dir=args.output_dir,
        baseline=baseline,
        environment=environment,
        variables=spec.variables,
        run_id=run_id,
        extra=extra,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_progress(args.output_dir, status="queued", done=0, total=1, phase="starting")
    try:
        payload = spec.runner(context)
        methods = payload.get("methods", [])
        report_md = payload.get("report", "")
        report_path = args.output_dir / "report.md"
        report_path.write_text(report_md, encoding="utf-8")
        status = payload.get("status", "complete")
        write_envelope(
            args.output_dir,
            context=context,
            status=status,
            methods=methods,
            report_rel_path=report_path.name,
            extra=payload.get("extra"),
        )
    except Exception as exc:  # keep the failure visible for the console monitor
        write_progress(
            args.output_dir,
            status="failed",
            done=0,
            total=1,
            phase="failed",
            message=f"{type(exc).__name__}: {exc}",
        )
        print(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
