"""Regression tests for the KG-context token budget in query assembly.

The mix-mode query context lists entity/relation descriptions before document
chunks.  A KG-heavy graph could consume the whole ``max_total_tokens`` budget
and collapse ``available_chunk_tokens`` toward zero, silently dropping
answer-bearing evidence chunks from the model-visible context.  These tests
pin the reserved chunk floor and the entity-first KG shrink.
"""

from __future__ import annotations

import json

from lightrag.base import QueryParam
from lightrag.operate import (
    _build_context_str,
    _minimum_chunk_budget,
    _truncate_kg_context_to_budget,
)
from lightrag.utils import Tokenizer, TokenizerInterface


class _CharTokenizer(TokenizerInterface):
    """1:1 char-per-token; deterministic, dependency-free tokenizer."""

    def encode(self, content: str) -> list[int]:
        return [ord(ch) for ch in content]

    def decode(self, tokens: list[int]) -> str:
        return "".join(chr(t) for t in tokens)


def _tok() -> Tokenizer:
    return Tokenizer("char", _CharTokenizer())


def test_minimum_chunk_budget_scales_with_max_total_tokens() -> None:
    assert _minimum_chunk_budget(8192) == 2048
    assert _minimum_chunk_budget(1000) == 250
    assert _minimum_chunk_budget(32768) == 2048
    assert _minimum_chunk_budget(0) == 0


def test_truncate_kg_context_to_budget_keeps_entities_first() -> None:
    tokenizer = _tok()
    entities = [
        {
            "entity": f"E-{i:02d}",
            "type": "concept",
            "description": "answer-bearing entity " * 3,
        }
        for i in range(20)
    ]
    relations = [
        {
            "entity1": f"E-{i:02d}",
            "entity2": f"E-{i + 1:02d}",
            "description": "edge " * 3,
        }
        for i in range(20)
    ]
    budget = 500
    kept_entities, kept_relations = __import__("asyncio").run(
        _truncate_kg_context_to_budget(
            entities_context=entities,
            relations_context=relations,
            max_kg_tokens=budget,
            tokenizer=tokenizer,
        )
    )
    assert kept_entities
    # Entities are prioritized over relations.
    assert len(kept_entities) > len(kept_relations)
    # The rendered KG text fits the budget.
    rendered = "\n".join(
        json.dumps(item, ensure_ascii=False) for item in kept_entities + kept_relations
    )
    assert len(tokenizer.encode(rendered)) <= budget


async def test_build_context_str_reserves_chunk_budget_floor(
    monkeypatch,
) -> None:
    tokenizer = _tok()
    captured: dict[str, int | None] = {"limit": None}

    async def fake_process_chunks_unified(
        query,
        unique_chunks,
        query_param,
        global_config,
        source_type="mixed",
        chunk_token_limit=None,
        progress_callback=None,
    ):
        captured["limit"] = chunk_token_limit
        return unique_chunks

    monkeypatch.setattr(
        "lightrag.operate.process_chunks_unified", fake_process_chunks_unified
    )
    monkeypatch.setattr(
        "lightrag.operate.render_chunks_context_text",
        lambda chunks: "\n".join(c.get("content", "") for c in chunks),
    )
    monkeypatch.setattr(
        "lightrag.operate.generate_reference_list_from_chunks",
        lambda chunks: (
            [{"reference_id": "1", "file_path": "doc.docx"}],
            [{**chunk, "reference_id": "1"} for chunk in chunks],
        ),
    )

    entities = [
        {
            "entity": f"E-{i:03d}",
            "type": "concept",
            "description": "long kg description " * 10,
            "created_at": "UNKNOWN",
            "file_path": "doc.docx",
        }
        for i in range(60)
    ]
    relations = [
        {
            "entity1": f"E-{i:03d}",
            "entity2": f"E-{i + 1:03d}",
            "description": "long relation description " * 10,
            "created_at": "UNKNOWN",
            "file_path": "doc.docx",
        }
        for i in range(60)
    ]
    chunks = [
        {
            "content": f"evidence chunk {i} with FACT-{i:05d}",
            "file_path": "doc.docx",
            "chunk_id": f"c{i}",
        }
        for i in range(5)
    ]
    query_param = QueryParam(max_total_tokens=12000)
    context, _raw = await _build_context_str(
        entities_context=entities,
        relations_context=relations,
        merged_chunks=chunks,
        query="what is the value?",
        query_param=query_param,
        global_config={"tokenizer": tokenizer, "max_total_tokens": 12000},
    )

    floor = _minimum_chunk_budget(12000)
    assert isinstance(captured["limit"], int)
    assert captured["limit"] >= floor
    # The evidence chunk content survived into the final context.
    assert "FACT-00003" in context
    # The KG was shrunk below what the full list would have consumed.
    assert "E-059" not in context
