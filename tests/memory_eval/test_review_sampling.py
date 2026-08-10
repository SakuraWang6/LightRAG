from memory_eval_tests.online.review import build_review_queue, review_agreement


def test_review_queue_prioritizes_uncertain_cases_deterministically() -> None:
    rows = [
        {"question_id": "q1", "review_required": False, "answer_verdict": "pass"},
        {"question_id": "q2", "review_required": True, "answer_verdict": "uncertain"},
        {"question_id": "q3", "review_required": False, "answer_verdict": "fail"},
    ]
    queue = build_review_queue(rows, sample_size=1, seed=42)
    assert [item["question_id"] for item in queue] == ["q2"]


def test_review_agreement_reports_error_direction() -> None:
    report = review_agreement(
        [
            {"automatic_verdict": "pass", "review_verdict": "fail"},
            {"automatic_verdict": "fail", "review_verdict": "pass"},
            {"automatic_verdict": "uncertain", "review_verdict": "pass"},
            {"automatic_verdict": "pass", "review_verdict": "pass"},
        ]
    )
    assert report["agreement_rate"] == 0.25
    assert report["error_direction"] == {
        "automatic_false_positive": 1,
        "automatic_false_negative": 1,
        "uncertain_disagreement": 1,
    }
