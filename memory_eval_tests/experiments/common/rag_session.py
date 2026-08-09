"""Shared LightRAG session helpers for KG-based experiments.

These helpers used to live in the legacy ``kg_ablation`` script and were
imported by several registered experiments via private names.  They now live
here so both the legacy runners and the registered harness share one
implementation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from lightrag import LightRAG, QueryParam

DEFAULT_STORAGE = Path(
    "memory_eval_tests/runs/online/rich-smoke-v1-local-qwen8b-kg-timeout900/rag_storage"
)


def find_rag() -> LightRAG:
    """Build the configured app once and retrieve its already-opened RAG object."""
    # ``lightrag.api.config`` parses command-line settings at import time.  Keep
    # the experiment flags out of that parser, then restore argv.
    saved_argv = sys.argv
    sys.argv = [sys.argv[0]]
    try:
        from lightrag.api.lightrag_server import get_application
    finally:
        sys.argv = saved_argv
    app = get_application()
    for included_router in app.routes:
        router = getattr(included_router, "original_router", None)
        for route in getattr(router, "routes", []):
            if route.path != "/query":
                continue
            for cell in route.endpoint.__closure__ or ():
                if isinstance(cell.cell_contents, LightRAG):
                    return cell.cell_contents
    raise RuntimeError(
        "Unable to locate the LightRAG instance from the configured API app"
    )


def load_keyword_cache(storage_dir: Path) -> dict[str, tuple[list[str], list[str]]]:
    cache_path = storage_dir / "kv_store_llm_response_cache.json"
    if not cache_path.exists():
        raise FileNotFoundError(
            f"missing keyword cache at {cache_path}; the selected storage_dir must "
            "contain a prepared LightRAG index. Pass --storage-dir explicitly or "
            "run against an existing output_dir/rag_storage."
        )
    rows = json.loads(cache_path.read_text(encoding="utf-8"))
    result: dict[str, tuple[list[str], list[str]]] = {}
    for row in rows.values():
        if row.get("cache_type") != "keywords":
            continue
        question = str(row.get("original_prompt") or "").strip()
        try:
            payload = json.loads(str(row.get("return") or "{}"))
        except json.JSONDecodeError:
            continue
        high = payload.get("high_level_keywords") or []
        low = payload.get("low_level_keywords") or []
        if question and isinstance(high, list) and isinstance(low, list):
            result[question] = (
                [str(item) for item in high],
                [str(item) for item in low],
            )
    return result


def query_param(
    *,
    top_k: int,
    high_keywords: list[str],
    low_keywords: list[str],
    prompt_only: bool = False,
    context_only: bool = False,
) -> QueryParam:
    return QueryParam(
        mode="mix",
        top_k=top_k,
        chunk_top_k=top_k,
        max_total_tokens=8192,
        hl_keywords=high_keywords,
        ll_keywords=low_keywords,
        only_need_prompt=prompt_only,
        only_need_context=context_only,
        response_type="Multiple Paragraphs",
    )
