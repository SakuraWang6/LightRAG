"""Deterministic review sampling and agreement reporting for answer scorers."""

from __future__ import annotations

import random
from collections import Counter
from typing import Any


def build_review_queue(
    rows: list[dict[str, Any]], *, sample_size: int = 20, seed: int = 13
) -> list[dict[str, Any]]:
    """Always retain uncertain cases, then fill a reproducible audit sample."""
    uncertain = [row for row in rows if row.get("review_required")]
    candidates = [row for row in rows if not row.get("review_required")]
    rng = random.Random(seed)
    rng.shuffle(candidates)
    selected = uncertain + candidates[: max(0, sample_size - len(uncertain))]
    return [
        {
            "question_id": row.get("question_id"),
            "automatic_verdict": row.get("answer_verdict"),
            "automatic_reason": (row.get("scorer") or {}).get("reason"),
            "answer": row.get("answer"),
            "expected": row.get("expected"),
            "scenario_labels": row.get("scenario_labels") or [],
        }
        for row in selected
    ]


def review_agreement(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Report agreement without treating either side as infallible ground truth."""
    comparable = [
        row
        for row in records
        if row.get("automatic_verdict") in {"pass", "fail", "uncertain"}
        and row.get("review_verdict") in {"pass", "fail", "uncertain"}
    ]
    matrix = Counter(
        (str(row["automatic_verdict"]), str(row["review_verdict"]))
        for row in comparable
    )
    disagreements = [row for row in comparable if row["automatic_verdict"] != row["review_verdict"]]
    return {
        "sample_count": len(comparable),
        "agreement_rate": (
            (len(comparable) - len(disagreements)) / len(comparable)
            if comparable
            else None
        ),
        "confusion": {
            f"automatic_{automatic}__review_{review}": count
            for (automatic, review), count in sorted(matrix.items())
        },
        "error_direction": {
            "automatic_false_positive": sum(
                row["automatic_verdict"] == "pass" and row["review_verdict"] == "fail"
                for row in disagreements
            ),
            "automatic_false_negative": sum(
                row["automatic_verdict"] == "fail" and row["review_verdict"] == "pass"
                for row in disagreements
            ),
            "uncertain_disagreement": sum(
                "uncertain" in {row["automatic_verdict"], row["review_verdict"]}
                for row in disagreements
            ),
        },
        "disagreement_count": len(disagreements),
    }
