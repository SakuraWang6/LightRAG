"""Read-only routes for the evaluation-console tab of the WebUI.

Envelopes are read straight from ``memory_eval_tests/runs`` (see
:mod:`lightrag.api.eval_index`); ``POST /eval/refresh`` is a no-op rescan that
keeps the WebUI refresh button meaningful.
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from lightrag.utils import logger
from ..eval_index import default_runs_root, load_run, scan_runs
from ..utils_api import get_combined_auth_dependency, internal_server_error


def create_eval_routes(api_key: Optional[str] = None, runs_root: Optional[Path] = None) -> APIRouter:
    """Create the eval-console router.

    Args:
        api_key: Optional API key for ``combined_auth``; pass the server's key.
        runs_root: Override the runs directory (used by tests).
    """
    router = APIRouter(prefix="/eval", tags=["eval-console"])
    combined_auth = get_combined_auth_dependency(api_key)
    root = Path(runs_root) if runs_root is not None else default_runs_root()

    @router.get("/status", dependencies=[Depends(combined_auth)])
    async def eval_status() -> dict[str, Any]:
        try:
            return {
                "runs_root": str(root.relative_to(Path(__file__).resolve().parents[2])) if root.is_relative_to(
                    Path(__file__).resolve().parents[2]
                ) else str(root),
                "indexed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "run_count": len(scan_runs(root)),
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
    ) -> dict[str, Any]:
        try:
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
            return {"runs": runs, "runs_root": str(root)}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error listing eval runs: {exc}")
            logger.error(traceback.format_exc())
            raise internal_server_error(exc)

    @router.get("/runs/{run_id:path}", dependencies=[Depends(combined_auth)])
    async def get_run(run_id: str) -> dict[str, Any]:
        try:
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

    return router
