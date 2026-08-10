"""Add a second source document and a real cross-document oracle question."""

from __future__ import annotations

from pathlib import Path

from memory_data_service.schemas import FactRecord, QuestionRecord


def add_cross_document_case(
    *, dataset_id: str, dataset_path: Path, facts: list[FactRecord], questions: list[QuestionRecord]
) -> Path | None:
    """Materialize a companion DOCX and a question that requires both sources."""
    if not facts:
        return None
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError("python-docx is required for cross-document cases") from exc
    source = facts[0]
    companion_fact_id = "FACT-CROSS-00001"
    companion_answer = "enabled"
    companion = dataset_path / f"{dataset_id}-companion.docx"
    document = Document()
    document.core_properties.title = "LightRAG Synthetic Companion Evidence"
    document.add_heading("Companion Evidence Register", 0)
    document.add_paragraph(
        f"{companion_fact_id}: The companion verification state is {companion_answer}."
    )
    document.save(companion)
    facts.append(
        FactRecord(
            fact_id=companion_fact_id,
            fact_type="cross_document",
            answer=companion_answer,
            expected_text=f"{companion_fact_id}: The companion verification state is {companion_answer}.",
            section="Companion Evidence Register",
            page=1,
            object_type="text",
            object_id_hint="companion.docx",
        )
    )
    questions.append(
        QuestionRecord(
            id="Q-CROSS-DOCUMENT-00001",
            question=(
                "Using the primary document and the companion document, what are the "
                f"canonical value for {source.fact_id} and the companion verification state?"
            ),
            answer=f"{source.answer}; {companion_answer}",
            question_type="cross_document",
            evidence_fact_ids=[source.fact_id, companion_fact_id],
        )
    )
    return companion
