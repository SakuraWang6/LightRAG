"""Deterministic compatibility contract for comparing completed evaluations."""

from __future__ import annotations

from typing import Any


def compare_contract(envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    """Return explicit non-comparable fields; callers must not rank on failure."""
    if len(envelopes) < 2:
        raise ValueError("at least two runs are required")
    fields = {
        "dataset_fingerprint": lambda run: ((run.get("execution_manifest") or {}).get("dataset") or {}).get("manifest_sha256"),
        "case_set": lambda run: sorted((run.get("launch_params") or {}).get("case_ids") or []),
        "environment_version": lambda run: ((run.get("execution_manifest") or {}).get("execution_unit") or {}).get("profile"),
        "environment_configuration": lambda run: ((run.get("execution_manifest") or {}).get("execution_unit") or {}).get("configuration_fingerprint"),
        "evaluation_type": lambda run: ((run.get("evaluation") or {}).get("id")),
        "scorer_version": lambda run: run.get("scorer_version"),
        "repetitions": lambda run: (run.get("comparison_settings") or {}).get("repetitions", 1),
        "warmups": lambda run: (run.get("comparison_settings") or {}).get("warmups", 0),
    }
    mismatches: dict[str, list[Any]] = {}
    for name, getter in fields.items():
        values = [getter(run) for run in envelopes]
        if any(value is None for value in values) or any(value != values[0] for value in values[1:]):
            mismatches[name] = values
    return {
        "comparable": not mismatches,
        "incompatible_fields": sorted(mismatches),
        "observed_values": mismatches,
        "ranking_permitted": not mismatches,
    }
