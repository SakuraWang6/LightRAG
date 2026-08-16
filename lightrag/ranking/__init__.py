"""Ranking strategy hooks for retrieval candidate ordering.

The default strategy is ``none`` (identity): LightRAG's retrieval behaviour is
exactly unchanged unless a run explicitly enables a strategy such as
``structured`` via ``LIGHTRAG_RANKING_STRATEGY``.
"""

from __future__ import annotations

import os
from typing import Any

from lightrag.ranking.structured import _structured_rank

_STRATEGIES = {"none", "structured"}


def apply_ranking_strategy(query: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the configured ranking strategy to a candidate pool.

    With ``LIGHTRAG_RANKING_STRATEGY`` unset or ``none`` the candidate list is
    returned unchanged, preserving default retrieval order.
    """
    strategy = os.environ.get("LIGHTRAG_RANKING_STRATEGY", "none").strip().lower()
    if strategy not in _STRATEGIES:
        raise ValueError(
            f"LIGHTRAG_RANKING_STRATEGY must be one of {sorted(_STRATEGIES)}, got {strategy!r}"
        )
    if strategy == "structured":
        return _structured_rank(query, chunks)
    return chunks
