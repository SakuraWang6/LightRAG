"""Deterministic question sampling shared by the online runners.

Both retrieval and answer evaluation must evaluate the same question subset so
their numbers are comparable within one report.  The sampler is a deterministic
evenly-spaced stride (not a prefix), so a capped run does not bias toward the
questions at the front of the oracle.
"""

from __future__ import annotations

from typing import Any


def sample_evenly(items: list[Any], max_cases: int | None) -> list[Any]:
    """Return up to ``max_cases`` items spread evenly across the input.

    ``max_cases`` of ``None`` or ``<= 0`` returns the full list unchanged.  The
    selected indexes are deterministic and independent of item contents.
    """
    if max_cases is None or max_cases <= 0 or len(items) <= max_cases:
        return items
    if max_cases == 1:
        return [items[0]]
    step = (len(items) - 1) / (max_cases - 1)
    indexes = sorted({round(index * step) for index in range(max_cases)})
    return [items[index] for index in indexes]
