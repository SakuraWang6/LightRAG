"""Read-only routes for the evaluation-console tab of the WebUI.

Envelopes are read straight from ``memory_eval_tests/runs`` (see
:mod:`lightrag.api.eval_index`); ``POST /eval/refresh`` is a no-op rescan that
keeps the WebUI refresh button meaningful.
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from lightrag.utils import logger

from ..utils_api import get_combined_auth_dependency, internal_server_error

try:
    from memory_eval_tests import __version__ as _eval_framework_version
    from memory_eval_tests.experiments.common.chat import chat_ollama

    from ..eval_index import default_runs_root, load_run, scan_runs

    _EVAL_AVAILABLE = True
except ImportError:
    _EVAL_AVAILABLE = False
    _eval_framework_version = None


def create_eval_routes(api_key: Optional[str] = None, runs_root: Optional[Path] = None) -> APIRouter:
    """Create the eval-console router.

    Args:
        api_key: Optional API key for ``combined_auth``; pass the server's key.
        runs_root: Override the runs directory (used by tests).
    """
    router = APIRouter(prefix="/eval", tags=["eval-console"])
    combined_auth = get_combined_auth_dependency(api_key)
    root = (
        Path(runs_root)
        if runs_root is not None
        else (default_runs_root() if _EVAL_AVAILABLE else None)
    )

    def require_eval() -> None:
        if not _EVAL_AVAILABLE:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Evaluation framework package is not installed; "
                    "reinstall lightrag-hku with the bundled "
                    "memory_eval_tests/memory_data_service packages."
                ),
            )

    @router.get("/status", dependencies=[Depends(combined_auth)])
    async def eval_status() -> dict[str, Any]:
        try:
            require_eval()
            return {
                "runs_root": str(root.relative_to(Path(__file__).resolve().parents[2])) if root.is_relative_to(
                    Path(__file__).resolve().parents[2]
                ) else str(root),
                "indexed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "run_count": len(scan_runs(root)),
                "eval_framework_version": _eval_framework_version,
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error reading eval status: {exc}")
            logger.error(traceback.format_exc())
            raise internal_server_error(exc)

    @router.get("/runs", dependencies=[Depends(combined_auth)])
    async def list_runs(
        kind: Optional[str] = Query(default=None, description="Filter by run kind"),
        dataset: Optional[str] = Query(default=None, description="Filter by dataset id"),
        q: Optional[str] = Query(default=None, description="Search label / dataset / artifact titles"),
        limit: int = Query(default=500, ge=1, le=10000, description="Max runs per page"),
        offset: int = Query(default=0, ge=0, description="Pagination offset"),
    ) -> dict[str, Any]:
        try:
            require_eval()
            runs = scan_runs(root)
            if kind:
                runs = [r for r in runs if r["kind"] == kind]
            if dataset:
                runs = [r for r in runs if r["dataset"] == dataset]
            if q:
                needle = q.strip().lower()
                runs = [
                    r
                    for r in runs
                    if needle
                    in " ".join(
                        [r["label"] or "", r["dataset"] or "", *r["artifact_titles"]]
                    ).lower()
                ]
            total = len(runs)
            page = runs[offset : offset + limit]
            return {
                "runs": page,
                "total": total,
                "offset": offset,
                "limit": limit,
                "runs_root": str(root),
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error listing eval runs: {exc}")
            logger.error(traceback.format_exc())
            raise internal_server_error(exc)

    @router.get("/runs/{run_id:path}", dependencies=[Depends(combined_auth)])
    async def get_run(run_id: str) -> dict[str, Any]:
        try:
            require_eval()
            detail = load_run(root, run_id)
            if detail is None:
                raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
            return detail
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error loading eval run '{run_id}': {exc}")
            logger.error(traceback.format_exc())
            raise internal_server_error(exc)

    @router.post("/refresh", dependencies=[Depends(combined_auth)])
    async def refresh_index() -> dict[str, Any]:
        try:
            require_eval()
            runs = scan_runs(root)
            return {
                "indexed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "file_count": sum(len(run.get("artifact_titles", [])) for run in runs),
                "run_count": len(runs),
            }
        except Exception as exc:
            logger.error(f"Error rebuilding eval index: {exc}")
            logger.error(traceback.format_exc())
            raise internal_server_error(exc)

    @router.post("/runs/{run_id:path}/analyze", dependencies=[Depends(combined_auth)])
    def analyze_run(
        run_id: str,
        model: str = Query(default="qwen3:8b", description="Ollama model for the analysis"),
        ollama_url: str = Query(default="http://127.0.0.1:11434"),
        force: bool = Query(default=False, description="Regenerate instead of returning the cache"),
    ) -> dict[str, Any]:
        """Ask the local LLM to produce a concise analysis of one run.

        Implemented as a sync endpoint so FastAPI runs it in the threadpool:
        the long Ollama call no longer blocks the event loop, keeping the
        WebUI polling responsive while an analysis is in flight.
        """
        try:
            require_eval()
            detail = load_run(root, run_id)
            if detail is None:
                raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
            run_dir = Path(detail["run_dir"])
            cache_path = run_dir / "analysis.json"
            if cache_path.exists() and not force:
                return json.loads(cache_path.read_text(encoding="utf-8"))

            methods = detail.get("artifacts", [])
            summary_methods = next(
                (a for a in methods if a.get("table", {}).get("rows")),
                None,
            )
            report = next(
                (a for a in methods if a.get("report_md")),
                None,
            )
            conditions = {
                c["key"]: c["value"]
                for c in detail.get("conditions", [])
                if c["key"] in {"dataset", "pages", "tier", "model", "mode", "top_k", "num_ctx", "kg", "methods"}
            }
            rows = (summary_methods or {}).get("table", {}).get("rows", [])
            snippet = []
            for row in rows[:12]:
                label = row.get("label") or row.get("method") or row.get("arm")
                picked = {
                    key: row.get(key)
                    for key in (
                        "answer_accuracy",
                        "accuracy",
                        "groundedness",
                        "ungrounded_rate",
                        "abstention_accuracy",
                        "average_recall",
                        "mrr",
                        "candidate_recall",
                        "selected_recall",
                        "selection_precision",
                        "role_coverage",
                        "retrieval_recall",
                        "mean_context_chars",
                        "mean_selected_context_chars",
                        "cases",
                    )
                    if row.get(key) is not None
                }
                snippet.append({"method": label, **picked})
            report_excerpt = (report or {}).get("report_md", "")[:2000]
            prompt = (
                f"实验：{detail.get('label')}\n"
                f"说明：{detail.get('description') or ''}\n"
                f"条件：{conditions}\n"
                f"结果：{snippet}\n"
                f"报告摘录：\n{report_excerpt}\n"
            )
            text = chat_ollama(
                host=ollama_url,
                model=model,
                system=(
                    "你是评测分析助手。用简洁的中文分析这段评测结果：先说结论，再指出方法间差异、"
                    "可能的失败模式（如检索失败/选择失败/上下文过大/拒答问题）和可执行的改进建议。"
                    "不要罗列参数，不要超过 300 字。"
                ),
                user=prompt,
                num_predict=700,
                num_ctx=8192,
                timeout=1800,
                read_timeout=600,
                retries=1,
            )
            payload = {
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "model": model,
                "text": text,
            }
            # Write atomically so a failed regeneration never destroys the
            # previous analysis.
            tmp_path = run_dir / "analysis.json.tmp"
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp_path.replace(cache_path)
            return payload
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error analyzing eval run '{run_id}': {exc}")
            logger.error(traceback.format_exc())
            raise internal_server_error(exc)

    return router
