import json

import pytest

from memory_eval_tests.online.answer_eval import (
    _canonical_formula,
    _formula_match,
    score_answer,
)

pytestmark = pytest.mark.offline

def test_formula_normalization_accepts_latex_unicode_and_fraction_variants():
    expected = r"E_{5}=P_{5}T_{5}/\eta_{5}"
    variants = (
        "E_5 = P_5 T_5 / eta_5",
        r"E_{5}=\frac{P_{5}T_{5}}{\eta_{5}}",
        r"E_5 = \frac{P_5 T_5}{η_5}",
        "E_{5} = (P_{5} * T_{5}) / η_{5}",
    )
    assert [_canonical_formula(value) for value in variants] == [_canonical_formula(expected)] * len(variants)
    assert all(_formula_match(expected, value) for value in variants)


def test_formula_normalization_keeps_operator_structure():
    expected = r"E_{5}=P_{5}T_{5}/\eta_{5}"
    assert not _formula_match(expected, "E_5 = P_5 + T_5 / eta_5")


def test_abstention_synonyms_are_recognized_deterministically():
    question = {"question_type": "abstain", "expected_behavior": "abstain"}
    variants = (
        "The document does not mention this approval code.",
        "It cannot be addressed from the provided context.",
        "There is insufficient information to determine the answer.",
        "The context does not contain the requested appendix.",
    )
    assert all(
        score_answer(
            answer_text=value,
            expected="The document does not provide this information.",
            question=question,
            evidence_facts=[],
            references_blob="",
        )["abstention_correct"]
        for value in variants
    )


def test_evidence_and_citation_metrics_are_not_conflated():
    fact = {"fact_id": "FACT-00001", "answer": "9021 QMU", "expected_text": "9021 QMU"}
    base = dict(
        expected="9021 QMU",
        question={"question_type": "direct_numeric", "expected_behavior": "answer"},
        evidence_facts=[fact],
        references_blob="Evidence: FACT-00001 = 9021 QMU",
    )
    no_citation = score_answer(answer_text="The answer is 9021 QMU.", **base)
    cited = score_answer(answer_text="The answer is 9021 QMU (FACT-00001).", **base)

    assert no_citation["evidence_available"] is True
    assert no_citation["citation_presence"] is False
    assert no_citation["citation_correctness"] is None
    assert no_citation["grounded"] is True
    assert no_citation["ungrounded"] is False
    assert cited["citation_presence"] is True
    assert cited["citation_correctness"] is True


def test_ungrounded_reflects_answer_error_or_missing_evidence():
    fact = {"fact_id": "FACT-00001", "answer": "9021 QMU", "expected_text": "9021 QMU"}
    base = dict(
        expected="9021 QMU",
        question={"question_type": "direct_numeric", "expected_behavior": "answer"},
        evidence_facts=[fact],
        references_blob="Evidence: FACT-00001 = 9021 QMU",
    )
    wrong = score_answer(answer_text="The answer is 9999 XYZ.", **base)
    assert wrong["grounded"] is False
    assert wrong["ungrounded"] is True

    missing_evidence = score_answer(
        answer_text="The answer is 9021 QMU.",
        expected="9021 QMU",
        question={"question_type": "direct_numeric", "expected_behavior": "answer"},
        evidence_facts=[fact],
        references_blob="",
    )
    assert missing_evidence["grounded"] is False
    assert missing_evidence["ungrounded"] is True


def test_score_answer_no_longer_emits_legacy_alias_fields():
    scored = score_answer(
        answer_text="The answer is 9021 QMU (FACT-00001).",
        expected="9021 QMU",
        question={"question_type": "direct_numeric", "expected_behavior": "answer"},
        evidence_facts=[{"fact_id": "FACT-00001", "answer": "9021 QMU", "expected_text": "9021 QMU"}],
        references_blob="Evidence: FACT-00001 = 9021 QMU",
    )
    assert "citation_correct" not in scored
    assert "hallucinated" not in scored
    assert scored["ungrounded"] is False


def test_abstain_excludes_evidence_available_but_counts_as_grounded():
    question = {"question_type": "abstain", "expected_behavior": "abstain"}
    expected = "The document does not provide this information."
    correct = score_answer(
        answer_text="The document does not mention this approval code.",
        expected=expected,
        question=question,
        evidence_facts=[],
        references_blob="",
    )
    assert correct["abstention_correct"] is True
    assert correct["evidence_available"] is None
    assert correct["citation_presence"] is False
    assert correct["grounded"] is True
    assert correct["ungrounded"] is False

    wrong = score_answer(
        answer_text="The answer is 42.",
        expected=expected,
        question=question,
        evidence_facts=[],
        references_blob="",
    )
    assert wrong["abstention_correct"] is False
    assert wrong["evidence_available"] is None
    assert wrong["grounded"] is False
    assert wrong["ungrounded"] is True


def test_evaluate_answers_emits_canonical_summary_keys(monkeypatch, tmp_path):
    from memory_eval_tests.online.answer_eval import evaluate_answers

    def fake_post_json(url: str, payload: dict, **kwargs) -> dict:
        return {"response": "The answer is 9021 QMU (FACT-00001).", "references": []}

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "oracle.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "id": "Q1",
                        "question": "What is X?",
                        "question_type": "direct_numeric",
                        "expected_behavior": "answer",
                        "answer": "9021 QMU",
                        "evidence_fact_ids": ["FACT-1"],
                    },
                    {
                        "id": "Q2",
                        "question": "Y?",
                        "question_type": "abstain",
                        "expected_behavior": "abstain",
                        "answer": "The document does not provide this information.",
                        "evidence_fact_ids": [],
                    },
                    {
                        "id": "Q3",
                        "question": "Z?",
                        "question_type": "direct_numeric",
                        "expected_behavior": "answer",
                        "answer": "9038 QMU",
                        "evidence_fact_ids": ["FACT-2"],
                    },
                ],
                "facts": [
                    {"fact_id": "FACT-1", "answer": "9021 QMU", "expected_text": "9021 QMU"},
                    {"fact_id": "FACT-2", "answer": "9038 QMU", "expected_text": "9038 QMU"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("memory_eval_tests.online.answer_eval._post_json", fake_post_json)
    report = evaluate_answers(
        dataset_source=str(dataset),
        rag_api_url="http://127.0.0.1:9621",
        max_cases=3,
    )
    assert "ungrounded_rate" in report
    assert "hallucination_rate" not in report
    assert "citation_accuracy" not in report
    assert "evidence_available" in report
    assert [row["question"] for row in report["results"]] == [
        "What is X?",
        "Y?",
        "Z?",
    ]


def test_evaluate_answers_requests_and_records_controlled_final_context_trace(monkeypatch, tmp_path):
    from memory_eval_tests.online.answer_eval import evaluate_answers

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "oracle.json").write_text(
        json.dumps(
            {
                "questions": [{"id": "Q1", "question": "What?", "answer": "42", "evidence_fact_ids": []}],
                "facts": [],
            }
        ),
        encoding="utf-8",
    )
    seen: dict = {}

    def fake_post_json(_url, payload, **_kwargs):
        seen.update(payload)
        return {
            "response": "42",
            "references": [],
            "evaluation_trace": {"status": "observed", "final_context": "oracle context"},
        }

    monkeypatch.setattr("memory_eval_tests.online.answer_eval._post_json", fake_post_json)
    result = evaluate_answers(
        dataset_source=str(dataset), rag_api_url="http://api.test", evaluation_trace=True
    )
    assert seen["evaluation_trace"] is True
    assert result["results"][0]["final_context_trace"]["final_context"] == "oracle context"
