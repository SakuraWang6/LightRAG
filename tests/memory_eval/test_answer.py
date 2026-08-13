"""Regression coverage for answer-generation observability."""

from __future__ import annotations

from typing import Any

from memory_eval_tests import answer


def test_looks_like_abstain_accepts_missing_information_phrasings() -> None:
    assert answer._looks_like_abstain(
        "文档中不存在关于“锆石旁路模块”的任何信息，因此无法提供其审批编号。"
    )
    assert answer._looks_like_abstain("根据提供的文档，无法给出该编号。")
    assert answer._looks_like_abstain("上下文未包含该模块的相关信息。")
    assert answer._looks_like_abstain("文档中没有关于该模块的信息")


def test_numeric_unit_match_accepts_chinese_units_without_spaces() -> None:
    assert answer._numeric_unit_match("114 次/秒", "吞吐配额为 114次/秒")
    assert answer._numeric_unit_match("9 小时", "校准窗口为 9小时")
    assert answer._numeric_unit_match("30 分", "转人工阈值为 30 分")
    assert answer._numeric_unit_match("0.50 %", "错误率上限为 0.50%")
    assert answer._numeric_unit_match("1043 QMU", "标准标定上限为 1043 QMU")


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
