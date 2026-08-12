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


def test_repeated_answer_attributed_to_expected_text_instance(monkeypatch) -> None:
    """A repeated answer sentence must not steal the FACT attribution.

    Generated documents repeat the same control sentence on several pages.
    The bare answer matches every occurrence; only the FACT-ID-anchored
    expected_text identifies the actual instance, so the first ranked chunk
    with that sentence (not the earlier repeated phrase) owns the hit.
    """
    fact = {
        "fact_id": "FACT-0016",
        "answer": "周衡完成来源复核且顾澄完成安全签核",
        "expected_text": (
            "FACT-0016：实施单元 0004 涉及退款或隐私变更时，"
            "发布前必须由周衡完成来源复核且顾澄完成安全签核。"
        ),
    }
    questions = [
        {
            "id": "Q-GATE",
            "question": "发布前还需要什么？",
            "evidence_fact_ids": ["FACT-0016"],
        }
    ]
    repeated_chunk = "第 15 页：该单元同样要求周衡完成来源复核且顾澄完成安全签核。"
    anchored_chunk = fact["expected_text"]
    responses = iter(
        [
            {
                "data": {
                    "references": [
                        {
                            "file_path": "source.docx",
                            "content": [repeated_chunk, anchored_chunk],
                        }
                    ]
                }
            }
        ]
    )

    class FakeDatasetClient:
        def __init__(self, _source: str) -> None:
            pass

        @staticmethod
        def oracle() -> dict[str, Any]:
            return {"facts": [fact], "questions": questions}

    monkeypatch.setattr(retrieval, "DatasetClient", FakeDatasetClient)
    monkeypatch.setattr(retrieval, "_post_json", lambda *_args, **_kwargs: next(responses))

    report = retrieval.evaluate_api(dataset_source="dataset", rag_api_url="http://rag")
    row = report["results"][0]
    assert row["recall_at_k"] == 1.0
    assert row["first_evidence_rank"] == 2
    assert row["hit_fact_ids"] == ["FACT-0016"]
    assert len(row["hit_evidence"]) == 1
    evidence = row["hit_evidence"][0]
    assert evidence["rank"] == 2
    assert evidence["text"] == anchored_chunk
    assert evidence["matches"][0]["match_type"] == "expected_text"


def test_markup_rendered_table_matches_expected_text() -> None:
    """A table row rendered as JSON must still anchor on the FACT sentence.

    The parser serialises tables as ``["FACT-00007", "标准行标记", "200 次/秒"]``;
    normalisation drops JSON and Chinese punctuation so the FACT-ID-anchored
    expected_text matches the rendered artifact precisely instead of falling
    back to a loose bare-answer match.
    """
    fact = {
        "fact_id": "FACT-00007",
        "answer": "200 次/秒",
        "expected_text": "FACT-00007 标准行标记 200 次/秒",
    }
    chunk = (
        '<table format="json">[["参数", "标称值", "配额 (次/秒)"],'
        '["FACT-00007", "标准行标记", "200 次/秒"]]</table>'
    )
    match = retrieval._find_expected_text_match(chunk, fact)
    assert match is not None
    assert match["match_type"] == "expected_text"
    assert "200 次/秒" in match["excerpt"]


def test_fullwidth_punctuation_does_not_break_evidence_match() -> None:
    fact = {
        "fact_id": "FACT-00011",
        "answer": "M-103",
        "expected_text": "FACT-00011：实施单元 0003 的交付里程碑为 M-103，由林岚在第 7 页确认。",
    }
    chunk = "FACT-00011：实施单元 0003 的交付里程碑为 M-103，由林岚在第 7 页确认。"
    assert retrieval._find_expected_text_match(chunk, fact) is not None


def test_value_without_unit_is_not_evidence_of_united_answer() -> None:
    """The matcher must not guess units: '200' is not evidence for '200 次/秒'."""
    fact = {
        "fact_id": "FACT-00007",
        "answer": "200 次/秒",
        "expected_text": "FACT-00007 标准行标记 200 次/秒",
    }
    chunk = '["FACT-00007", "标准行标记", "200"]'
    assert retrieval._find_expected_text_match(chunk, fact) is None
    assert retrieval._find_answer_match(chunk, fact) is None
