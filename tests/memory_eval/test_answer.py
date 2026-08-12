"""Regression coverage for answer-generation observability."""

from __future__ import annotations

from typing import Any

from memory_eval_tests import answer


def test_answer_evaluation_persists_provider_truncation_signal(monkeypatch) -> None:
    class FakeDatasetClient:
        def __init__(self, _source: str) -> None:
            pass

        @staticmethod
        def oracle() -> dict[str, Any]:
            return {
                "facts": [],
                "questions": [
                    {
                        "id": "Q-1",
                        "question": "What is the value?",
                        "answer": "42",
                        "question_type": "direct_numeric",
                        "evidence_fact_ids": [],
                    }
                ],
            }

    monkeypatch.setattr(answer, "DatasetClient", FakeDatasetClient)
    monkeypatch.setattr(
        answer,
        "_post_json",
        lambda *_args, **_kwargs: {
            "response": "42, followed by an incomplete explanation",
            "response_truncated": True,
            "references": [],
        },
    )

    report = answer.evaluate_answers(dataset_source="dataset", rag_api_url="http://rag")

    assert report["results"][0]["response_truncated"] is True
    assert report["generation_truncation_rate"] == 1.0
