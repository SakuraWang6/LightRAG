"""Regression tests for table-atomic fixed-token chunking.

Parsed tables are serialised as ``<table ...>[[row...],[row...]]</table>``.
The plain token-window splitter used to cut this JSON at an arbitrary token
boundary, leaving unclosed arrays and separating an answer-bearing row from
the rest of the table (observed on the LONG-TBL-APP stress case).  The
table-aware path keeps tables atomic and splits oversized tables at JSON row
boundaries, so every emitted table piece is complete, valid markup.
"""

from __future__ import annotations

import json
import re

from lightrag.chunker import chunking_by_fixed_token
from lightrag.utils import Tokenizer, TokenizerInterface


class _CharTokenizer(TokenizerInterface):
    """1:1 char-per-token; ``decode(encode(x)) == x`` for verbatim windows."""

    def encode(self, content: str) -> list[int]:
        return [ord(ch) for ch in content]

    def decode(self, tokens: list[int]) -> str:
        return "".join(chr(t) for t in tokens)


def _tok() -> Tokenizer:
    return Tokenizer("char", _CharTokenizer())


def _table(rows: list[list[str]], table_id: str = "tb-test") -> str:
    body = json.dumps(rows, ensure_ascii=False)
    return f'<table id="{table_id}" format="json">{body}</table>'


def test_small_table_keeps_preceding_context_in_one_chunk() -> None:
    rows = [
        ["Parameter", "Nominal", "Maximum", "Unit"],
        ["Latency Alpha", "5.50", "33.75", "ms"],
        ["FACT-00006", "gold-row", "33.75", "ms"],
    ]
    content = "Intro paragraph.\n" + _table(rows) + "\nOutro paragraph."
    chunks = chunking_by_fixed_token(_tok(), content, chunk_token_size=400)
    table_chunks = [
        chunk["content"] for chunk in chunks if "<table" in chunk["content"]
    ]
    assert len(table_chunks) == 1
    assert table_chunks[0].startswith("Intro paragraph.")
    assert table_chunks[0].endswith("</table>")
    assert _table(rows) in table_chunks[0]


def test_oversized_table_splits_at_row_boundaries() -> None:
    rows: list[list[str]] = [["Row", "Scenario", "Latency", "Status"]]
    for index in range(1, 90):
        if index == 89:
            rows.append(["A-089", "appendix rollover stress", "62.99 ms", "FACT-00055"])
        else:
            rows.append(
                [
                    f"A-{index:03d}",
                    "appendix rollover stress",
                    f"20.{index:02d} ms",
                    "distractor",
                ]
            )
    content = (
        "Table LONG-TBL-APP: Appendix long-table stress case with many rows; "
        "FACT-00055 marks the authoritative final rollover latency.\n"
        + _table(rows, table_id="LONG-TBL-APP")
    )
    chunks = chunking_by_fixed_token(_tok(), content, chunk_token_size=240)

    table_chunks = [
        chunk["content"] for chunk in chunks if "<table" in chunk["content"]
    ]
    assert len(table_chunks) > 1, "the oversized table must be split"

    # Every table piece is a complete <table>...</table> element whose inner
    # JSON array parses, and no piece exceeds the token budget.
    for piece in table_chunks:
        assert "<table" in piece
        assert piece.rstrip().endswith("</table>")
        match = re.search(r"<table[^>]*>(.*)</table>", piece, flags=re.DOTALL)
        assert match is not None
        parsed = json.loads(match.group(1))
        assert isinstance(parsed, list) and parsed
        assert len(_tok().encode(piece)) <= 240

    # The answer-bearing row survives intact in a single piece.
    gold_rows = [
        piece
        for piece in table_chunks
        if '"62.99 ms"' in piece and "FACT-00055" in piece
    ]
    assert len(gold_rows) == 1
    assert '"A-089"' in gold_rows[0]
    # The title is copied into every table piece so the answer-bearing tail
    # piece is retrievable by a query that names the table.
    assert "Table LONG-TBL-APP" in gold_rows[0]


def test_text_between_tables_is_chunked_normally() -> None:
    rows = [["A", "1"], ["FACT-00001", "value"]]
    filler = "filler " * 300
    content = (
        "start "
        + _table(rows, "tb-1")
        + "\n"
        + filler
        + "\n"
        + _table(rows, "tb-2")
        + "\nend"
    )
    chunks = chunking_by_fixed_token(_tok(), content, chunk_token_size=200)
    assert chunks
    # Table pieces are atomic and filler text is split into windows.
    table_pieces = [c["content"] for c in chunks if "<table" in c["content"]]
    assert len(table_pieces) == 2
    assert all(c["tokens"] <= 200 for c in chunks)
    assert any(c["content"].startswith("filler") for c in chunks)
