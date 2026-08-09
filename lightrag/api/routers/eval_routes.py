"""Read-only routes for the evaluation-console tab of the WebUI.

Envelopes are read straight from ``memory_eval_tests/runs`` (see
:mod:`lightrag.api.eval_index`); ``POST /eval/refresh`` is a no-op rescan that
keeps the WebUI refresh button meaningful.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lightrag.utils import logger

from ..utils_api import get_combined_auth_dependency, internal_server_error

try:
    from memory_data_service.schemas import TIER_PAGE_DEFAULTS
    from memory_data_service.storage import (
        DEFAULT_GENERATED_ROOT,
        list_datasets,
        load_manifest,
    )
    from memory_eval_tests import __version__ as _eval_framework_version
    from memory_eval_tests.experiments.common.chat import chat_ollama
    from memory_eval_tests.experiments.registry import get_spec, list_specs
    from memory_eval_tests.experiments.supervise import RunParams

    from .. import eval_jobs
    from ..eval_index import default_runs_root, load_run, scan_runs

    _EVAL_AVAILABLE = True
except ImportError:
    _EVAL_AVAILABLE = False
    _eval_framework_version = None


class DatasetCreateJobRequest(BaseModel):
    dataset_id: str
    tier: str = "smoke"
    profile: str = "rich"
    pages: int | None = None
    formats: list[str] = Field(default_factory=lambda: ["docx"])
    modalities: list[str] = Field(
        default_factory=lambda: ["text", "tables", "figures", "equations"]
    )
    force: bool = False
    allow_oversized_generation: bool = False


class CreateJobRequest(BaseModel):
    kind: Literal["run", "dataset"] = "run"
    experiment: str | None = None
    dataset: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    supervise: bool = False
    supervision: str = "auto"
    stale_minutes: int = 60
    max_restarts: int = 3
    poll_seconds: int = 30
    dataset_create: DatasetCreateJobRequest | None = None


class TemplateRequest(BaseModel):
    name: str
    experiment: str
    dataset: str
    params: dict[str, Any] = Field(default_factory=dict)
    supervise: bool = False


_GENERIC_PARAM_KEYS = {
    "model",
    "mode",
    "top_k",
    "chunk_top_k",
    "num_ctx",
    "num_predict",
    "temperature",
    "max_cases",
    "kg",
    "engine",
    "extra",
}
_INFRA_PARAMS = {
    "rag_api_url",
    "ollama_url",
    "storage_dir",
    "api_key",
    "access_token",
    "runs_root",
}
_TEMPLATE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_DATASET_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _validate_dataset_id(dataset_id: str) -> str:
    """Reject traversal (``..``, slashes) before any filesystem access."""
    if not _DATASET_ID_RE.fullmatch(dataset_id) or dataset_id in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid dataset id")
    return dataset_id


def _validate_job_id(job_id: str) -> str:
    if not _JOB_ID_RE.fullmatch(job_id) or job_id in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid job id")
    return job_id


def _coerce(value: Any, value_type: str) -> Any:
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    if value_type == "bool":
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    return str(value)


def _extra_pairs(extra: list[Any]) -> list[str]:
    pairs: list[str] = []
    for item in extra:
        if not isinstance(item, str) or "=" not in item:
            raise ValueError(f"extra must be KEY=VALUE, got {item!r}")
        pairs.append(item)
    return pairs


def _build_run_params(
    *,
    experiment: str,
    dataset: str,
    params: dict[str, Any],
    runs_root: Path,
    datasets_root: Path,
) -> RunParams:
    spec = get_spec(experiment)
    unknown = set(params) - _GENERIC_PARAM_KEYS - _INFRA_PARAMS - set(spec.extra_schema)
    if unknown:
        raise ValueError(f"unknown parameters: {sorted(unknown)}")
    infra = set(params) & _INFRA_PARAMS
    if infra:
        raise ValueError(f"infrastructure parameters are not accepted: {sorted(infra)}")
    missing_env = [key for key in spec.env_required if not os.getenv(key)]
    if missing_env:
        raise ValueError(
            f"experiment requires environment variables: {', '.join(missing_env)}"
        )
    extra = _extra_pairs(params.get("extra") or [])
    for key in spec.extra_schema:
        if key in params:
            extra.append(f"{key}={_coerce(params[key], spec.extra_schema[key])}")
    dataset_dir = datasets_root / dataset
    if not (dataset_dir / "manifest.json").exists():
        raise ValueError(f"dataset not found under generated root: {dataset}")
    env = os.environ
    return RunParams(
        experiment=experiment,
        dataset=dataset_dir,
        output_dir=Path("."),
        model=params.get("model"),
        mode=params.get("mode"),
        top_k=params.get("top_k"),
        chunk_top_k=params.get("chunk_top_k"),
        num_ctx=params.get("num_ctx"),
        num_predict=params.get("num_predict"),
        temperature=params.get("temperature"),
        ollama_url=env.get("OLLAMA_URL", "http://127.0.0.1:11434"),
        rag_api_url=env.get("RAG_API_URL", "http://127.0.0.1:9621"),
        api_key=env.get("LIGHTRAG_API_KEY"),
        access_token=env.get("LIGHTRAG_ACCESS_TOKEN"),
        runs_root=runs_root,
        engine=params.get("engine"),
        max_cases=int(params.get("max_cases") or 0),
        skip_kg=not bool(params.get("kg", True)),
        extra=extra,
    )


def _templates_path(runs_root: Path) -> Path:
    return runs_root / "templates.json"


def _read_templates(runs_root: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(_templates_path(runs_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return payload if isinstance(payload, list) else []


def _write_templates(runs_root: Path, items: list[dict[str, Any]]) -> None:
    path = _templates_path(runs_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def create_eval_routes(
    api_key: Optional[str] = None,
    runs_root: Optional[Path] = None,
    datasets_root: Optional[Path] = None,
) -> APIRouter:
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
    datasets = datasets_root or DEFAULT_GENERATED_ROOT

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
                "runs_root": str(root.relative_to(Path(__file__).resolve().parents[2]))
                if root.is_relative_to(Path(__file__).resolve().parents[2])
                else str(root),
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
        dataset: Optional[str] = Query(
            default=None, description="Filter by dataset id"
        ),
        q: Optional[str] = Query(
            default=None, description="Search label / dataset / artifact titles"
        ),
        limit: int = Query(
            default=500, ge=1, le=10000, description="Max runs per page"
        ),
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

    @router.get("/runs/{run_id:path}/log", dependencies=[Depends(combined_auth)])
    async def get_run_log(
        run_id: str,
        lines: int = Query(default=200, ge=1, le=5000),
    ) -> dict[str, Any]:
        try:
            require_eval()
            detail = load_run(root, run_id)
            if detail is None:
                raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
            log_path = Path(detail["run_dir"]) / "run.log"
            if not log_path.exists():
                return {"exists": False, "lines": []}
            content = log_path.read_text(encoding="utf-8").splitlines()
            return {"exists": True, "lines": content[-lines:]}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error reading eval run log '{run_id}': {exc}")
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
            runs = scan_runs(root, force=True)
            return {
                "indexed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "file_count": sum(len(run.get("artifact_titles", [])) for run in runs),
                "run_count": len(runs),
            }
        except Exception as exc:
            logger.error(f"Error rebuilding eval index: {exc}")
            logger.error(traceback.format_exc())
            raise internal_server_error(exc)

    @router.get("/experiments", dependencies=[Depends(combined_auth)])
    async def list_experiments() -> dict[str, Any]:
        try:
            require_eval()
            items = []
            for spec in list_specs():
                items.append(
                    {
                        "id": spec.id,
                        "label": spec.label,
                        "description": spec.description,
                        "supervision": spec.supervision,
                        "supports_resume": spec.supports_resume,
                        "default_baseline": spec.default_baseline,
                        "variables": spec.variables,
                        "extra_schema": spec.extra_schema,
                        "env_required": spec.env_required,
                        "env_ready": all(os.getenv(key) for key in spec.env_required),
                    }
                )
            return {"experiments": items}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error listing experiments: {exc}")
            raise internal_server_error(exc)

    @router.post("/jobs", dependencies=[Depends(combined_auth)])
    async def create_job(request: CreateJobRequest) -> dict[str, Any]:
        try:
            require_eval()
            max_active_raw = os.getenv("MEMORY_EVAL_MAX_ACTIVE_JOBS")
            if max_active_raw and max_active_raw.strip().isdigit():
                max_active = int(max_active_raw)
                active = [
                    job
                    for job in eval_jobs.list_jobs(
                        runs_root=root, datasets_root=datasets
                    )
                    if job.get("status") == "running"
                ]
                if len(active) >= max_active:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"active job limit reached ({len(active)} >= "
                            f"{max_active}); cancel a running job or raise "
                            "MEMORY_EVAL_MAX_ACTIVE_JOBS"
                        ),
                    )
            if request.kind == "run":
                if not request.experiment or not request.dataset:
                    raise HTTPException(
                        status_code=400,
                        detail="experiment and dataset are required for run jobs",
                    )
                try:
                    params = _build_run_params(
                        experiment=request.experiment,
                        dataset=request.dataset,
                        params=request.params,
                        runs_root=root,
                        datasets_root=datasets,
                    )
                except (ValueError, KeyError) as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                job = eval_jobs.start_run_job(
                    runs_root=root,
                    params=params,
                    supervise=request.supervise,
                    supervision=request.supervision,
                    stale_minutes=request.stale_minutes,
                    max_restarts=request.max_restarts,
                    poll_seconds=request.poll_seconds,
                )
                return job
            if request.dataset_create is None:
                raise HTTPException(
                    status_code=400,
                    detail="dataset_create is required for dataset jobs",
                )
            create = request.dataset_create
            pages = create.pages or TIER_PAGE_DEFAULTS.get(create.tier, 12)
            try:
                job = eval_jobs.start_dataset_job(
                    runs_root=root,
                    datasets_root=datasets,
                    dataset_id=create.dataset_id,
                    tier=create.tier,
                    profile=create.profile,
                    pages=pages,
                    formats=create.formats,
                    modalities=create.modalities,
                    force=create.force,
                    allow_oversized_generation=create.allow_oversized_generation,
                )
            except (ValueError, KeyError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return job
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error creating eval job: {exc}")
            raise internal_server_error(exc)

    @router.get("/jobs", dependencies=[Depends(combined_auth)])
    async def list_jobs() -> dict[str, Any]:
        try:
            require_eval()
            return {"jobs": eval_jobs.list_jobs(runs_root=root, datasets_root=datasets)}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error listing eval jobs: {exc}")
            raise internal_server_error(exc)

    @router.get("/jobs/{job_id}", dependencies=[Depends(combined_auth)])
    async def get_job(job_id: str) -> dict[str, Any]:
        try:
            require_eval()
            _validate_job_id(job_id)
            job = eval_jobs.get_job(
                runs_root=root, datasets_root=datasets, job_id=job_id
            )
            if job is None:
                raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
            job["log"] = eval_jobs.job_log_tail(job)
            return job
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error loading eval job '{job_id}': {exc}")
            raise internal_server_error(exc)

    @router.post("/jobs/{job_id}/cancel", dependencies=[Depends(combined_auth)])
    async def cancel_job(job_id: str) -> dict[str, Any]:
        try:
            require_eval()
            _validate_job_id(job_id)
            job = eval_jobs.cancel_job(
                runs_root=root, datasets_root=datasets, job_id=job_id
            )
            if job is None:
                raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
            return job
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error canceling eval job '{job_id}': {exc}")
            raise internal_server_error(exc)

    @router.get("/datasets", dependencies=[Depends(combined_auth)])
    async def list_datasets_route(
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        try:
            require_eval()
            all_datasets = list_datasets(datasets)
            page = all_datasets[offset : offset + limit]
            return {
                "datasets": [item.model_dump() for item in page],
                "total": len(all_datasets),
                "offset": offset,
                "limit": limit,
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error listing datasets: {exc}")
            raise internal_server_error(exc)

    @router.get("/datasets/{dataset_id}", dependencies=[Depends(combined_auth)])
    async def get_dataset(dataset_id: str) -> dict[str, Any]:
        try:
            require_eval()
            _validate_dataset_id(dataset_id)
            path = datasets / dataset_id
            if not path.is_dir() or not (path / "manifest.json").exists():
                raise HTTPException(status_code=404, detail="dataset not found")
            return load_manifest(path).model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error loading dataset '{dataset_id}': {exc}")
            raise internal_server_error(exc)

    @router.delete("/datasets/{dataset_id}", dependencies=[Depends(combined_auth)])
    async def delete_dataset(dataset_id: str) -> dict[str, Any]:
        try:
            require_eval()
            _validate_dataset_id(dataset_id)
            root_resolved = datasets.resolve()
            path = datasets / dataset_id
            path_resolved = path.resolve()
            if (
                path_resolved != root_resolved
                and root_resolved not in path_resolved.parents
            ):
                raise HTTPException(status_code=400, detail="invalid dataset id")
            if not path.is_dir():
                raise HTTPException(status_code=404, detail="dataset not found")
            active = eval_jobs.active_dataset_job(
                runs_root=root, datasets_root=datasets, dataset_id=dataset_id
            )
            if active is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"dataset is being generated by job {active['id']}; cancel it first",
                )
            shutil.rmtree(path)
            return {"deleted": dataset_id}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error deleting dataset '{dataset_id}': {exc}")
            raise internal_server_error(exc)

    @router.get("/templates", dependencies=[Depends(combined_auth)])
    async def list_templates() -> dict[str, Any]:
        try:
            require_eval()
            return {"templates": _read_templates(root)}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error listing templates: {exc}")
            raise internal_server_error(exc)

    @router.post("/templates", dependencies=[Depends(combined_auth)])
    async def save_template(request: TemplateRequest) -> dict[str, Any]:
        try:
            require_eval()
            if not _TEMPLATE_NAME_RE.fullmatch(request.name):
                raise HTTPException(
                    status_code=400,
                    detail="template name must match [A-Za-z0-9_.-]{1,64}",
                )
            items = _read_templates(root)
            items = [item for item in items if item.get("name") != request.name]
            items.append(
                {
                    "name": request.name,
                    "experiment": request.experiment,
                    "dataset": request.dataset,
                    "params": request.params,
                    "supervise": request.supervise,
                }
            )
            _write_templates(root, items)
            return {"saved": request.name}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error saving template: {exc}")
            raise internal_server_error(exc)

    @router.delete("/templates", dependencies=[Depends(combined_auth)])
    async def delete_template(name: str = Query(...)) -> dict[str, Any]:
        try:
            require_eval()
            items = _read_templates(root)
            remaining = [item for item in items if item.get("name") != name]
            if len(remaining) == len(items):
                raise HTTPException(
                    status_code=404, detail=f"Template not found: {name}"
                )
            _write_templates(root, remaining)
            return {"deleted": name}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error deleting template: {exc}")
            raise internal_server_error(exc)

    @router.post("/runs/{run_id:path}/analyze", dependencies=[Depends(combined_auth)])
    def analyze_run(
        run_id: str,
        force: bool = Query(
            default=False, description="Regenerate instead of returning the cache"
        ),
    ) -> dict[str, Any]:
        """Ask the local LLM to produce a concise analysis of one run.

        Implemented as a sync endpoint so FastAPI runs it in the threadpool:
        the long Ollama call no longer blocks the event loop, keeping the
        WebUI polling responsive while an analysis is in flight.  The Ollama
        endpoint and model are fixed to server-side configuration
        (``OLLAMA_URL`` / ``OLLAMA_MODEL``) instead of trusting client-supplied
        values.
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
                if c["key"]
                in {
                    "dataset",
                    "pages",
                    "tier",
                    "model",
                    "mode",
                    "top_k",
                    "num_ctx",
                    "kg",
                    "methods",
                }
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
                host=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
                model=os.getenv("OLLAMA_MODEL", "qwen3:8b"),
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
                "model": os.getenv("OLLAMA_MODEL", "qwen3:8b"),
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
