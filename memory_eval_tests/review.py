"""Deterministic review sampling and agreement summaries for answer scorers."""

from __future__ import annotations

import random
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
