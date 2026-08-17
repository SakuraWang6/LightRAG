"""Deterministic dataset identity and scenario metadata helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from memory_data_service.schemas import (
    DATASET_SCHEMA_VERSION,
    DatasetCreateRequest,
    GenerationProvenance,
    QuestionRecord,
)

_QUESTION_SCENARIOS: dict[str, list[str]] = {
    "direct_numeric": ["single_hop", "distractor_fact", "approximate_numeric"],
    "version_condition": ["single_hop", "negative_question"],
    "conflict_resolution": ["multi_hop", "contradictory_fact"],
    "negative_constraint": ["single_hop", "negative_question"],
    "table_cell": ["table"],
    "figure_text": ["image"],
    "figure_caption": ["image"],
    "equation": ["formula"],
    "equation_variable": ["formula"],
    "formula_variable": ["formula"],
    "multi_hop": ["multi_hop", "cross_page"],
    "cross_document": ["multi_hop", "cross_document"],
    "abstain": ["unanswerable"],
}


def annotate_question_scenarios(questions: list[QuestionRecord]) -> dict[str, int]:
    """Attach canonical scenario labels and return the observable quotas."""
    counts: dict[str, int] = {}
    for question in questions:
        labels = _QUESTION_SCENARIOS.get(question.question_type, ["single_hop"])
        question.scenario_labels = labels  # type: ignore[assignment]
        question.question_variants = {
            "canonical": question.question,
            "paraphrase": f"According to the document, {question.question}",
            "evidence_first": f"Use the document evidence to answer: {question.question}",
            "entity_alias": f"For the referenced record, {question.question}",
            "word_order": f"From the document, answer this request: {question.question}",
        }
        for label in labels:
            counts[label] = counts.get(label, 0) + 1
    return counts


def resolve_scenario_quotas(
    *, requested: dict[str, int], observed: dict[str, int]
) -> dict[str, int]:
    """Enforce requested lower bounds; otherwise record the generated quota."""
    missing = {
        name: quota
        for name, quota in requested.items()
        if quota < 0 or observed.get(name, 0) < quota
    }
    if missing:
        details = ", ".join(
            f"{name}: requested {quota}, observed {observed.get(name, 0)}"
            for name, quota in sorted(missing.items())
        )
        raise ValueError("generated dataset did not meet scenario quotas: " + details)
    return dict(requested) if requested else dict(sorted(observed.items()))


def build_provenance(
    *,
    request: DatasetCreateRequest,
    pages: int,
    generator: str,
    template_version: str,
    source_file: Path,
) -> tuple[str, GenerationProvenance]:
    code_version = hashlib.sha256(source_file.read_bytes()).hexdigest()
    inputs: dict[str, Any] = {
        "tier": request.tier,
        "pages": pages,
        "profile": request.profile,
        "language": request.language,
        "formats": list(request.formats),
        "modalities": list(request.modalities),
        "title": request.title,
        "display_name": request.display_name,
        "split": request.split,
        "scenario_quotas": dict(sorted(request.scenario_quotas.items())),
    }
    provenance = GenerationProvenance(
        generator=generator,
        generator_code_version=code_version,
        template_version=template_version,
        seed=request.seed,
        input_parameters=inputs,
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                **provenance.model_dump(),
                "oracle_schema_version": DATASET_SCHEMA_VERSION,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return fingerprint, provenance
