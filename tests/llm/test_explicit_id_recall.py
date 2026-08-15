"""Regression tests for explicit-identifier recall in chunk vector search.

When a query names an explicit fact identifier (e.g. ``FACT-GOV-00001``),
vector similarity can rank the chunk that contains it below unrelated
chunks, so the oracle evidence never reaches the model-visible context.
The chunk vector path now re-queries the store with each explicit
identifier and prepends the matches.
"""

from __future__ import annotations

import pytest

from lightrag.base import QueryParam
from lightrag.operate import _get_vector_context, _explicit_id_recall


class _FakeChunksVdb:
    """Records every query text; returns a chunk for FACT-GOV-00001."""

    cosine_better_than_threshold = 0.0

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.normal_chunks = [
            {
                "content": "unrelated flowchart chunk",
                "id": "c-unrelated",
                "file_path": "doc.docx",
            },
            {
                "content": "FACT-CROSS-00001: companion verification state",
                "id": "c-companion",
                "file_path": "companion.docx",
            },
        ]
        self.gov_chunk = {
            "content": "FACT-GOV-00001: Maya Chen owns business acceptance.",
            "id": "c-gov",
            "file_path": "doc.docx",
        }

    async def query(self, query: str, top_k: int, query_embedding=None):
        self.calls.append(query)
        if query == "FACT-GOV-00001":
            return [self.gov_chunk]
        return self.normal_chunks[:top_k]


class _FakeTextChunksDb:
    """Minimal KV facade exposing ``all_keys``/``get_by_ids`` for exact recall."""

    def __init__(self) -> None:
        self.data = {
            "c-gov": {
                "_id": "c-gov",
                "content": "FACT-GOV-00001: Maya Chen owns business acceptance.",
                "file_path": "doc.docx",
            },
            "c-other": {
                "_id": "c-other",
                "content": "unrelated chunk content",
                "file_path": "doc.docx",
            },
        }

    async def all_keys(self) -> list[str]:
        return list(self.data)

    async def get_by_ids(self, ids: list[str]) -> list[dict]:
        return [self.data[i] for i in ids if i in self.data]


def _param(**overrides) -> QueryParam:
    values = {"top_k": 5, "chunk_top_k": 5}
    values.update(overrides)
    return QueryParam(**values)


async def test_explicit_id_recall_extracts_identifiers() -> None:
    assert await _explicit_id_recall("no ids here", _FakeChunksVdb(), 5) == []
    recalled = await _explicit_id_recall(
        "what is FACT-GOV-00001 and the companion state?",
        _FakeChunksVdb(),
        5,
    )
    assert [c["chunk_id"] for c in recalled] == ["c-gov"]
    assert recalled[0]["source_type"] == "explicit_id"


async def test_get_vector_context_prepends_explicit_id_chunks() -> None:
    vdb = _FakeChunksVdb()
    chunks = await _get_vector_context(
        "Using the primary document, what is FACT-GOV-00001?",
        vdb,
        _param(),
    )
    assert "Using the primary document, what is FACT-GOV-00001?" in vdb.calls
    assert "FACT-GOV-00001" in vdb.calls
    # The explicitly-named identifier chunk is prepended to the normal results.
    assert chunks[0]["chunk_id"] == "c-gov"
    assert chunks[0]["content"] == vdb.gov_chunk["content"]
    assert chunks[-1]["chunk_id"] == "c-companion"


async def test_get_vector_context_unchanged_without_identifiers() -> None:
    vdb = _FakeChunksVdb()
    chunks = await _get_vector_context(
        "what is the calibration limit?", vdb, _param()
    )
    assert vdb.calls == ["what is the calibration limit?"]
    assert all(c["source_type"] == "vector" for c in chunks)


async def test_explicit_id_recall_when_normal_search_is_empty() -> None:
    class _EmptyNormalVdb(_FakeChunksVdb):
        async def query(self, query: str, top_k: int, query_embedding=None):
            self.calls.append(query)
            if query == "FACT-GOV-00001":
                return [self.gov_chunk]
            return []

    vdb = _EmptyNormalVdb()
    chunks = await _get_vector_context(
        "what is FACT-GOV-00001?", vdb, _param()
    )
    assert [c["chunk_id"] for c in chunks] == ["c-gov"]


async def test_explicit_id_recall_exact_match_fallback() -> None:
    class _NoHitVdb(_FakeChunksVdb):
        async def query(self, query: str, top_k: int, query_embedding=None):
            self.calls.append(query)
            return []

    vdb = _NoHitVdb()
    chunks = await _get_vector_context(
        "what is FACT-GOV-00001 and the companion state?",
        vdb,
        _param(),
        text_chunks_db=_FakeTextChunksDb(),
    )
    assert [c["chunk_id"] for c in chunks] == ["c-gov"]
    assert chunks[0]["source_type"] == "explicit_id_exact"
    assert "Maya Chen" in chunks[0]["content"]


async def test_explicit_id_recall_prefers_search_values_capability() -> None:
    class _SearchableChunksDb(_FakeTextChunksDb):
        async def search_values(self, substrings: list[str]) -> list[dict]:
            return [
                value
                for key, value in self.data.items()
                if any(substring in value.get("content", "") for substring in substrings)
            ]

    class _NoHitVdb(_FakeChunksVdb):
        async def query(self, query: str, top_k: int, query_embedding=None):
            self.calls.append(query)
            return []

    vdb = _NoHitVdb()
    chunks = await _get_vector_context(
        "what is FACT-GOV-00001?",
        vdb,
        _param(),
        text_chunks_db=_SearchableChunksDb(),
    )
    assert [chunk["chunk_id"] for chunk in chunks] == ["c-gov"]
    assert chunks[0]["source_type"] == "explicit_id_exact"
