"""Deterministic quality/latency/cost summaries for fair comparison arms."""

from __future__ import annotations

import math
from statistics import mean, median, stdev
from typing import Any


def summarize_arm(samples: list[dict[str, Any]], *, input_cost_per_million: float | None = None, output_cost_per_million: float | None = None) -> dict[str, Any]:
    latencies = [float(item["latency_seconds"]) for item in samples if isinstance(item.get("latency_seconds"), (int, float))]
    successes = [item for item in samples if item.get("status", "success") == "success"]
    input_tokens = sum(float(item.get("input_tokens") or 0) for item in samples)
    output_tokens = sum(float(item.get("output_tokens") or 0) for item in samples)
    cost = None
    if input_cost_per_million is not None or output_cost_per_million is not None:
        cost = input_tokens * float(input_cost_per_million or 0) / 1_000_000 + output_tokens * float(output_cost_per_million or 0) / 1_000_000
    return {
        "sample_count": len(samples),
        "success_rate": len(successes) / len(samples) if samples else 0.0,
        "latency": _distribution(latencies),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost": cost,
    }


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"mean": None, "stddev": None, "p50": None, "p95": None, "confidence_interval": None, "evidence": "insufficient"}
    ordered = sorted(values)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    avg = mean(values)
    if len(values) < 2:
        return {"mean": avg, "stddev": None, "p50": median(values), "p95": ordered[p95_index], "confidence_interval": None, "evidence": "insufficient"}
    deviation = stdev(values)
    half_width = 1.96 * deviation / math.sqrt(len(values))
    return {"mean": avg, "stddev": deviation, "p50": median(values), "p95": ordered[p95_index], "confidence_interval": [avg - half_width, avg + half_width], "evidence": "estimated"}


def paired_case_deltas(
    methods: list[dict[str, Any]], *, metric: str = "exact_match"
) -> list[dict[str, Any]]:
    """Compare every arm with the first arm on their shared question IDs only."""
    if len(methods) < 2:
        return []
    baseline = _case_values(methods[0].get("results") or [], metric)
    if not baseline:
        return []
    output: list[dict[str, Any]] = []
    for candidate in methods[1:]:
        current = _case_values(candidate.get("results") or [], metric)
        shared = sorted(set(baseline) & set(current))
        if not shared:
            continue
        deltas = [current[case_id] - baseline[case_id] for case_id in shared]
        output.append(
            {
                "metric": metric,
                "baseline": methods[0].get("label") or methods[0].get("method"),
                "candidate": candidate.get("label") or candidate.get("method"),
                "case_count": len(shared),
                "mean_delta": mean(deltas),
                "wins": sum(delta > 0 for delta in deltas),
                "ties": sum(delta == 0 for delta in deltas),
                "losses": sum(delta < 0 for delta in deltas),
                "evidence": "estimated" if len(shared) >= 2 else "insufficient",
            }
        )
    return output


def _case_values(rows: list[dict[str, Any]], metric: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for row in rows:
        case_id = row.get("question_id")
        value = row.get(metric)
        if not isinstance(case_id, str) or isinstance(value, bool) is False and not isinstance(value, (int, float)):
            continue
        values[case_id] = float(value)
    return values
