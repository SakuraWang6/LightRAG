"""Read-only routes for the evaluation-console tab of the WebUI.

Envelopes are read straight from ``memory_eval_tests/runs`` (see
:mod:`lightrag.api.eval_index`); ``POST /eval/refresh`` is a no-op rescan that
keeps the WebUI refresh button meaningful.
"""

from __future__ import annotations

import json
import csv
import io
import os
import re
import shutil
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
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
    from memory_eval_tests.experiments.frozen_context import freeze_final_contexts

    from .. import eval_jobs
    from .. import eval_profiles
    from .. import eval_comparison
    from ..eval_index import clear_scan_cache, default_runs_root, load_run, scan_runs

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
    supervision: Literal["auto", "none", "heartbeat"] = "auto"
    stale_minutes: int = Field(default=60, ge=1)
    max_restarts: int = 3
    poll_seconds: int = 30
    dataset_create: DatasetCreateJobRequest | None = None

    model_config = {"extra": "forbid"}


class TemplateRequest(BaseModel):
    name: str
    experiment: str
    dataset: str
    params: dict[str, Any] = Field(default_factory=dict)
    extraText: str = ""
    supervise: bool = False


class ModelRoleReference(BaseModel):
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    endpoint: str | None = Field(default=None, max_length=1024)
    secret_ref: str | None = Field(default=None, max_length=256)

    model_config = {"extra": "forbid"}


class EnvironmentProfileConfiguration(BaseModel):
    lightrag_version: str | None = Field(default=None, max_length=256)
    startup_template: str | None = Field(default=None, max_length=256)
    execution_mode: Literal["managed_local", "assigned"] = "managed_local"
    runtime_endpoint: str | None = Field(default=None, max_length=1024)
    retention_policy: Literal["retain", "archive", "cleanup"] = "retain"
    extraction: ModelRoleReference | None = None
    query: ModelRoleReference | None = None
    answer: ModelRoleReference | None = None
    embedding: ModelRoleReference
    vlm: ModelRoleReference | None = None
    reranker: ModelRoleReference | None = None
    parser_engine: str = Field(min_length=1, max_length=128)
    storage_backends: dict[str, str] = Field(default_factory=dict)
    retrieval_defaults: dict[str, int | float | bool | str] = Field(default_factory=dict)
    concurrency: dict[str, int] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class EnvironmentProfileDraftRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    profile_id: str | None = Field(default=None, max_length=64)
    configuration: EnvironmentProfileConfiguration

    model_config = {"extra": "forbid"}


class ComparisonPlanValidationRequest(BaseModel):
    comparison_type: Literal["answer_model", "retrieval_configuration", "embedding", "full_pipeline"]
    variables: dict[str, list[Any]]
    inputs: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class FreezeContextRequest(BaseModel):
    parent_run_id: str = Field(min_length=1, max_length=256)

    model_config = {"extra": "forbid"}


class CompareRunsRequest(BaseModel):
    run_ids: list[str] = Field(min_length=2, max_length=32)
    model_config = {"extra": "forbid"}


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
    if not _DATASET_ID_RE.fullmatch(dataset) or dataset in {".", ".."}:
        raise ValueError("invalid dataset id")
    extra = _extra_pairs(params.get("extra") or [])
    for key in spec.extra_schema:
        if key in params:
            extra.append(f"{key}={_coerce(params[key], spec.extra_schema[key])}")
    if (
        spec.id == "custom_arms"
        and params.get("comparison_type") == "answer_model"
    ):
        frozen_run_id = str(params.get("frozen_context_run_id") or "")
        source = load_run(runs_root, frozen_run_id)
        if source is None:
            raise ValueError("frozen_context_run_id was not found")
        if source.get("dataset") != dataset:
            raise ValueError("answer-model comparison dataset must match frozen_context_run_id")
        frozen_path = Path(source["run_dir"]) / "frozen_context.json"
        if not frozen_path.is_file():
            raise ValueError("frozen_context_run_id has no frozen_context.json artifact")
        extra.append(f"prompts={frozen_path}")
    if spec.id == "end_to_end_baseline":
        profile_id = params.get("environment_profile_id")
        raw_version = params.get("environment_profile_version")
        if not profile_id or raw_version is None:
            raise ValueError(
                "end_to_end_baseline requires environment_profile_id and environment_profile_version"
            )
        try:
            profile_version = int(raw_version)
        except (TypeError, ValueError) as exc:
            raise ValueError("environment_profile_version must be an integer") from exc
        profile = eval_profiles.get_profile_version(runs_root, str(profile_id), profile_version)
        if profile is None:
            raise ValueError("environment profile version was not found")
        if profile.get("status") != "published":
            raise ValueError("end-to-end runs may only use a published environment profile")
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

    @router.get("/runs/{run_id:path}/workspace", dependencies=[Depends(combined_auth)])
    async def get_run_workspace(run_id: str) -> dict[str, Any]:
        """Return only the execution unit persisted under the resolved run dir."""
        try:
            require_eval()
            detail = load_run(root, run_id)
            if detail is None:
                raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
            path = Path(detail["run_dir"]) / "execution_unit.json"
            try:
                unit = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise HTTPException(status_code=404, detail="execution unit not found") from None
            if not isinstance(unit, dict):
                raise HTTPException(status_code=404, detail="execution unit not found")
            return {"execution_unit": unit}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error loading eval run workspace '{run_id}': {exc}")
            raise internal_server_error(exc)

    @router.get("/runs/{run_id:path}/ingestion", dependencies=[Depends(combined_auth)])
    async def get_run_ingestion(run_id: str) -> dict[str, Any]:
        try:
            require_eval()
            detail = load_run(root, run_id)
            if detail is None:
                raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
            run_dir = Path(detail["run_dir"])
            try:
                ingestion = json.loads((run_dir / "ingestion_receipt.json").read_text(encoding="utf-8"))
                index = json.loads((run_dir / "index_receipt.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise HTTPException(status_code=404, detail="ingestion receipts not found") from None
            if not isinstance(ingestion, dict) or not isinstance(index, dict):
                raise HTTPException(status_code=404, detail="ingestion receipts not found")
            return {"ingestion_receipt": ingestion, "index_receipt": index}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error loading eval run ingestion '{run_id}': {exc}")
            raise internal_server_error(exc)

    @router.get("/runs/{run_id:path}/diagnosis", dependencies=[Depends(combined_auth)])
    async def get_run_diagnosis(run_id: str) -> dict[str, Any]:
        try:
            require_eval()
            detail = load_run(root, run_id)
            if detail is None:
                raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
            run_dir = Path(detail["run_dir"])
            try:
                traces = json.loads((run_dir / "case_trace.json").read_text(encoding="utf-8"))
                diagnosis = json.loads((run_dir / "diagnosis.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise HTTPException(status_code=404, detail="case trace and diagnosis not found") from None
            if not isinstance(traces, dict) or not isinstance(diagnosis, dict):
                raise HTTPException(status_code=404, detail="case trace and diagnosis not found")
            return {"case_trace": traces, "diagnosis": diagnosis}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error loading eval run diagnosis '{run_id}': {exc}")
            raise internal_server_error(exc)

    @router.get("/runs/{run_id:path}/oracle-upper-bounds", dependencies=[Depends(combined_auth)])
    async def list_oracle_upper_bounds(run_id: str) -> dict[str, Any]:
        """List diagnostic upper-bound runs explicitly linked to this run."""
        try:
            require_eval()
            if load_run(root, run_id) is None:
                raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
            linked = [
                item
                for item in scan_runs(root)
                if item.get("experiment") == "oracle_upper_bound"
                and item.get("diagnoses_run_id") == run_id
            ]
            return {"run_id": run_id, "oracle_upper_bounds": linked}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error loading oracle upper bounds for '{run_id}': {exc}")
            raise internal_server_error(exc)

    @router.get("/runs/{run_id:path}/diagnosis.csv", dependencies=[Depends(combined_auth)])
    async def export_run_diagnosis(run_id: str) -> Response:
        try:
            require_eval()
            detail = load_run(root, run_id)
            if detail is None:
                raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
            try:
                diagnosis = json.loads(
                    (Path(detail["run_dir"]) / "diagnosis.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                raise HTTPException(status_code=404, detail="diagnosis not found") from None
            cases = diagnosis.get("cases") if isinstance(diagnosis, dict) else None
            if not isinstance(cases, list):
                raise HTTPException(status_code=404, detail="diagnosis not found")
            output = io.StringIO()
            writer = csv.DictWriter(
                output,
                fieldnames=[
                    "question_id", "question_type", "modality", "retrieval_mode",
                    "primary_cause", "confidence", "review_required", "rule_version", "evidence",
                ],
            )
            writer.writeheader()
            for case in cases:
                if not isinstance(case, dict):
                    continue
                writer.writerow(
                    {
                        key: "; ".join(str(item) for item in case.get(key, []))
                        if key == "evidence"
                        else case.get(key, "")
                        for key in writer.fieldnames
                    }
                )
            return Response(
                content=output.getvalue(),
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{run_id}-diagnosis.csv"'},
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error exporting eval run diagnosis '{run_id}': {exc}")
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

    @router.get("/environment-profiles", dependencies=[Depends(combined_auth)])
    async def list_environment_profiles() -> dict[str, Any]:
        try:
            require_eval()
            return {"profiles": eval_profiles.list_profiles(root)}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error listing environment profiles: {exc}")
            raise internal_server_error(exc)

    @router.get("/comparison-templates", dependencies=[Depends(combined_auth)])
    async def list_comparison_templates() -> dict[str, Any]:
        try:
            require_eval()
            return {"templates": eval_comparison.list_templates()}
        except Exception as exc:
            logger.error(f"Error listing comparison templates: {exc}")
            raise internal_server_error(exc)

    @router.post("/comparison-plans/validate", dependencies=[Depends(combined_auth)])
    async def validate_comparison_plan(request: ComparisonPlanValidationRequest) -> dict[str, Any]:
        try:
            require_eval()
            try:
                return eval_comparison.validate_plan(
                    comparison_type=request.comparison_type,
                    variables=request.variables,
                    inputs=request.inputs,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error validating comparison plan: {exc}")
            raise internal_server_error(exc)

    @router.post("/frozen-contexts", dependencies=[Depends(combined_auth)])
    async def create_frozen_context(request: FreezeContextRequest) -> dict[str, Any]:
        try:
            require_eval()
            parent = load_run(root, request.parent_run_id)
            if parent is None or parent.get("experiment") != "end_to_end_baseline":
                raise HTTPException(status_code=400, detail="parent_run_id must be an end_to_end_baseline run")
            output = Path(parent["run_dir"]) / "frozen_context.json"
            try:
                frozen = freeze_final_contexts(parent_run_dir=Path(parent["run_dir"]), output_path=output)
            except (OSError, ValueError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return {
                "parent_run_id": request.parent_run_id,
                "artifact": "frozen_context.json",
                "input_hash": frozen["input_hash"],
                "case_count": len(frozen["prompts"]),
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error freezing comparison context: {exc}")
            raise internal_server_error(exc)

    @router.post("/comparisons/validate", dependencies=[Depends(combined_auth)])
    async def validate_run_comparison(request: CompareRunsRequest) -> dict[str, Any]:
        try:
            require_eval()
            runs = []
            for run_id in request.run_ids:
                detail = load_run(root, run_id)
                if detail is None:
                    raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
                runs.append(json.loads((Path(detail["run_dir"]) / "run.json").read_text(encoding="utf-8")))
            return eval_comparison.compare_contract(runs)
        except HTTPException:
            raise
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/environment-profiles", dependencies=[Depends(combined_auth)])
    async def create_environment_profile(
        request: EnvironmentProfileDraftRequest,
    ) -> dict[str, Any]:
        try:
            require_eval()
            try:
                return eval_profiles.create_draft_version(
                    runs_root=root,
                    name=request.name,
                    profile_id=request.profile_id,
                    configuration=request.configuration.model_dump(),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error creating environment profile: {exc}")
            raise internal_server_error(exc)

    @router.get(
        "/environment-profiles/{profile_id}/versions/{version}",
        dependencies=[Depends(combined_auth)],
    )
    async def get_environment_profile_version(
        profile_id: str,
        version: int,
    ) -> dict[str, Any]:
        try:
            require_eval()
            item = eval_profiles.get_profile_version(root, profile_id, version)
            if item is None:
                raise HTTPException(status_code=404, detail="environment profile version not found")
            return item
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error reading environment profile version: {exc}")
            raise internal_server_error(exc)

    @router.post(
        "/environment-profiles/{profile_id}/versions/{version}/publish",
        dependencies=[Depends(combined_auth)],
    )
    async def publish_environment_profile_version(
        profile_id: str,
        version: int,
    ) -> dict[str, Any]:
        try:
            require_eval()
            try:
                item = eval_profiles.publish_version(
                    runs_root=root, profile_id=profile_id, version=version
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if item is None:
                raise HTTPException(status_code=404, detail="environment profile version not found")
            return item
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error publishing environment profile version: {exc}")
            raise internal_server_error(exc)

    @router.post("/jobs", dependencies=[Depends(combined_auth)])
    async def create_job(request: CreateJobRequest) -> dict[str, Any]:
        try:
            require_eval()
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
                    datasets_root=datasets,
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

    @router.delete("/runs/{run_id:path}", dependencies=[Depends(combined_auth)])
    async def delete_run(run_id: str) -> dict[str, Any]:
        try:
            require_eval()
            detail = load_run(root, run_id)
            if detail is None:
                raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
            run_dir = Path(detail["run_dir"])
            matching = [
                job
                for job in eval_jobs.list_jobs(runs_root=root, datasets_root=datasets)
                if job.get("kind") == "run"
                and job.get("output_dir") == str(run_dir)
                and job.get("status") in {"claiming", "running", "cancelling", "pending"}
            ]
            for job in matching:
                canceled = eval_jobs.cancel_job(
                    runs_root=root, datasets_root=datasets, job_id=job["id"]
                )
                if (
                    job.get("status") in {"running", "cancelling"}
                    and canceled is not None
                    and not eval_jobs.wait_job_exit(canceled)
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(f"job {job['id']} is still exiting; try again shortly"),
                    )
            shutil.rmtree(run_dir)
            for job in matching:
                eval_jobs.delete_job(runs_root=root, job_id=job["id"])
            clear_scan_cache(root)
            return {"deleted": run_id}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error deleting eval run '{run_id}': {exc}")
            logger.error(traceback.format_exc())
            raise internal_server_error(exc)

    @router.get("/models", dependencies=[Depends(combined_auth)])
    async def list_models() -> dict[str, Any]:
        try:
            require_eval()
            ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
            models: list[str] = []
            embedding_filtered: list[str] = []
            try:
                request = urllib.request.Request(
                    f"{ollama_url.rstrip('/')}/api/tags",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (OSError, urllib.error.URLError, TimeoutError, ValueError):
                payload = {}
            for item in payload.get("models") or []:
                name = str(item.get("name") or "")
                if not name:
                    continue
                lowered = name.split(":")[0].lower()
                if any(
                    marker in lowered
                    for marker in (
                        "embed",
                        "bge",
                        "mxbai",
                        "nomic",
                        "e5-",
                        "gte",
                        "text-embedding",
                        "jina-embeddings",
                    )
                ):
                    embedding_filtered.append(name)
                else:
                    models.append(name)
            return {
                "models": sorted(set(models)),
                "embedding_filtered": sorted(set(embedding_filtered)),
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error listing Ollama models: {exc}")
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
                    "extraText": request.extraText,
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


def resume_pending_eval_jobs(*, delay_seconds: float = 0) -> None:
    """Restart durable evaluation-job dispatching when the API server starts."""
    if not _EVAL_AVAILABLE:
        return
    eval_jobs.resume_pending_jobs(
        runs_root=default_runs_root(),
        datasets_root=DEFAULT_GENERATED_ROOT,
        delay_seconds=delay_seconds,
    )
