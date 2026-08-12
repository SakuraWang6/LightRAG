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


def test_answer_evaluation_parallel_preserves_order(monkeypatch) -> None:
    """The threaded answer path must keep deterministic question order."""

    class FakeDatasetClient:
        def __init__(self, _source: str) -> None:
            pass

        @staticmethod
        def oracle() -> dict[str, Any]:
            return {
                "facts": [],
                "questions": [
                    {
                        "id": f"Q-{index}",
                        "question": f"Value {index}?",
                        "answer": str(index),
                        "question_type": "direct_numeric",
                        "evidence_fact_ids": [],
                    }
                    for index in (1, 2, 3, 4, 5)
                ],
            }

    def fake_post(_url, payload, **_kwargs):
        value = str(payload["query"]).split()[1].rstrip("?")
        return {"response": value, "response_truncated": False, "references": []}

    monkeypatch.setattr(answer, "DatasetClient", FakeDatasetClient)
    monkeypatch.setattr(answer, "_post_json", fake_post)

    report = answer.evaluate_answers(
        dataset_source="dataset", rag_api_url="http://rag", max_concurrency=3
    )

    assert [row["question_id"] for row in report["results"]] == [
        "Q-1",
        "Q-2",
        "Q-3",
        "Q-4",
        "Q-5",
    ]
    assert [row["answer"] for row in report["results"]] == ["1", "2", "3", "4", "5"]
    assert report["correct_cases"] == 5
