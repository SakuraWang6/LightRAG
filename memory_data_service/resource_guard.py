from __future__ import annotations

import tracemalloc
from dataclasses import dataclass
from typing import Any, Self

from memory_data_service.schemas import DatasetCreateRequest

MAX_DEFAULT_GENERATION_PAGES = 3000


def enforce_generation_limits(request: DatasetCreateRequest, pages: int) -> None:
    if pages <= MAX_DEFAULT_GENERATION_PAGES or request.allow_oversized_generation:
        return
    raise ValueError(
        f"Refusing to generate {pages} pages without allow_oversized_generation=true. "
        f"The default guard is {MAX_DEFAULT_GENERATION_PAGES} pages because DOCX "
        "generation is in-process and python-docx is not streaming."
    )


def estimate_generation_resources(
    request: DatasetCreateRequest,
    pages: int,
) -> dict[str, Any]:
    table_count = pages // 3 if request.profile == "rich" else pages // 5
    figure_count = pages // 4 if "figures" in request.modalities else 0
    equation_count = pages // 5 if "equations" in request.modalities else 0
    if request.profile == "rich":
        table_count += 1  # appendix long-table stress object
        fact_count = pages + table_count + figure_count + equation_count
        fact_count += pages // 7 + pages // 9 + pages // 11 + 4
        question_count = (
            fact_count
            + max(1, pages // 5)
            + max(1, pages // 6)
            + max(1, pages // 8)
            + 6
        )
        object_count = 8 + pages * 15 + table_count * 4 + figure_count * 3 + equation_count * 4
        relation_count = object_count + fact_count * 3
    else:
        fact_count = pages + table_count + figure_count + equation_count
        question_count = fact_count
        object_count = 0
        relation_count = 0
    return {
        "max_default_pages": MAX_DEFAULT_GENERATION_PAGES,
        "allow_oversized_generation": request.allow_oversized_generation,
        "estimated_facts": fact_count,
        "estimated_questions": question_count,
        "estimated_objects": object_count,
        "estimated_relations": relation_count,
        "estimated_tables": table_count,
        "estimated_figures": figure_count,
        "estimated_equations": equation_count,
        "notes": [
            "DOCX generation uses python-docx and is not streaming.",
            "The guard prevents accidental oversized generation unless explicitly overridden.",
        ],
    }


@dataclass
class GenerationResourceMonitor:
    peak_memory_mb: float | None = None
    _started_tracing: bool = False

    def __enter__(self) -> Self:
        self._started_tracing = not tracemalloc.is_tracing()
        if self._started_tracing:
            tracemalloc.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        _, peak = tracemalloc.get_traced_memory()
        self.peak_memory_mb = round(peak / (1024 * 1024), 3)
        if self._started_tracing:
            tracemalloc.stop()
