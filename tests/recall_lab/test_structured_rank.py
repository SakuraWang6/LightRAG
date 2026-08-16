"""Regression tests for R1 structured ranking and ranking audit helpers."""

from __future__ import annotations

import pytest

from lightrag.operate import apply_ranking_strategy
from lightrag.ranking.structured import _structured_rank
from memory_recall_lab.audit.ranking import _classify_case


def test_apply_ranking_strategy_default_is_identity(monkeypatch) -> None:
    monkeypatch.delenv("LIGHTRAG_RANKING_STRATEGY", raising=False)
    chunks = [{"content": "a", "chunk_id": "1"}, {"content": "b", "chunk_id": "2"}]
    assert apply_ranking_strategy("query", chunks) is chunks


def test_apply_ranking_strategy_structured_uses_tiers(monkeypatch) -> None:
    monkeypatch.setenv("LIGHTRAG_RANKING_STRATEGY", "structured")
    chunks = [
        {"content": "unrelated", "chunk_id": "x"},
        {
            "content": "Object Type: Table Row\nTable ID: tb-1\nFACT-00006 gold row",
            "chunk_id": "row",
        },
    ]
    ranked = apply_ranking_strategy("According to FACT-00006 in TBL-0003?", chunks)
    assert ranked[0]["chunk_id"] == "row"


def test_apply_ranking_strategy_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setenv("LIGHTRAG_RANKING_STRATEGY", "bm25")
    with pytest.raises(ValueError, match="must be one of"):
        apply_ranking_strategy("query", [{"content": "a"}])


def test_structured_rank_prefers_matching_row_view():
    query = "In TBL-0003, what is the Maximum value for the authoritative gold row?"
    chunks = [
        {
            "content": (
                "Object Type: Table Row\nTable ID: tb-1\nTitle: Table TBL-0003: "
                "latency thresholds\nSubsystem: Voltage Guard\nNominal Band: 3.1"
            ),
            "chunk_id": "wrong-row",
        },
        {
            "content": (
                "Object Type: Table Row\nTable ID: tb-1\nTitle: Table TBL-0003: "
                "latency thresholds\nSubsystem: FACT-00006\nNominal Band: "
                "gold-row : authoritative\nSafety Band: 33.75 : ms"
            ),
            "chunk_id": "gold-row",
        },
        {
            "content": (
                "Object Type: Table\nTable ID: tb-1\nTitle: Table TBL-0003: "
                "latency thresholds\nColumns: Subsystem | Nominal Band"
            ),
            "chunk_id": "table-view",
        },
    ]

    ranked = _structured_rank(query, chunks)
    assert [chunk["chunk_id"] for chunk in ranked[:2]] == ["gold-row", "wrong-row"]


def test_structured_rank_exact_fact_beats_table_row():
    query = "According to FACT-00006 in TBL-0003, what is the value?"
    chunks = [
        {
            "content": "Object Type: Table Row\nTable ID: tb-1\nTitle: Table TBL-0003",
            "chunk_id": "table-row",
        },
        {
            "content": "FACT-00006 authoritative maximum 33.75 ms",
            "chunk_id": "fact",
        },
    ]
    assert _structured_rank(query, chunks)[0]["chunk_id"] == "fact"


def test_structured_rank_unchanged_without_identifiers():
    chunks = [{"content": "a", "chunk_id": "1"}, {"content": "b", "chunk_id": "2"}]
    assert _structured_rank("no identifiers here", chunks) == chunks


def test_audit_classifies_same_table_wrong_row():
    query = "In TBL-0003, what is the Maximum value for the authoritative gold row?"
    candidates = [
        {
            "rank": 1,
            "matched_fact_ids": [],
            "content_excerpt": (
                "Object Type: Table Row Table ID: tb-1 Title: Table TBL-0003: "
                "latency thresholds Subsystem: Voltage Guard"
            ),
        },
        {
            "rank": 2,
            "matched_fact_ids": ["FACT-00006"],
            "content_excerpt": (
                "Object Type: Table Row Table ID: tb-1 Title: Table TBL-0003: "
                "latency thresholds Subsystem: FACT-00006 Nominal Band: "
                "gold-row : authoritative Safety Band: 33.75 : ms"
            ),
        },
    ]
    result = _classify_case(query, candidates)
    assert result["gold_rank"] == 2
    assert result["category"] == "same_table_wrong_row"
