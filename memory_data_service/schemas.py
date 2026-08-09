from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


DATASET_SCHEMA_VERSION = "1.0"
_SUPPORTED_SCHEMA_VERSIONS = frozenset({DATASET_SCHEMA_VERSION})


TierName = Literal["smoke", "medium", "large", "stress"]
DocumentFormat = Literal["docx", "pdf"]
ModalityName = Literal["text", "tables", "figures", "equations"]
ProfileName = Literal["basic", "rich"]
ObjectType = Literal[
    "document",
    "section",
    "paragraph",
    "table",
    "figure",
    "equation",
    "caption",
    "reference",
    "footnote",
    "endnote",
    "layout_region",
    "textbox",
    "glossary_term",
    "appendix",
]
RelationType = Literal[
    "contains",
    "mentions",
    "refers_to",
    "supports",
    "contradicts",
    "distracts",
    "defines",
    "located_in",
    "caption_of",
]


TIER_PAGE_DEFAULTS: dict[str, int] = {
    "smoke": 20,
    "medium": 200,
    "large": 1000,
    "stress": 3000,
}

MAX_DEFAULT_GENERATION_PAGES = 3000


class DatasetCreateRequest(BaseModel):
    tier: TierName = "smoke"
    pages: int | None = Field(default=None, ge=1)
    allow_oversized_generation: bool = False
    profile: ProfileName = "rich"
    formats: list[DocumentFormat] = Field(default_factory=lambda: ["docx"])
    modalities: list[ModalityName] = Field(
        default_factory=lambda: ["text", "tables", "figures", "equations"]
    )
    dataset_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+$")
    seed: int = 13
    title: str = "LightRAG Synthetic Rich Memory Document"

    def resolved_pages(self) -> int:
        return self.pages or TIER_PAGE_DEFAULTS[self.tier]


class GeneratedFile(BaseModel):
    name: str
    format: DocumentFormat | Literal["json", "png"]
    path: str
    size_bytes: int = 0
    status: Literal["created", "skipped"] = "created"
    message: str = ""


class FactRecord(BaseModel):
    fact_id: str
    fact_type: str
    answer: str
    expected_text: str
    section: str
    page: int
    object_type: Literal["text", "table", "figure", "equation", "caption", "reference"]
    object_id_hint: str = ""


class QuestionRecord(BaseModel):
    id: str
    question: str
    answer: str
    question_type: str
    evidence_fact_ids: list[str]
    expected_behavior: Literal["answer", "abstain"] = "answer"


class DocumentObject(BaseModel):
    object_id: str
    object_type: ObjectType
    title: str = ""
    text: str = ""
    section: str = ""
    page_start: int
    page_end: int | None = None
    parent_id: str = ""
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)


class ObjectRelation(BaseModel):
    relation_id: str
    source_id: str
    target_id: str
    relation_type: RelationType
    evidence_text: str = ""
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)


class DatasetManifest(BaseModel):
    schema_version: str = DATASET_SCHEMA_VERSION
    dataset_id: str
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    tier: TierName
    pages: int
    profile: ProfileName = "basic"
    formats: list[DocumentFormat]
    modalities: list[ModalityName]
    title: str
    files: list[GeneratedFile] = Field(default_factory=list)
    generation_time_seconds: float | None = None
    generation_peak_memory_mb: float | None = None
    generation_resource_estimate: dict[str, str | int | float | bool | list[str]] = Field(
        default_factory=dict
    )
    facts_file: str = "facts.json"
    questions_file: str = "questions.json"
    objects_file: str = "objects.json"
    relations_file: str = "relations.json"
    oracle_file: str = "oracle.json"


class OraclePayload(BaseModel):
    schema_version: str = DATASET_SCHEMA_VERSION
    dataset_id: str
    facts: list[FactRecord]
    questions: list[QuestionRecord]
    objects: list[DocumentObject] = Field(default_factory=list)
    relations: list[ObjectRelation] = Field(default_factory=list)


class DatasetSummary(BaseModel):
    dataset_id: str
    tier: str
    profile: str = "basic"
    pages: int
    path: str
    created_at: str
    files: list[str]


def dataset_dir(root: Path, dataset_id: str) -> Path:
    return root / dataset_id


def check_schema_version(schema_version: str | None) -> tuple[bool, str]:
    """Return ``(supported, version)`` for a dataset's schema version.

    Missing versions are treated as the original unversioned layout (1.0).
    """
    version = schema_version or DATASET_SCHEMA_VERSION
    return version in _SUPPORTED_SCHEMA_VERSIONS, version
