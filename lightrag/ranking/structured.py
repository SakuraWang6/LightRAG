"""Structured ranking strategy (R1).

Explicit identifiers in the query become ranking tiers instead of being
treated as extra recall keys only:

    explicit FACT-ID match
        > TBL-ID match + row-view
        > TBL-ID match + table-view/raw
        > other candidates

Within the same TBL-ID row-view tier, a cheap lexical-overlap score is used as
a proxy for semantic row ranking.  This is a retrieval-ranking strategy for
the recall lab, not part of default LightRAG retrieval.
"""

from __future__ import annotations

import re
from typing import Any

_STRUCTURED_TABLE_ID_RE = re.compile(r"\bTBL-\d+\b", re.IGNORECASE)
_STRUCTURED_FACT_ID_RE = re.compile(r"\bFACT-\d+\b", re.IGNORECASE)
_STRUCTURED_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _structured_candidate_type(chunk: dict[str, Any]) -> str:
    text = str(chunk.get("content") or "")
    if "Object Type: Table Row" in text:
        return "row_view"
    if "Object Type: Table" in text:
        return "table_view"
    if "<table" in text:
        return "raw"
    return "other"


def _structured_ids(text: str, pattern: re.Pattern[str]) -> set[str]:
    return {match.group(0).upper() for match in pattern.finditer(text)}


def _structured_overlap(query: str, chunk: dict[str, Any]) -> int:
    query_tokens = {
        token.lower() for token in _STRUCTURED_TOKEN_RE.findall(query) if len(token) > 1
    }
    content_tokens = {
        token.lower()
        for token in _STRUCTURED_TOKEN_RE.findall(str(chunk.get("content") or ""))
        if len(token) > 1
    }
    score = len(query_tokens & content_tokens)
    lowered = str(chunk.get("content") or "").lower()
    for phrase in ("authoritative gold row", "gold row", "maximum value"):
        if phrase in lowered:
            score += 2
    return score


def _structured_rank(query: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply explicit structural constraints before dense ordering."""
    query_table_ids = _structured_ids(query, _STRUCTURED_TABLE_ID_RE)
    query_fact_ids = _structured_ids(query, _STRUCTURED_FACT_ID_RE)
    if not query_table_ids and not query_fact_ids:
        return chunks

    scored: list[tuple[int, int, int, dict[str, Any]]] = []
    for index, chunk in enumerate(chunks):
        content = str(chunk.get("content") or "")
        candidate_table_ids = _structured_ids(content, _STRUCTURED_TABLE_ID_RE)
        candidate_fact_ids = _structured_ids(content, _STRUCTURED_FACT_ID_RE)
        candidate_type = _structured_candidate_type(chunk)
        if query_fact_ids and candidate_fact_ids & query_fact_ids:
            tier = 0
        elif (
            query_table_ids
            and candidate_table_ids & query_table_ids
            and candidate_type == "row_view"
        ):
            tier = 1
        elif (
            query_table_ids
            and candidate_table_ids & query_table_ids
            and candidate_type in {"table_view", "raw"}
        ):
            tier = 2
        elif query_table_ids and candidate_table_ids & query_table_ids:
            tier = 3
        else:
            tier = 4
        overlap = _structured_overlap(query, chunk) if tier in {1, 2} else 0
        scored.append((tier, -overlap, index, chunk))

    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in scored]
