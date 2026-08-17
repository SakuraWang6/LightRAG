"""Metric-domain compatibility contract for comparing completed evaluations.

Compare and ranking are deliberately separate concepts:

- **Compare** answers "which metrics share meaning across these runs".  Two
  runs are comparable in a metric domain (retrieval / answer) when both
  actually evaluated that domain and share a non-empty question set.  Scorer
  differences only shrink the comparable metric set, they never block viewing
  the common metrics.
- **Ranking** requires strict benchmark identity: same dataset fingerprint,
  same per-domain question sets, same per-domain scorer inventories, same
  environment and repetition settings.  A run pair can therefore be
  comparable without being ranking-eligible.
"""

from __future__ import annotations

from typing import Any

RETRIEVAL_DOMAIN = "retrieval"
ANSWER_DOMAIN = "answer"
DOMAINS = (RETRIEVAL_DOMAIN, ANSWER_DOMAIN)


def _hashable(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        hash(value)
        return repr(value)
    except TypeError:
        return repr(value)


def _method(envelope: dict[str, Any], name: str) -> dict[str, Any] | None:
    for method in envelope.get("methods") or []:
        if isinstance(method, dict) and method.get("method") == name:
            return method
    return None


def _domain_case_ids(envelope: dict[str, Any], domain: str) -> set[str] | None:
    method_name = "answer" if domain == ANSWER_DOMAIN else "retrieval"
    method = _method(envelope, method_name)
    if method is None:
        return None
    rows = method.get("results") or []
    return {
        str(row.get("question_id")) for row in rows if row and row.get("question_id")
    }


def _scorer_inventory(
    envelope: dict[str, Any], domain: str
) -> tuple[tuple[str, str], ...] | None:
    key = "answer_scorers" if domain == ANSWER_DOMAIN else "retrieval_scorers"
    raw = envelope.get(key)
    if raw is None and domain == ANSWER_DOMAIN:
        raw = envelope.get("scorers")
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


def _domain_metric_keys(envelope: dict[str, Any], domain: str) -> set[str]:
    method_name = "answer" if domain == ANSWER_DOMAIN else "retrieval"
    method = _method(envelope, method_name)
    if method is None:
        return set()
    summary = method.get("summary") or {}
    return {
        str(key)
        for key, value in summary.items()
        if isinstance(value, (int, float, bool)) and value is not None
    }


def _strict_equality_mismatches(
    envelopes: list[dict[str, Any]],
) -> tuple[list[str], dict[str, Any]]:
    """Legacy whole-run equality view, kept for display purposes only."""
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
        "scorers": lambda run: (
            tuple(
                sorted(
                    (item.get("name"), item.get("version"))
                    for item in (run.get("answer_scorers") or run.get("scorers") or [])
                    if isinstance(item, dict)
                )
            )
            or None
        ),
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
    return sorted(mismatches), mismatches


def compare_contract(envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a metric-domain compatibility contract."""
    if len(envelopes) < 2:
        raise ValueError("at least two runs are required")

    domains: dict[str, dict[str, Any]] = {}
    for domain in DOMAINS:
        case_sets = [_domain_case_ids(run, domain) for run in envelopes]
        scorers = [_scorer_inventory(run, domain) for run in envelopes]
        available = [cases is not None for cases in case_sets]
        common_cases = (
            set.intersection(*[cases for cases in case_sets if cases is not None])
            if any(available)
            else set()
        )
        comparable = all(available) and len(common_cases) > 0
        reason: str | None = None
        if not all(available):
            reason = "not evaluated in every run"
        elif not common_cases:
            reason = "no shared question ids"
        elif domain == ANSWER_DOMAIN and (
            scorers[0] != scorers[1] or scorers[0] is None
        ):
            comparable = False
            reason = "answer scorer inventories differ"
        elif domain == RETRIEVAL_DOMAIN and (
            scorers[0] != scorers[1] or scorers[0] is None
        ):
            comparable = False
            reason = "retrieval scorer inventories differ"
        domains[domain] = {
            "available": available,
            "comparable": comparable,
            "comparable_cases": len(common_cases),
            "reason": reason,
        }

    common_metrics: dict[str, list[str]] = {}
    for domain in DOMAINS:
        if not domains[domain]["comparable"]:
            common_metrics[domain] = []
            continue
        key_sets = [_domain_metric_keys(run, domain) for run in envelopes]
        common_metrics[domain] = sorted(
            set.intersection(*key_sets) if key_sets else set()
        )

    dataset_fingerprints = [
        ((run.get("execution_manifest") or {}).get("dataset") or {}).get(
            "manifest_sha256"
        )
        for run in envelopes
    ]
    environment_versions = [
        ((run.get("execution_manifest") or {}).get("execution_unit") or {}).get(
            "profile"
        )
        for run in envelopes
    ]
    environment_configs = [
        ((run.get("execution_manifest") or {}).get("execution_unit") or {}).get(
            "configuration_fingerprint"
        )
        for run in envelopes
    ]
    repetitions = [
        (run.get("comparison_settings") or {}).get("repetitions", 1)
        for run in envelopes
    ]
    warmups = [
        (run.get("comparison_settings") or {}).get("warmups", 0) for run in envelopes
    ]

    def _domain_ranking(domain: str) -> dict[str, Any]:
        reasons: list[str] = []
        if len({_hashable(fp) for fp in dataset_fingerprints}) != 1:
            reasons.append("dataset fingerprint differs")
        if len({_hashable(v) for v in environment_versions}) != 1:
            reasons.append("environment version differs")
        if len({_hashable(v) for v in environment_configs}) != 1:
            reasons.append("environment configuration differs")
        if len(set(repetitions)) != 1 or len(set(warmups)) != 1:
            reasons.append("comparison repetitions/warmups differ")
        case_sets = [_domain_case_ids(run, domain) for run in envelopes]
        if not all(cases is not None for cases in case_sets):
            reasons.append(f"{domain} not evaluated in every run")
        elif any(cases != case_sets[0] for cases in case_sets[1:]):
            reasons.append(f"{domain} case set differs")
        scorers = [_scorer_inventory(run, domain) for run in envelopes]
        if not all(scorers):
            reasons.append(f"{domain} scorer inventory unavailable in some run")
        elif any(scorer != scorers[0] for scorer in scorers[1:]):
            reasons.append(f"{domain} scorer inventory differs")
        return {"eligible": not reasons, "reasons": reasons}

    ranking = {domain: _domain_ranking(domain) for domain in DOMAINS}
    appearing = [
        domain
        for domain in DOMAINS
        if any(_domain_case_ids(run, domain) is not None for run in envelopes)
    ]
    ranking_reasons = [
        reason for domain in appearing for reason in ranking[domain]["reasons"]
    ]

    metrics_unavailable: list[dict[str, Any]] = []
    for index, run in enumerate(envelopes):
        for domain in DOMAINS:
            if not domains[domain]["available"][index]:
                metrics_unavailable.append(
                    {
                        "run_index": index,
                        "run_id": run.get("run_id"),
                        "domain": domain,
                        "reason": "run did not evaluate this domain",
                    }
                )

    incompatible_fields, observed_values = _strict_equality_mismatches(envelopes)
    return {
        "comparable": any(domains[domain]["comparable"] for domain in DOMAINS),
        "domains": domains,
        "common_metrics": common_metrics,
        "ranking_permitted": not ranking_reasons,
        "ranking": ranking,
        "ranking_reasons": ranking_reasons,
        "metrics_unavailable": metrics_unavailable,
        "incompatible_fields": incompatible_fields,
        "observed_values": observed_values,
    }
