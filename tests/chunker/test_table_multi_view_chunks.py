"""Config-driven table-view / row-view representation tests.

Multi-view retrieval representations are opt-in capability switches.  With no
environment set, the chunker must keep the stable atomic-table behaviour
unchanged; with ``LIGHTRAG_TABLE_VIEW`` / ``LIGHTRAG_TABLE_ROW_VIEW`` enabled,
it emits view chunks that still carry the table sidecar so every hit can be
resolved back to the full Evidence Object.
"""

from __future__ import annotations

import json

from lightrag.chunker import chunking_by_fixed_token
from lightrag.utils import Tokenizer, TokenizerInterface


class _CharTokenizer(TokenizerInterface):
    def encode(self, content: str) -> list[int]:
        return [ord(ch) for ch in content]

    def decode(self, tokens: list[int]) -> str:
        return "".join(chr(t) for t in tokens)


def _tok() -> Tokenizer:
    return Tokenizer("char", _CharTokenizer())


def _table(rows: list[list[str]], table_id: str = "tb-test") -> str:
    body = json.dumps(rows, ensure_ascii=False)
    return f'<table id="{table_id}" caption="Table TBL-0003: latency thresholds" format="json">{body}</table>'


def _rows() -> list[list[str]]:
    return [
        ["Parameter", "Nominal", "Maximum", "Unit"],
        ["Latency Alpha", "5.50", "33.75", "ms"],
        ["FACT-00006", "gold-row", "33.75", "ms"],
    ]


def test_default_table_path_is_unchanged() -> None:
    content = "Intro paragraph.\n" + _table(_rows()) + "\nOutro paragraph."
    chunks = chunking_by_fixed_token(_tok(), content, chunk_token_size=400)
    assert all("Object Type: Table Row" not in chunk["content"] for chunk in chunks)
    table_chunks = [chunk for chunk in chunks if "<table" in chunk["content"]]
    assert len(table_chunks) == 1
    assert table_chunks[0]["content"].startswith("Intro paragraph.")
    assert "sidecar" not in table_chunks[0]


def test_table_views_emit_view_chunks_with_sidecar(monkeypatch) -> None:
    monkeypatch.setenv("LIGHTRAG_TABLE_VIEW", "1")
    monkeypatch.setenv("LIGHTRAG_TABLE_ROW_VIEW", "1")
    content = "Intro paragraph.\n" + _table(_rows()) + "\nOutro paragraph."
    chunks = chunking_by_fixed_token(_tok(), content, chunk_token_size=400)
    table_views = [
        chunk
        for chunk in chunks
        if chunk["content"].startswith("Object Type: Table\n")
    ]
    row_views = [
        chunk for chunk in chunks if "Object Type: Table Row" in chunk["content"]
    ]
    assert len(table_views) == 1
    assert len(row_views) == len(_rows()) - 1
    for chunk in table_views + row_views:
        assert chunk.get("sidecar") == {
            "type": "table",
            "id": "tb-test",
            "refs": [{"type": "table", "id": "tb-test"}],
        }
    assert all("<table" not in chunk["content"] for chunk in table_views + row_views)


def test_row_view_only_skips_table_summary(monkeypatch) -> None:
    monkeypatch.setenv("LIGHTRAG_TABLE_VIEW", "0")
    monkeypatch.setenv("LIGHTRAG_TABLE_ROW_VIEW", "1")
    content = _table(_rows())
    chunks = chunking_by_fixed_token(_tok(), content, chunk_token_size=400)
    assert all(not chunk["content"].startswith("Object Type: Table\n") for chunk in chunks)
    assert len([c for c in chunks if "Object Type: Table Row" in c["content"]]) == 2


def test_long_table_rows_stay_row_safe_with_views(monkeypatch) -> None:
    monkeypatch.setenv("LIGHTRAG_TABLE_VIEW", "0")
    monkeypatch.setenv("LIGHTRAG_TABLE_ROW_VIEW", "1")
    rows: list[list[str]] = [["Row", "Scenario", "Latency", "Status"]]
    for index in range(1, 60):
        rows.append(
            [
                f"A-{index:03d}",
                "appendix rollover stress",
                f"20.{index:02d} ms",
                "FACT-00055" if index == 59 else "distractor",
            ]
        )
    content = "Table LONG-TBL-APP: many rows.\n" + _table(rows, "LONG-TBL-APP")
    chunks = chunking_by_fixed_token(_tok(), content, chunk_token_size=240)
    row_chunks = [
        chunk["content"] for chunk in chunks if "Object Type: Table Row" in chunk["content"]
    ]
    assert any("FACT-00055" in chunk for chunk in row_chunks)
    for chunk in row_chunks:
        assert chunk.count("Row: A-") <= 1
        assert "Table ID: LONG-TBL-APP" in chunk
