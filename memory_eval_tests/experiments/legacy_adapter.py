"""Adapter that registers legacy experiment scripts into the unified harness.

The six selector/KG experiments were written before the envelope contract
existed and expose ``main()`` + async ``_run(args)`` with bespoke output paths.
This module builds an ``ExperimentSpec`` for each of them so they can be
started through ``python -m memory_eval_tests.experiments.run`` and produce
standard ``run.json`` / ``report.md`` artifacts alongside their original JSON
files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from memory_eval_tests.experiments.common import ExperimentSpec, RunContext


def _as_bool(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def namespace_from_context(
    context: RunContext,
    *,
    artifact_stem: str,
    extra_paths: dict[str, str] | None = None,
) -> argparse.Namespace:
    """Build the legacy ``argparse.Namespace`` from a harness RunContext."""
    baseline = context.baseline
    environment = context.environment
    extra = context.extra
    storage_dir = Path(
        str(
            baseline.get("storage_dir")
            or environment.get("storage_dir")
            or extra.get("storage_dir")
            or context.output_dir / "rag_storage"
        )
    )
    namespaces: dict[str, Any] = {
        "dataset": context.dataset,
        "storage_dir": storage_dir,
        "api_key": extra.get("api_key") or environment.get("api_key"),
        "access_token": extra.get("access_token") or environment.get("access_token"),
        "ollama_url": str(
            environment.get("ollama_url")
            or extra.get("ollama_url")
            or "http://127.0.0.1:11434"
        ),
        "model": str(baseline.get("model") or extra.get("model") or "qwen3:8b"),
        "num_predict": int(baseline.get("num_predict") or 256),
        "max_cases": int(extra.get("max_cases") or baseline.get("max_cases") or 0),
        "resume": _as_bool(extra.get("resume")),
        "output_json": context.output_dir / f"{artifact_stem}_results.json",
        "output_md": context.output_dir / f"{artifact_stem}_report.md",
        "diagnoses_run_id": extra.get("diagnoses_run_id"),
        "diagnoses_run_dir": extra.get("_diagnoses_run_dir"),
    }
    for key, default in (extra_paths or {}).items():
        namespaces[key] = Path(extra.get(key) or default)
    return argparse.Namespace(**namespaces)


def methods_from_legacy(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Map a legacy payload's ``methods`` list into envelope methods."""
    methods: list[dict[str, Any]] = []
    for item in payload.get("methods") or []:
        if not isinstance(item, dict):
            continue
        methods.append(
            {
                "method": item.get("method") or item.get("label") or "method",
                "label": item.get("label") or item.get("method") or "",
                "params": {
                    key: value
                    for key, value in item.items()
                    if key not in {"method", "label", "summary", "results"}
                },
                "summary": item.get("summary") or {},
                "results": item.get("results") or [],
            }
        )
    return methods


def legacy_spec(
    *,
    experiment_id: str,
    label: str,
    description: str,
    run: Callable[[argparse.Namespace], Awaitable[dict[str, Any]]],
    artifact_stem: str,
    render_report: Callable[[dict[str, Any]], str],
    methods_from: Callable[
        [dict[str, Any]], list[dict[str, Any]]
    ] = methods_from_legacy,
    default_baseline: dict[str, Any] | None = None,
    variables: list[dict[str, Any]] | None = None,
    extra_paths: dict[str, str] | None = None,
    extra_schema: dict[str, str] | None = None,
    prepare: Callable[[RunContext], None] | None = None,
    result_extra: Callable[[RunContext, dict[str, Any]], dict[str, Any]] | None = None,
) -> ExperimentSpec:
    """Create an ExperimentSpec wrapping a legacy async runner."""

    def runner(context: RunContext) -> dict[str, Any]:
        args = namespace_from_context(
            context,
            artifact_stem=artifact_stem,
            extra_paths=extra_paths,
        )
        payload = asyncio.run(run(args))
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report_md = render_report(payload)
        extra = result_extra(context, payload) if result_extra is not None else {}
        args.output_md.write_text(report_md, encoding="utf-8")
        return {
            "methods": methods_from(payload),
            "report": report_md,
            "status": "complete",
            "extra": extra,
        }

    return ExperimentSpec(
        id=experiment_id,
        label=label,
        description=description,
        runner=runner,
        default_baseline=default_baseline or {},
        variables=variables or [],
        kind="experiment",
        extra_schema=extra_schema or {},
        prepare=prepare,
    )
