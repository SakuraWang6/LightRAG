"""Tests for API retrieval ranking semantics (real MRR, not binary hits)."""

from __future__ import annotations

import json

import pytest

from memory_eval_tests.online.retrieval_eval import evaluate_api


def _write_oracle(tmp_path, *, questions, facts) -> str:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "oracle.json").write_text(
        json.dumps({"questions": questions, "facts": facts}),
        encoding="utf-8",
    )
    return str(dataset)


def _fact(fact_id: str, answer: str) -> dict:
    return {"fact_id": fact_id, "answer": answer, "expected_text": f"{fact_id}: {answer}"}


def test_api_recall_and_mrr_use_reference_rank(monkeypatch, tmp_path) -> None:
    dataset = _write_oracle(
        tmp_path,
        questions=[
            {
                "id": "Q1",
                "question": "What is the calibration limit for cell 0001 and cell 0002?",
                "question_type": "direct_numeric",
                "expected_behavior": "answer",
                "evidence_fact_ids": ["FACT-1", "FACT-2"],
            }
        ],
        facts=[_fact("FACT-1", "9021 QMU"), _fact("FACT-2", "9038 QMU")],
    )

    captured_payloads: list[dict] = []

    def fake_post_json(url: str, payload: dict) -> dict:
        captured_payloads.append(payload)
        assert payload["include_chunk_content"] is True
        return {
            "status": "ok",
            "data": {
                "references": [
                    {
                        "file_path": "rich-smoke.docx",
                        "content": [
                            "Some unrelated context sentence.",
                            "The authoritative calibration limit for cell 0001 is 9021 QMU.",
                        ],
                    },
                    {
                        "file_path": "rich-smoke.docx",
                        "content": [
                            "The authoritative calibration limit for cell 0002 is 9038 QMU."
                        ],
                    },
                ]
            },
            "metadata": {},
        }

    monkeypatch.setattr("memory_eval_tests.online.retrieval_eval._post_json", fake_post_json)
    report = evaluate_api(
        dataset_source=dataset,
        rag_api_url="http://127.0.0.1:9621",
        mode="mix",
        top_k=10,
    )
    assert captured_payloads[0]["mode"] == "mix"
    assert report["backend"] == "api"
    assert report["cases"] == 1
    assert report["average_recall"] == pytest.approx(1.0)
    # FACT-1 first appears in the second ranked chunk (rank 2), FACT-2 at rank 3.
    assert report["mrr"] == pytest.approx(0.5)
    assert report["context_precision"] == pytest.approx(1.0)
    assert report["object_hit_rate"] is None
    case = report["results"][0]
    assert case["hit_fact_ids"] == ["FACT-1", "FACT-2"]
    assert case["object_hit_rate"] is None
    assert [ctx["rank"] for ctx in case["top_contexts"]] == [1, 2]


def test_api_partial_recall_and_context_precision(monkeypatch, tmp_path) -> None:
    dataset = _write_oracle(
        tmp_path,
        questions=[
            {
                "id": "Q1",
                "question": "Which limits apply?",
                "question_type": "direct_numeric",
                "expected_behavior": "answer",
                "evidence_fact_ids": ["FACT-1", "FACT-2"],
            }
        ],
        facts=[_fact("FACT-1", "9021 QMU"), _fact("FACT-2", "9038 QMU")],
    )

    def fake_post_json(url: str, payload: dict) -> dict:
        return {
            "status": "ok",
            "data": {
                "references": [
                    {"file_path": "a.docx", "content": ["No evidence in this chunk."]},
                    {"file_path": "b.docx", "content": ["The limit is 9021 QMU."]},
                    {"file_path": "c.docx", "content": ["Unrelated."]},
                ]
            },
            "metadata": {},
        }

    monkeypatch.setattr("memory_eval_tests.online.retrieval_eval._post_json", fake_post_json)
    report = evaluate_api(
        dataset_source=dataset,
        rag_api_url="http://127.0.0.1:9621",
    )
    case = report["results"][0]
    assert case["recall_at_k"] == pytest.approx(0.5)
    # The only hit (FACT-1) appears in the second reference item -> rank 2.
    assert case["reciprocal_rank"] == pytest.approx(0.5)
    assert case["context_precision"] == pytest.approx(1 / 3)
    assert report["average_recall"] == pytest.approx(0.5)


@pytest.mark.parametrize(
    "response",
    [
        {"status": "ok", "data": {"references": None}, "metadata": {}},
        {"status": "ok", "data": {}, "metadata": {}},
        {
            "status": "ok",
            "data": {"references": [{"file_path": "a.docx"}]},
            "metadata": {},
        },
        {
            "status": "ok",
            "data": {"references": [{"file_path": "a.docx", "content": "not a list"}]},
            "metadata": {},
        },
    ],
)
def test_api_requires_ranked_chunk_content(monkeypatch, tmp_path, response) -> None:
    dataset = _write_oracle(
        tmp_path,
        questions=[
            {
                "id": "Q1",
                "question": "What is the limit?",
                "question_type": "direct_numeric",
                "expected_behavior": "answer",
                "evidence_fact_ids": ["FACT-1"],
            }
        ],
        facts=[_fact("FACT-1", "9021 QMU")],
    )

    monkeypatch.setattr(
        "memory_eval_tests.online.retrieval_eval._post_json",
        lambda url, payload: response,
    )
    with pytest.raises(ValueError, match=r"references|content"):
        evaluate_api(dataset_source=dataset, rag_api_url="http://127.0.0.1:9621")


def test_api_max_cases_samples_deterministically(monkeypatch, tmp_path) -> None:
    questions = [
        {
            "id": f"Q{i:03d}",
            "question": f"Question {i}?",
            "question_type": "direct_numeric",
            "expected_behavior": "answer",
            "evidence_fact_ids": [f"FACT-{i}"],
        }
        for i in range(1, 37)
    ]
    facts = [_fact(f"FACT-{i}", f"value {i}") for i in range(1, 37)]
    dataset = _write_oracle(tmp_path, questions=questions, facts=facts)
    seen: list[str] = []

    def fake_post_json(url: str, payload: dict) -> dict:
        seen.append(payload["query"])
        return {
            "status": "ok",
            "data": {
                "references": [{"file_path": "a.docx", "content": ["nothing"]}]
            },
            "metadata": {},
        }

    monkeypatch.setattr("memory_eval_tests.online.retrieval_eval._post_json", fake_post_json)
    report = evaluate_api(dataset_source=dataset, rag_api_url="http://127.0.0.1:9621", max_cases=4)
    assert report["cases"] == 4
    assert [case["question_id"] for case in report["results"]] == ["Q001", "Q013", "Q024", "Q036"]
    # Evenly spaced across the oracle, not a front-prefix sample.
    assert seen == ["Question 1?", "Question 13?", "Question 24?", "Question 36?"]
