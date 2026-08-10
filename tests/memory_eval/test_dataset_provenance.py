"""I4 data lineage and scenario metadata tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_data_service.provenance import (
    annotate_question_scenarios,
    build_provenance,
    resolve_scenario_quotas,
)
from memory_data_service.schemas import DatasetCreateRequest, QuestionRecord
from memory_data_service.cross_document import add_cross_document_case
from memory_data_service.schemas import FactRecord


def test_provenance_fingerprint_is_deterministic_and_tracks_inputs() -> None:
    request = DatasetCreateRequest(
        profile="rich",
        pages=3,
        seed=21,
        split="validation",
        scenario_quotas={"multi_hop": 2, "table": 1},
    )
    source = Path(__file__)
    first, provenance = build_provenance(
        request=request,
        pages=3,
        generator="test-generator",
        template_version="v1",
        source_file=source,
    )
    second, _ = build_provenance(
        request=request,
        pages=3,
        generator="test-generator",
        template_version="v1",
        source_file=source,
    )
    assert first == second
    assert provenance.seed == 21
    assert provenance.input_parameters["split"] == "validation"


def test_question_scenarios_cover_table_and_unanswerable_cases() -> None:
    questions = [
        QuestionRecord(id="q1", question="x", answer="y", question_type="table_cell", evidence_fact_ids=[]),
        QuestionRecord(id="q2", question="x", answer="", question_type="abstain", evidence_fact_ids=[], expected_behavior="abstain"),
    ]
    counts = annotate_question_scenarios(questions)
    assert questions[0].scenario_labels == ["table"]
    assert questions[1].scenario_labels == ["unanswerable"]
    assert counts == {"table": 1, "unanswerable": 1}


def test_scenario_quotas_are_recorded_or_rejected() -> None:
    assert resolve_scenario_quotas(requested={}, observed={"table": 2}) == {"table": 2}
    with pytest.raises(ValueError, match="did not meet scenario quotas"):
        resolve_scenario_quotas(requested={"multi_hop": 1}, observed={"table": 2})


def test_cross_document_case_uses_two_document_evidence(tmp_path: Path) -> None:
    facts = [FactRecord(fact_id="FACT-1", fact_type="x", answer="42", expected_text="42", section="S", page=1, object_type="text")]
    questions: list[QuestionRecord] = []
    companion = add_cross_document_case(dataset_id="d", dataset_path=tmp_path, facts=facts, questions=questions)
    assert companion is not None and companion.exists()
    assert questions[0].question_type == "cross_document"
    assert questions[0].evidence_fact_ids == ["FACT-1", "FACT-CROSS-00001"]
