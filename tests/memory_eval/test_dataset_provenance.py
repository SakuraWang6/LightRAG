"""I4 data lineage and scenario metadata tests."""

from __future__ import annotations

from pathlib import Path

from memory_data_service.provenance import annotate_question_scenarios, build_provenance
from memory_data_service.schemas import DatasetCreateRequest, QuestionRecord


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
