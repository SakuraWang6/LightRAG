from memory_eval_tests.experiments.comparison_stats import summarize_arm


def test_summary_reports_latency_cost_and_insufficient_evidence():
    one = summarize_arm([{"latency_seconds": 1.0, "input_tokens": 100, "output_tokens": 20}])
    assert one["latency"]["evidence"] == "insufficient"
    many = summarize_arm(
        [{"latency_seconds": value, "input_tokens": 1_000_000, "output_tokens": 500_000} for value in (1.0, 2.0, 3.0)],
        input_cost_per_million=2, output_cost_per_million=4,
    )
    assert many["latency"]["p95"] == 3.0
    assert many["estimated_cost"] == 12.0
