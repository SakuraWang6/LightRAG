import pytest

from memory_eval_tests.online.answer_eval import _question_variant, score_answer


def test_semantic_question_without_scorer_is_uncertain_and_queued() -> None:
    result = score_answer(
        answer_text="equivalent wording",
        expected="canonical wording",
        question={"question_type": "free_text", "scoring_mode": "semantic"},
        evidence_facts=[],
        references_blob="[]",
    )
    assert result["answer_verdict"] == "uncertain"
    assert result["review_required"] is True
    assert result["scorer"]["name"] == "deterministic-answer-rules"


def test_question_variants_are_explicit_and_fail_closed() -> None:
    question = {
        "id": "q1",
        "question": "canonical",
        "question_variants": {"paraphrase": "rephrased"},
    }
    assert _question_variant(question, "paraphrase") == "rephrased"
    with pytest.raises(ValueError, match="has no 'evidence_first' variant"):
        _question_variant(question, "evidence_first")
