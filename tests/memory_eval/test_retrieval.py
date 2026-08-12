"""Regression coverage for product-evaluation retrieval evidence."""

from __future__ import annotations

from typing import Any

from memory_eval_tests import retrieval


def test_retrieval_groups_multiple_fact_matches_in_one_full_chunk(
    monkeypatch,
) -> None:
    facts = [
        {
            "fact_id": "FACT-TABLE",
            "answer": "33.75 ms",
            "expected_text": "FACT-TABLE authoritative 33.75 ms",
        },
        {
            "fact_id": "FACT-EQUATION",
            "answer": "E_{5}=P_{5}T_{5}/\\eta_{5}",
            "expected_text": "Equation EQ-0005: E_{5}=P_{5}T_{5}/\\eta_{5}",
        },
        {
            "fact_id": "FACT-LONG-TABLE",
            "answer": "54.99 ms",
            "expected_text": "FACT-LONG-TABLE authoritative final rollover latency 54.99 ms",
        },
    ]
    questions = [
        {
            "id": "Q-MULTI",
            "question": "Which table value and equation apply?",
            "evidence_fact_ids": ["FACT-TABLE", "FACT-EQUATION"],
        },
        {
            "id": "Q-LONG",
            "question": "What is the final rollover latency?",
            "evidence_fact_ids": ["FACT-LONG-TABLE"],
        },
    ]
    chunk_with_both_facts = (
        "FACT-TABLE is in the preceding table with 33.75 ms.\n"
        + "padding " * 420
        + "Equation EQ-0005 states E_5=P_5T_5/eta_5."
    )
    id_only_chunk = "FACT-LONG-TABLE marks the appendix stress-table row."
    responses = iter(
        [
            {"data": {"references": [{"file_path": "source.docx", "content": [chunk_with_both_facts]}]}},
            {"data": {"references": [{"file_path": "source.docx", "content": [id_only_chunk]}]}},
        ]
    )

    class FakeDatasetClient:
        def __init__(self, _source: str) -> None:
            pass

        @staticmethod
        def oracle() -> dict[str, Any]:
            return {"facts": facts, "questions": questions}

    monkeypatch.setattr(retrieval, "DatasetClient", FakeDatasetClient)
    monkeypatch.setattr(retrieval, "_post_json", lambda *_args, **_kwargs: next(responses))

    report = retrieval.evaluate_api(dataset_source="dataset", rag_api_url="http://rag")
    multi, long_table = report["results"]

    assert multi["recall_at_k"] == 1.0
    assert multi["hit_fact_ids"] == ["FACT-TABLE", "FACT-EQUATION"]
    assert len(multi["hit_evidence"]) == 1
    evidence = multi["hit_evidence"][0]
    assert evidence["text"] == chunk_with_both_facts
    assert [match["fact_id"] for match in evidence["matches"]] == [
        "FACT-TABLE",
        "FACT-EQUATION",
    ]
    assert "33.75 ms" in evidence["matches"][0]["excerpt"]
    assert "E_5=P_5T_5/eta_5" in evidence["matches"][1]["excerpt"]

    # A marker without its answer-bearing value must not inflate recall@K.
    assert long_table["recall_at_k"] == 0.0
    assert long_table["hit_fact_ids"] == []
    assert long_table["hit_evidence"] == []


def test_fact_identifier_alone_is_not_retrieval_evidence() -> None:
    fact = {
        "fact_id": "FACT-00027",
        "answer": "54.99 ms",
        "expected_text": "FACT-00027 authoritative final rollover latency 54.99 ms",
    }

    assert retrieval._content_contains_fact(
        "FACT-00027 marks the authoritative final rollover latency.", fact
    ) is False
    assert retrieval._content_contains_fact(
        "The table row is [\"A-089\", \"54.99 ms\", \"FACT-00027\"].", fact
    ) is True
