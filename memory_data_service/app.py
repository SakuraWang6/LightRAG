from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader

from memory_data_service.generators import generate_dataset
from memory_data_service.schemas import DatasetCreateRequest, DatasetManifest
from memory_data_service.storage import (
    DEFAULT_GENERATED_ROOT,
    list_datasets,
    load_manifest,
    load_oracle,
)

app = FastAPI(title="LightRAG Memory Data Service", version="0.1.0")


_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _require_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    """Optional auth: enabled only when MEMORY_DATA_SERVICE_API_KEY is set."""
    expected = os.getenv("MEMORY_DATA_SERVICE_API_KEY")
    if expected and api_key != expected:
        raise HTTPException(status_code=401, detail="invalid API key")


@app.post("/datasets", response_model=DatasetManifest)
def create_dataset(
    request: DatasetCreateRequest,
    force: bool = Query(default=False, description="Overwrite an existing dataset id."),
    _: None = Depends(_require_api_key),
) -> DatasetManifest:
    try:
        return generate_dataset(request, root=DEFAULT_GENERATED_ROOT, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/datasets")
def get_datasets(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(_require_api_key),
) -> dict:
    datasets = list_datasets(DEFAULT_GENERATED_ROOT)
    return {
        "datasets": datasets[offset : offset + limit],
        "total": len(datasets),
        "offset": offset,
        "limit": limit,
    }


@app.get("/datasets/{dataset_id}", response_model=DatasetManifest)
def get_dataset(
    dataset_id: str,
    _: None = Depends(_require_api_key),
) -> DatasetManifest:
    dataset_path = _dataset_path(dataset_id)
    return load_manifest(dataset_path)


@app.get("/datasets/{dataset_id}/oracle")
def get_oracle(
    dataset_id: str,
    _: None = Depends(_require_api_key),
):
    dataset_path = _dataset_path(dataset_id)
    return load_oracle(dataset_path).model_dump()


@app.get("/datasets/{dataset_id}/files/{name}")
def get_file(
    dataset_id: str,
    name: str,
    _: None = Depends(_require_api_key),
) -> FileResponse:
    dataset_path = _dataset_path(dataset_id)
    target = (dataset_path / name).resolve()
    if (
        dataset_path.resolve() not in target.parents
        and target != dataset_path.resolve()
    ):
        raise HTTPException(status_code=400, detail="invalid file path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(str(target), filename=target.name)


@app.delete("/datasets/{dataset_id}")
def delete_dataset(
    dataset_id: str,
    _: None = Depends(_require_api_key),
) -> dict:
    dataset_path = _dataset_path(dataset_id)
    shutil.rmtree(dataset_path)
    return {"deleted": dataset_id}


def _safe_dataset_id(dataset_id: str) -> str:
    if (
        not dataset_id
        or dataset_id in {".", ".."}
        or "/" in dataset_id
        or "\\" in dataset_id
    ):
        raise HTTPException(status_code=400, detail="invalid dataset id")
    return dataset_id


def _dataset_path(dataset_id: str) -> Path:
    path = DEFAULT_GENERATED_ROOT / _safe_dataset_id(dataset_id)
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail="dataset not found")
    return path
