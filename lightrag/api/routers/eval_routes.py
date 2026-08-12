"""Read-only routes for the evaluation-console tab of the WebUI.

Envelopes are read straight from ``memory_eval_tests/runs`` (see
:mod:`lightrag.api.eval_index`); ``POST /eval/refresh`` is a no-op rescan that
keeps the WebUI refresh button meaningful.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import shutil
import tempfile
import traceback
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from lightrag.utils import logger

from ..utils_api import get_combined_auth_dependency, internal_server_error

try:
    from memory_data_service.schemas import TIER_PAGE_DEFAULTS
    from memory_data_service.storage import (
        DEFAULT_GENERATED_ROOT,
        list_datasets,
        load_manifest,
        load_oracle,
    )
    from memory_eval_tests import __version__ as _eval_framework_version
    from memory_eval_tests.runner import RunParams

    from .. import eval_comparison, eval_jobs
    from ..eval_index import (
        case_final_context_trace,
        clear_scan_cache,
        default_runs_root,
        load_run,
        scan_runs,
    )

    _EVAL_AVAILABLE = True
except ImportError:
    _EVAL_AVAILABLE = False
    _eval_framework_version = None


class DatasetCreateJobRequest(BaseModel):
    dataset_id: str | None = None
    # ``title`` is kept as a short-lived compatibility alias for WebUI bundles
    # that may still be cached in a browser during an API upgrade.  New clients
    # must use ``display_name``: it is the user-facing dataset name, rather than
    # a source document title.
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    tier: str = "smoke"
    profile: str = "rich"
    language: Literal["en", "zh"] = "en"
    pages: int | None = None
    formats: list[str] = Field(default_factory=lambda: ["docx"])
    modalities: list[str] = Field(
        default_factory=lambda: ["text", "tables", "figures", "equations"]
    )
    force: bool = False
    allow_oversized_generation: bool = False


class CreateJobRequest(BaseModel):
    kind: Literal["run", "dataset"] = "run"
    dataset: str | None = None
    name: str | None = Field(default=None, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)
    dataset_create: DatasetCreateJobRequest | None = None

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
    "max_total_tokens",
    "temperature",
    "max_cases",
    "question_types",
    "kg",
    "vlm",
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
_DATASET_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MAX_DATASET_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_DATASET_ARCHIVE_FILES = 1_000
_MAX_DATASET_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024


def _validate_dataset_id(dataset_id: str) -> str:
    """Reject traversal (``..``, slashes) before any filesystem access."""
    if not _DATASET_ID_RE.fullmatch(dataset_id) or dataset_id in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid dataset id")
    return dataset_id


def _validate_job_id(job_id: str) -> str:
    if not _JOB_ID_RE.fullmatch(job_id) or job_id in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid job id")
    return job_id


def _safe_archive_parts(name: str) -> tuple[str, ...]:
    """Return a relative archive path or reject traversal and link-like names."""
    normalized = name.replace("\\", "/")
    parts = tuple(part for part in normalized.split("/") if part not in {"", "."})
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"unsafe archive member: {name!r}")
    if normalized.startswith("/") or ":" in parts[0]:
        raise ValueError(f"unsafe archive member: {name!r}")
    return parts


def _safe_dataset_file_path(dataset_dir: Path, name: str) -> Path:
    parts = _safe_archive_parts(name)
    path = dataset_dir.joinpath(*parts)
    if dataset_dir.resolve() not in path.resolve().parents:
        raise ValueError(f"unsafe manifest file name: {name!r}")
    return path


def _import_dataset_archive(*, archive: UploadFile, datasets_root: Path) -> dict[str, Any]:
    """Import one generated-scenario zip after validating its executable contract.

    A scenario is portable only when its manifest, oracle, and every created
    document travel together.  Paths recorded by the generator are rewritten
    after import, because they are machine-local implementation details.
    """
    filename = archive.filename or ""
    if not filename.lower().endswith(".zip"):
        raise ValueError("only a .zip generated scenario package can be imported")
    with tempfile.TemporaryDirectory(prefix="lightrag-eval-import-") as temp_dir:
        archive_path = Path(temp_dir) / "scenario.zip"
        total = 0
        with archive_path.open("wb") as target:
            while chunk := archive.file.read(1024 * 1024):
                total += len(chunk)
                if total > _MAX_DATASET_ARCHIVE_BYTES:
                    raise ValueError("scenario package exceeds the 512 MiB import limit")
                target.write(chunk)
        if not zipfile.is_zipfile(archive_path):
            raise ValueError("uploaded file is not a valid zip archive")

        staging = Path(temp_dir) / "unpacked"
        staging.mkdir()
        unpacked_bytes = 0
        with zipfile.ZipFile(archive_path) as source:
            entries = [entry for entry in source.infolist() if not entry.is_dir()]
            if len(entries) > _MAX_DATASET_ARCHIVE_FILES:
                raise ValueError("scenario package contains too many files")
            for entry in entries:
                parts = _safe_archive_parts(entry.filename)
                # Unix symlinks can escape the staging directory after extraction.
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"scenario package contains a symbolic link: {entry.filename!r}")
                unpacked_bytes += entry.file_size
                if unpacked_bytes > _MAX_DATASET_UNPACKED_BYTES:
                    raise ValueError("scenario package exceeds the 2 GiB unpacked limit")
                output = staging.joinpath(*parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as input_file, output.open("wb") as output_file:
                    shutil.copyfileobj(input_file, output_file)

        manifests = list(staging.rglob("manifest.json"))
        if len(manifests) != 1:
            raise ValueError("scenario package must contain exactly one manifest.json")
        source_dir = manifests[0].parent
        manifest = load_manifest(source_dir)
        dataset_id = _validate_dataset_id(manifest.dataset_id)
        oracle_path = _safe_dataset_file_path(source_dir, manifest.oracle_file)
        if not oracle_path.is_file():
            raise ValueError(f"scenario package is missing oracle file: {manifest.oracle_file}")
        oracle = load_oracle(source_dir)
        if oracle.dataset_id != dataset_id:
            raise ValueError("oracle dataset_id does not match manifest dataset_id")
        for item in manifest.files:
            if item.status != "created":
                continue
            document_path = _safe_dataset_file_path(source_dir, item.name)
            if not document_path.is_file():
                raise ValueError(f"scenario package is missing created file: {item.name}")

        destination = datasets_root / dataset_id
        if destination.exists():
            raise ValueError(f"dataset already exists: {dataset_id}")
        datasets_root.mkdir(parents=True, exist_ok=True)
        destination.parent.resolve()
        shutil.move(str(source_dir), str(destination))
        try:
            payload = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
            for item in payload.get("files") or []:
                if item.get("status") == "created":
                    item["path"] = str(_safe_dataset_file_path(destination, str(item.get("name") or "")))
            (destination / "manifest.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except Exception:
            # Keep a rejected package from looking importable on a retry.
            shutil.rmtree(destination, ignore_errors=True)
            raise
        return load_manifest(destination).model_dump()


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


def _positive_int_param(params: dict[str, Any], key: str) -> int | None:
    """Read an optional positive integer without silently accepting booleans."""
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if parsed < 1:
        raise ValueError(f"{key} must be at least 1")
    return parsed


def _temperature_param(params: dict[str, Any]) -> float | None:
    """Validate a provider-neutral sampling temperature before queuing work."""
    value = params.get("temperature")
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("temperature must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("temperature must be a number") from exc
    if not math.isfinite(parsed) or not 0 <= parsed <= 2:
        raise ValueError("temperature must be between 0 and 2")
    return parsed


def _run_label(value: str | None) -> str | None:
    if value is None:
        return None
    label = value.strip()
    if not label:
        raise ValueError("evaluation name must not be empty")
    if any(ord(char) < 32 for char in label):
        raise ValueError("evaluation name contains unsupported control characters")
    return label


def _available_parser_engines() -> list[str]:
    """Return only parser engines this server can actually start."""
    try:
        from lightrag.parser.registry import (
            engine_endpoint_configured,
            supported_parser_engines,
        )

        return sorted(
            engine
            for engine in supported_parser_engines()
            if engine_endpoint_configured(engine)
        )
    except Exception:
        # Keep the launch form usable during a partial parser installation.
        return ["native"]


def _configured_query_model() -> str:
    return (
        os.getenv("QUERY_LLM_MODEL")
        or os.getenv("LLM_MODEL")
        or os.getenv("OLLAMA_MODEL")
        or "qwen3:8b"
    )


def _configured_query_provider() -> str:
    return (
        os.getenv("QUERY_LLM_BINDING")
        or os.getenv("LLM_BINDING")
        or "ollama"
    ).strip().lower()


def _configured_ollama_url() -> str:
    return (
        os.getenv("QUERY_LLM_BINDING_HOST")
        or os.getenv("LLM_BINDING_HOST")
        or os.getenv("OLLAMA_URL")
        or "http://127.0.0.1:11434"
    )


def _is_embedding_model(name: str) -> bool:
    lowered = name.split(":")[0].lower()
    return any(
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
    )


def _evaluation_model_capability() -> dict[str, Any]:
    """Describe only models this deployment can actually run for evaluation."""
    provider = _configured_query_provider()
    default_model = _configured_query_model()
    parser_engines = _available_parser_engines()
    result: dict[str, Any] = {
        "provider": provider,
        "default_model": default_model,
        "parser_engines": parser_engines,
        "default_parser_engine": "native"
        if "native" in parser_engines
        else (parser_engines[0] if parser_engines else None),
        "models": [],
        "embedding_filtered": [],
        "selectable_models": [],
        "model_selection": "fixed",
        "configuration_error": None,
    }
    if provider != "ollama":
        # Remote providers do not offer a safe generic model-discovery API.
        # The configured model is still selectable in the form, but cannot be
        # silently replaced with an arbitrary client-supplied identifier.
        result.update(
            {
                "models": [default_model],
                "selectable_models": [default_model],
                "model_selection": "fixed",
            }
        )
        return result

    try:
        request = urllib.request.Request(
            f"{_configured_ollama_url().rstrip('/')}/api/tags",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        payload = {}
    installed = sorted(
        {
            str(item.get("name") or "")
            for item in payload.get("models") or []
            if isinstance(item, dict) and str(item.get("name") or "")
        }
    )
    models = [name for name in installed if not _is_embedding_model(name)]
    embedding_filtered = [name for name in installed if _is_embedding_model(name)]
    result.update(
        {
            "models": models,
            "embedding_filtered": embedding_filtered,
            "selectable_models": models,
            "model_selection": "selectable",
        }
    )
    if default_model not in models:
        result["configuration_error"] = (
            f"服务器配置的回答模型 {default_model!r} 未安装或不可用"
        )
    return result


def _build_run_params(
    *,
    dataset: str,
    name: str | None,
    params: dict[str, Any],
    runs_root: Path,
    datasets_root: Path,
) -> RunParams:
    allowed_extra = {"allow_partial_ingestion", "ingestion_success_threshold"}
    unknown = set(params) - _GENERIC_PARAM_KEYS - _INFRA_PARAMS - allowed_extra
    if unknown:
        raise ValueError(f"unknown parameters: {sorted(unknown)}")
    infra = set(params) & _INFRA_PARAMS
    if infra:
        raise ValueError(f"infrastructure parameters are not accepted: {sorted(infra)}")
    if not _DATASET_ID_RE.fullmatch(dataset) or dataset in {".", ".."}:
        raise ValueError("invalid dataset id")
    extra = _extra_pairs(params.get("extra") or [])
    for key, value_type in {
        "allow_partial_ingestion": "bool",
        "ingestion_success_threshold": "float",
    }.items():
        if key in params:
            extra.append(f"{key}={_coerce(params[key], value_type)}")
    dataset_dir = datasets_root / dataset
    if not (dataset_dir / "manifest.json").exists():
        raise ValueError(f"dataset not found under generated root: {dataset}")
    engine = params.get("engine")
    if engine is not None:
        available_engines = _available_parser_engines()
        if engine not in available_engines:
            raise ValueError(
                f"engine must be one of the configured parser engines: {available_engines}"
            )
    kg_enabled = bool(params.get("kg", True))
    vlm = params.get("vlm")
    if vlm is not None and not isinstance(vlm, bool):
        raise ValueError("vlm must be a boolean")
    mode = params.get("mode")
    if not kg_enabled:
        # With entity/relation extraction skipped, graph-aware modes have no
        # graph to query.  Keep the resulting run a clear vector-only baseline.
        if mode is None:
            mode = "naive"
        elif mode != "naive":
            raise ValueError("mode must be naive when KG extraction is disabled")
    env = os.environ
    max_cases = params.get("max_cases", 0)
    if isinstance(max_cases, bool):
        raise ValueError("max_cases must be an integer")
    try:
        max_cases = int(max_cases)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_cases must be an integer") from exc
    if max_cases < 0:
        raise ValueError("max_cases must be 0 or greater")
    top_k = _positive_int_param(params, "top_k")
    chunk_top_k = _positive_int_param(params, "chunk_top_k")
    num_ctx = _positive_int_param(params, "num_ctx")
    num_predict = _positive_int_param(params, "num_predict")
    max_total_tokens = _positive_int_param(params, "max_total_tokens")
    temperature = _temperature_param(params)
    model_capability = _evaluation_model_capability()
    capability_error = model_capability.get("configuration_error")
    if capability_error:
        raise ValueError(str(capability_error))
    model = params.get("model") or model_capability.get("default_model")
    selectable_models = model_capability.get("selectable_models") or []
    if not isinstance(model, str) or model not in selectable_models:
        raise ValueError(
            f"model must be one of the server-available models: {selectable_models}"
        )
    return RunParams(
        dataset=dataset_dir,
        output_dir=Path("."),
        label=_run_label(name),
        model=model,
        mode=mode,
        top_k=top_k,
        chunk_top_k=chunk_top_k,
        num_ctx=num_ctx,
        num_predict=num_predict,
        max_total_tokens=max_total_tokens,
        temperature=temperature,
        api_key=env.get("LIGHTRAG_API_KEY"),
        access_token=env.get("LIGHTRAG_ACCESS_TOKEN"),
        runs_root=runs_root,
        engine=engine,
        max_cases=max_cases,
        question_types=(
            [str(item) for item in params["question_types"]]
            if isinstance(params.get("question_types"), list)
            else None
        ),
        skip_kg=not kg_enabled,
        vlm=vlm,
        extra=extra,
    )


def create_eval_routes(
    api_key: str | None = None,
    runs_root: Path | None = None,
    datasets_root: Path | None = None,
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
        dataset: str | None = Query(
            default=None, description="Filter by dataset id"
        ),
        q: str | None = Query(
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
            run_dir = Path(detail["run_dir"])
            log_paths = [run_dir / "run.log", run_dir / "execution_unit.log"]
            content: list[str] = []
            for log_path in log_paths:
                if not log_path.exists():
                    continue
                if content:
                    content.append(f"--- {log_path.name} ---")
                content.extend(log_path.read_text(encoding="utf-8").splitlines())
            if not content:
                return {"exists": False, "lines": []}
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

    @router.get(
        "/runs/{run_id:path}/cases/{case_id}/context",
        dependencies=[Depends(combined_auth)],
    )
    async def get_case_context(run_id: str, case_id: str) -> dict[str, Any]:
        try:
            require_eval()
            detail = load_run(root, run_id)
            if detail is None:
                raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
            trace = case_final_context_trace(root, run_id, case_id)
            if trace is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"No final-context trace for case {case_id}",
                )
            return {"final_context_trace": trace}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error loading case context '{run_id}/{case_id}': {exc}")
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

    @router.post("/jobs", dependencies=[Depends(combined_auth)])
    async def create_job(request: CreateJobRequest) -> dict[str, Any]:
        try:
            require_eval()
            if request.kind == "run":
                if not request.dataset:
                    raise HTTPException(
                        status_code=400,
                        detail="dataset is required for evaluation jobs",
                    )
                try:
                    params = _build_run_params(
                        dataset=request.dataset,
                        name=request.name,
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
                    # E2E runs have no safe resume contract.  Do not expose a
                    # restart switch that suggests failed partial ingestion can
                    # be resumed; users can explicitly reproduce a completed
                    # run after reviewing its immutable inputs.
                    supervise=False,
                    supervision="none",
                    stale_minutes=60,
                    max_restarts=0,
                    poll_seconds=30,
                )
                return job
            if request.dataset_create is None:
                raise HTTPException(
                    status_code=400,
                    detail="dataset_create is required for dataset jobs",
                )
            create = request.dataset_create
            display_name = (create.display_name or create.title or "").strip()
            if not display_name:
                raise HTTPException(
                    status_code=400,
                    detail="dataset_create.display_name is required",
                )
            pages = create.pages or TIER_PAGE_DEFAULTS.get(create.tier, 12)
            try:
                job = eval_jobs.start_dataset_job(
                    runs_root=root,
                    datasets_root=datasets,
                    dataset_id=create.dataset_id,
                    display_name=display_name,
                    tier=create.tier,
                    profile=create.profile,
                    language=create.language,
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

    # Keep this literal route before ``/datasets/{dataset_id}``, otherwise
    # FastAPI would interpret ``import`` as a dataset identifier.
    @router.post("/datasets/import", dependencies=[Depends(combined_auth)])
    async def import_dataset(file: UploadFile = File(...)) -> dict[str, Any]:
        try:
            require_eval()
            return _import_dataset_archive(archive=file, datasets_root=datasets)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(f"Error importing generated scenario: {exc}")
            raise internal_server_error(exc)
        finally:
            await file.close()

    @router.get("/datasets/{dataset_id}", dependencies=[Depends(combined_auth)])
    async def get_dataset(dataset_id: str) -> dict[str, Any]:
        try:
            require_eval()
            _validate_dataset_id(dataset_id)
            path = datasets / dataset_id
            if not path.is_dir() or not (path / "manifest.json").exists():
                raise HTTPException(status_code=404, detail="dataset not found")
            payload = load_manifest(path).model_dump()
            try:
                oracle = load_oracle(path)
                questions = oracle.questions or []
                payload["question_count"] = len(questions)
                payload["question_types"] = sorted(
                    {
                        str(question.question_type or "")
                        for question in questions
                        if question.question_type
                    }
                )
            except (OSError, ValueError):
                payload["question_count"] = 0
                payload["question_types"] = []
            return payload
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
            return _evaluation_model_capability()
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error listing Ollama models: {exc}")
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
