"""Unit tests for recall-lab ranking metrics."""

from __future__ import annotations

import json

import pytest

from memory_recall_lab import retrieval


def _dataset(tmp_path, facts):
    oracle = {
        "questions": [
            {
                "id": "Q-1",
                "question": "In TBL-0003, what is the maximum value?",
                "question_type": "table_cell",
                "evidence_fact_ids": [fact["fact_id"] for fact in facts],
                "expected_behavior": "answer",
            }
        ],
        "facts": facts,
    }
    (tmp_path / "oracle.json").write_text(json.dumps(oracle), encoding="utf-8")
    return str(tmp_path)


def _response(chunks):
    return {
        "status": "success",
        "data": {
            "chunks": chunks,
            "references": [],
        },
    }


@pytest.mark.parametrize(
    ("gold_index", "expected_recall_1", "expected_first_rank"),
    [
        (0, 1.0, 1),
        (2, 0.0, 3),
        (9, 0.0, 10),
    ],
)
def test_evaluate_recall_single_fact_ranking(
    monkeypatch, tmp_path, gold_index, expected_recall_1, expected_first_rank
):
    facts = [
        {
            "fact_id": "FACT-00006",
            "expected_text": "FACT-00006 authoritative maximum 33.75 ms",
            "answer": "33.75 ms",
        }
    ]
    dataset = _dataset(tmp_path, facts)
    chunks = []
    for index in range(10):
        content = (
            f"FACT-00006 authoritative maximum 33.75 ms"
            if index == gold_index
            else f"distractor chunk {index}"
        )
        chunks.append(
            {
                "content": content,
                "file_path": "doc.docx",
                "chunk_id": f"c-{index}",
            }
        )
    monkeypatch.setattr(retrieval, "post_json", lambda *a, **k: _response(chunks))

    report = retrieval.evaluate_recall(
        dataset_source=dataset,
        rag_api_url="http://unused",
        mode="naive",
        top_k=10,
    )

    assert report["cases"] == 1
    result = report["results"][0]
    assert result["recall_at_1"] == expected_recall_1
    assert result["first_evidence_rank"] == expected_first_rank
    assert result["recall_at_k"] == 1.0
    assert result["candidates"][gold_index]["matched_fact_ids"] == ["FACT-00006"]
    assert report["summary"]["overall"]["mrr"] == 1 / expected_first_rank


def test_evaluate_recall_miss(monkeypatch, tmp_path):
    facts = [{"fact_id": "FACT-00006", "expected_text": "absent evidence"}]
    dataset = _dataset(tmp_path, facts)
    monkeypatch.setattr(
        retrieval,
        "post_json",
        lambda *a, **k: _response(
            [{"content": "nothing relevant", "file_path": "doc.docx", "chunk_id": "c-0"}]
        ),
    )

    report = retrieval.evaluate_recall(
        dataset_source=dataset,
        rag_api_url="http://unused",
        mode="naive",
        top_k=5,
    )

    assert report["results"][0]["first_evidence_rank"] is None
    assert report["results"][0]["recall_at_1"] == 0.0
    assert report["summary"]["overall"]["gold_rank_distribution"]["miss"] == 1


def test_evaluate_recall_multi_fact_full_recall(monkeypatch, tmp_path):
    facts = [
        {"fact_id": "FACT-1", "expected_text": "first evidence"},
        {"fact_id": "FACT-2", "expected_text": "second evidence"},
    ]
    dataset = _dataset(tmp_path, facts)
    chunks = [
        {"content": "first evidence", "file_path": "a.docx", "chunk_id": "c-0"},
        {"content": "distractor", "file_path": "a.docx", "chunk_id": "c-1"},
        {"content": "second evidence", "file_path": "a.docx", "chunk_id": "c-2"},
    ]
    monkeypatch.setattr(retrieval, "post_json", lambda *a, **k: _response(chunks))

    report = retrieval.evaluate_recall(
        dataset_source=dataset,
        rag_api_url="http://unused",
        mode="naive",
        top_k=5,
    )

    result = report["results"][0]
    assert result["recall_at_1"] == 0.5
    assert result["full_recall_at_1"] is False
    assert result["recall_at_3"] == 1.0
    assert result["full_recall_at_3"] is True
    assert result["mean_fact_mrr"] == (1 + 1 / 3) / 2
