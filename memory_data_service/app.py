from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from memory_data_service.generators import generate_dataset
from memory_data_service.schemas import DatasetCreateRequest, DatasetManifest, DatasetSummary
from memory_data_service.storage import (
    DEFAULT_GENERATED_ROOT,
    list_datasets,
    load_manifest,
    load_oracle,
)


app = FastAPI(title="LightRAG Memory Data Service", version="0.1.0")


@app.post("/datasets", response_model=DatasetManifest)
def create_dataset(request: DatasetCreateRequest) -> DatasetManifest:
    try:
        return generate_dataset(request, root=DEFAULT_GENERATED_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/datasets", response_model=list[DatasetSummary])
def get_datasets() -> list[DatasetSummary]:
    return list_datasets(DEFAULT_GENERATED_ROOT)


@app.get("/datasets/{dataset_id}", response_model=DatasetManifest)
def get_dataset(dataset_id: str) -> DatasetManifest:
    dataset_path = _dataset_path(dataset_id)
    return load_manifest(dataset_path)


@app.get("/datasets/{dataset_id}/oracle")
def get_oracle(dataset_id: str):
    dataset_path = _dataset_path(dataset_id)
    return load_oracle(dataset_path).model_dump()


@app.get("/datasets/{dataset_id}/files/{name}")
def get_file(dataset_id: str, name: str) -> FileResponse:
    dataset_path = _dataset_path(dataset_id)
    target = (dataset_path / name).resolve()
    if dataset_path.resolve() not in target.parents and target != dataset_path.resolve():
        raise HTTPException(status_code=400, detail="invalid file path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(str(target), filename=target.name)


def _dataset_path(dataset_id: str) -> Path:
    path = DEFAULT_GENERATED_ROOT / dataset_id
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail="dataset not found")
    return path
