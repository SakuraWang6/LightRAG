"""Deterministic compatibility contract for comparing completed evaluations."""

from __future__ import annotations

from typing import Any


def _scorer_contract(run: dict[str, Any]) -> tuple[tuple[str, str], ...] | None:
    """Return a stable scorer inventory, never treating an empty one as valid."""
    raw = run.get("scorers")
    if not isinstance(raw, list) or not raw:
        return None
    inventory: list[tuple[str, str]] = []
    for scorer in raw:
        if not isinstance(scorer, dict):
            return None
        name, version = scorer.get("name"), scorer.get("version")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(version, str)
            or not version
        ):
            return None
        inventory.append((name, version))
    return tuple(sorted(inventory))


def compare_contract(envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    """Return explicit non-comparable fields; callers must not rank on failure."""
    if len(envelopes) < 2:
        raise ValueError("at least two runs are required")
    fields = {
        "dataset_fingerprint": lambda run: (
            (run.get("execution_manifest") or {}).get("dataset") or {}
        ).get("manifest_sha256"),
        "case_set": lambda run: (
            sorted(selection.get("case_ids"))
            if isinstance(
                selection := (
                    (run.get("execution_manifest") or {}).get("case_selection")
                ),
                dict,
            )
            and isinstance(selection.get("case_ids"), list)
            else None
        ),
        "environment_version": lambda run: (
            (run.get("execution_manifest") or {}).get("execution_unit") or {}
        ).get("profile"),
        "environment_configuration": lambda run: (
            (run.get("execution_manifest") or {}).get("execution_unit") or {}
        ).get("configuration_fingerprint"),
        "evaluation_type": lambda run: (run.get("evaluation") or {}).get("id"),
        "scorers": _scorer_contract,
        "repetitions": lambda run: (run.get("comparison_settings") or {}).get(
            "repetitions", 1
        ),
        "warmups": lambda run: (run.get("comparison_settings") or {}).get("warmups", 0),
    }
    mismatches: dict[str, list[Any]] = {}
    for name, getter in fields.items():
        values = [getter(run) for run in envelopes]
        if any(value is None for value in values) or any(
            value != values[0] for value in values[1:]
        ):
            mismatches[name] = values
    completed = all(run.get("status") == "complete" for run in envelopes)
    if not completed:
        mismatches["run_status"] = [run.get("status") for run in envelopes]
    return {
        "comparable": not mismatches,
        "incompatible_fields": sorted(mismatches),
        "observed_values": mismatches,
        "ranking_permitted": not mismatches,
    }
