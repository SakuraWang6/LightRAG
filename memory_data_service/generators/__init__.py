from __future__ import annotations

from pathlib import Path

from memory_data_service.generators.chinese_docx_generator import (
    generate_chinese_dataset,
)
from memory_data_service.generators.docx_generator import (
    generate_dataset as generate_basic_dataset,
)
from memory_data_service.generators.rich_docx_generator import generate_rich_dataset
from memory_data_service.schemas import (
    DEFAULT_SYNTHETIC_DOCUMENT_TITLE,
    DatasetCreateRequest,
    DatasetManifest,
)
from memory_data_service.storage import DEFAULT_GENERATED_ROOT


def generate_dataset(
    request: DatasetCreateRequest,
    *,
    root: Path = DEFAULT_GENERATED_ROOT,
    force: bool = False,
) -> DatasetManifest:
    dataset_path = root / request.dataset_id if request.dataset_id else None
    if dataset_path is not None and (dataset_path / "manifest.json").exists() and not force:
        raise ValueError(
            f"dataset already exists: {request.dataset_id} at {dataset_path} "
            "(pass force=True to overwrite)"
        )
    display_name = request.display_name.strip()
    if display_name:
        # Generated source documents deliberately inherit the business-facing
        # dataset name, so imported documents and the dataset selector agree.
        request = request.model_copy(update={"title": display_name})
    elif request.title.strip() and request.title != DEFAULT_SYNTHETIC_DOCUMENT_TITLE:
        # Preserve useful names from CLI/API clients created before
        # ``display_name`` existed without turning the old generic template
        # title into a misleading dataset name.
        request = request.model_copy(update={"display_name": request.title.strip()})
    if request.language == "zh":
        return generate_chinese_dataset(request, root=root)
    if request.profile == "basic":
        return generate_basic_dataset(request, root=root)
    return generate_rich_dataset(request, root=root)


__all__ = ["generate_dataset"]
