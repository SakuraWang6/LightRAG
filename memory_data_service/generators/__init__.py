from __future__ import annotations

from pathlib import Path

from memory_data_service.generators.docx_generator import generate_dataset as generate_basic_dataset
from memory_data_service.generators.rich_docx_generator import generate_rich_dataset
from memory_data_service.schemas import DatasetCreateRequest, DatasetManifest
from memory_data_service.storage import DEFAULT_GENERATED_ROOT


def generate_dataset(
    request: DatasetCreateRequest,
    *,
    root: Path = DEFAULT_GENERATED_ROOT,
) -> DatasetManifest:
    if request.profile == "basic":
        return generate_basic_dataset(request, root=root)
    return generate_rich_dataset(request, root=root)

__all__ = ["generate_dataset"]
