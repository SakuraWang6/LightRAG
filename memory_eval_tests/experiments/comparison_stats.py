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
