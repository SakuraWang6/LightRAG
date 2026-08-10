from memory_eval_tests.experiments.comparison_stats import paired_case_deltas, summarize_arm


def test_summary_reports_latency_cost_and_insufficient_evidence():
    one = summarize_arm([{"latency_seconds": 1.0, "input_tokens": 100, "output_tokens": 20}])
    assert one["latency"]["evidence"] == "insufficient"
    many = summarize_arm(
        [{"latency_seconds": value, "input_tokens": 1_000_000, "output_tokens": 500_000} for value in (1.0, 2.0, 3.0)],
        input_cost_per_million=2, output_cost_per_million=4,
    )
    assert many["latency"]["p95"] == 3.0
    assert many["estimated_cost"] == 12.0


def test_paired_case_deltas_only_uses_shared_cases():
    deltas = paired_case_deltas(
        [
            {"label": "base", "results": [{"question_id": "q1", "exact_match": True}, {"question_id": "q2", "exact_match": False}]},
            {"label": "candidate", "results": [{"question_id": "q1", "exact_match": False}, {"question_id": "q3", "exact_match": True}]},
        ]
    )
    assert deltas == [
        {
            "metric": "exact_match",
            "baseline": "base",
            "candidate": "candidate",
            "case_count": 1,
            "mean_delta": -1.0,
            "wins": 0,
            "ties": 0,
            "losses": 1,
            "evidence": "insufficient",
        }
    ]
