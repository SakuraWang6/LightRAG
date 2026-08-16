"""Fixed-size token-window chunking — the LightRAG default strategy.

Chunks the input text into windows of at most ``chunk_token_size`` tokens
with ``chunk_overlap_token_size`` of overlap between adjacent windows.
When ``split_by_character`` is supplied, the splitter first segments on
that delimiter and then either tokenizes each segment as-is
(``split_by_character_only=True``) or further sub-splits any segment
that exceeds the token cap.

Two entry points are exported:

  - :func:`chunking_by_token_size` — the **legacy 6-arg signature**
    used as the default value for :attr:`lightrag.LightRAG.chunking_func`.
    Kept for backward compatibility so externally-supplied chunking
    functions can continue to drop in unchanged.

  - :func:`chunking_by_fixed_token` — the same algorithm exposed under
    the **new file-chunker contract** (standard prefix
    ``(tokenizer, content, chunk_token_size)`` plus keyword-only
    knobs). Used by the file-based chunking dispatcher in
    ``process_single_document`` for ``doc_process_opts.chunking == "F"``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from lightrag.exceptions import ChunkTokenLimitExceededError
from lightrag.utils import Tokenizer, logger


_TABLE_MARKUP_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.DOTALL)
_TABLE_BODY_RE = re.compile(r"(<table\b[^>]*>)(.*)(</table>)", re.DOTALL)
_TABLE_ROW_SEP = "], ["
_TABLE_ROW_SPLIT_RE = re.compile(r"(?<=\])\,\s*(?=\[)")
_TABLE_TITLE_RE = re.compile(r"^(Table\s+\S+|表\s*\d+[：:])")
_TABLE_ID_RE = re.compile(r"<table\b[^>]*\bid=\"([^\"]+)\"")
_TABLE_CAPTION_RE = re.compile(r"""<table\b[^>]*\bcaption\s*=\s*["']([^"']+)["']""")


def _env_flag(name: str, default: bool = False) -> bool:
    """Read an experiment capability switch from the server environment.

    Capability switches are opt-in and default to the stable behaviour, so an
    unset variable always means "keep the current production representation".
    """
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _table_preceding_context_enabled() -> bool:
    return _env_flag("LIGHTRAG_TABLE_PRECEDING_CONTEXT", default=True)


def _table_structured_envelope_enabled() -> bool:
    return _env_flag("LIGHTRAG_TABLE_STRUCTURED_ENVELOPE")


def _table_view_enabled() -> bool:
    return _env_flag("LIGHTRAG_TABLE_VIEW")


def _table_row_view_enabled() -> bool:
    return _env_flag("LIGHTRAG_TABLE_ROW_VIEW")


def _table_sidecar(table_text: str) -> dict[str, Any] | None:
    """Return a sidecar pointing at the parsed table object, when identifiable."""
    match = _TABLE_ID_RE.search(table_text)
    if not match:
        return None
    table_id = match.group(1)
    return {"type": "table", "id": table_id, "refs": [{"type": "table", "id": table_id}]}


def _table_caption(table_text: str) -> str:
    match = _TABLE_CAPTION_RE.search(table_text)
    return match.group(1).strip() if match else ""


def _table_rows(table_text: str) -> list[list[Any]]:
    """Decode the JSON rows inside a parsed ``<table>`` tag."""
    match = _TABLE_BODY_RE.match(table_text)
    if not match:
        return []
    inner = match.group(2)
    if not (inner.startswith("[") and inner.endswith("]")):
        return []
    rows: list[list[Any]] = []
    for row_text in _TABLE_ROW_SPLIT_RE.split(inner[1:-1]):
        try:
            value = json.loads(row_text)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, list):
            rows.append(value)
    return rows


def _table_id(table_text: str) -> str:
    match = _TABLE_ID_RE.search(table_text)
    return match.group(1) if match else ""


def _table_columns(table_text: str) -> str:
    """Return a pipe-joined header list from a JSON ``<table>`` body."""
    rows = _table_rows(table_text)
    if not rows:
        return ""
    header = rows[0]
    return " | ".join(str(cell) for cell in header)


def _table_envelope(table_text: str, title_text: str = "") -> str:
    """Wrap a table in a structured context envelope for retrieval."""
    table_id = _table_id(table_text)
    title = (_table_title(title_text) or _table_caption(table_text)).strip()
    columns = _table_columns(table_text)
    lines = ["Object Type: Table"]
    if table_id:
        lines.append(f"Table ID: {table_id}")
    if title:
        lines.append(f"Title: {title}")
    if columns:
        lines.append(f"Columns: {columns}")
    return "\n".join(lines) + "\n\n" + table_text


def _table_views(
    table_text: str,
    prev_text: str,
    tokenizer: Tokenizer,
    chunk_token_size: int,
    *,
    table_view: bool = True,
    row_view: bool = True,
) -> list[dict[str, Any]]:
    """Build table-view and row-view retrieval chunks for one parsed table.

    The original atomic table is never replaced: every view chunk keeps the
    table sidecar so a retrieval hit can always be resolved back to the full
    Evidence Object.  Long tables stay row-safe: rows are never split and a
    pathological single row is split only at cell-group boundaries.
    """
    rows = _table_rows(table_text)
    if not rows:
        return [
            {
                "content": table_text,
                "tokens": len(tokenizer.encode(table_text)),
                "sidecar": _table_sidecar(table_text),
            }
        ]

    table_id = _table_id(table_text)
    title = (_table_title(prev_text) or _table_caption(table_text)).strip()
    header = rows[0]
    header_text = " | ".join(str(cell) for cell in header)
    views: list[dict[str, Any]] = []
    if table_view:
        table_lines = ["Object Type: Table"]
        if table_id:
            table_lines.append(f"Table ID: {table_id}")
        if title:
            table_lines.append(f"Title: {title}")
        table_lines.append(f"Columns: {header_text}")
        table_lines.append(f"This table contains {max(0, len(rows) - 1)} data rows.")
        views.append(
            {
                "content": "\n".join(table_lines),
                "tokens": len(tokenizer.encode("\n".join(table_lines))),
                "sidecar": _table_sidecar(table_text),
            }
        )

    if not row_view:
        return views
    for row in rows[1:]:
        cells: list[str] = []
        for index, value in enumerate(row):
            label = str(header[index]) if index < len(header) else f"Column {index + 1}"
            cells.append(f"{label}: {value}")
        row_lines = ["Object Type: Table Row"]
        if table_id:
            row_lines.append(f"Table ID: {table_id}")
        if title:
            row_lines.append(f"Title: {title}")
        row_lines.extend(cells)
        row_view_text = "\n".join(row_lines)
        tokens = len(tokenizer.encode(row_view_text))
        if tokens <= chunk_token_size:
            views.append(
                {
                    "content": row_view_text,
                    "tokens": tokens,
                    "sidecar": _table_sidecar(table_text),
                }
            )
            continue
        # A single pathological row larger than the token budget is split into
        # cell groups while still carrying the table identity in every piece.
        current_lines = row_lines[:3]
        current_tokens = len(tokenizer.encode("\n".join(current_lines)))
        for cell in cells:
            candidate = "\n".join(current_lines + [cell])
            candidate_tokens = len(tokenizer.encode(candidate))
            if current_lines[3:] and candidate_tokens > chunk_token_size:
                views.append(
                    {
                        "content": "\n".join(current_lines),
                        "tokens": current_tokens,
                        "sidecar": _table_sidecar(table_text),
                    }
                )
                current_lines = row_lines[:3] + [cell]
                current_tokens = len(tokenizer.encode("\n".join(current_lines)))
            else:
                current_lines.append(cell)
                current_tokens = candidate_tokens
        if len(current_lines) > 3:
            views.append(
                {
                    "content": "\n".join(current_lines),
                    "tokens": current_tokens,
                    "sidecar": _table_sidecar(table_text),
                }
            )
    return views


def _split_table_pieces(
    table_text: str,
    tokenizer: Tokenizer,
    chunk_token_size: int,
    title: str = "",
    *,
    structured_envelope: bool = False,
) -> list[dict[str, Any]]:
    """Split one oversized ``<table>`` block into row-atomic pieces.

    A parsed table is serialised as ``<table ...>[[row...],[row...]]</table>``.
    The old fixed-token window could cut this JSON at an arbitrary token
    boundary, leaving unclosed arrays and separating the answer-bearing row
    from the rest of the table.  Pieces are rebuilt as complete
    ``<table>[...rows...]</table>`` units, so every piece is valid markup and
    the token cap is respected without ever splitting inside a row.
    """
    sidecar = _table_sidecar(table_text)
    match = _TABLE_BODY_RE.match(table_text)
    if not match:
        return [
            {
                "content": table_text,
                "tokens": len(tokenizer.encode(table_text)),
                "sidecar": sidecar,
            }
        ]
    open_tag, inner, close_tag = match.groups()
    if not (inner.startswith("[") and inner.endswith("]")):
        return [
            {
                "content": table_text,
                "tokens": len(tokenizer.encode(table_text)),
                "sidecar": sidecar,
            }
        ]
    rows = _TABLE_ROW_SPLIT_RE.split(inner[1:-1])

    def wrap(row_slice: list[str]) -> str:
        # Rows already carry their own brackets; a plain comma separator
        # rebuilds a valid ``[[row...],[row...]]`` array.
        body = "[" + ", ".join(row_slice) + "]"
        if structured_envelope:
            return _table_envelope(open_tag + body + close_tag, title)
        return title + open_tag + body + close_tag

    pieces: list[dict[str, Any]] = []
    current_rows: list[str] = []
    current_tokens = 0
    for row in rows:
        candidate = wrap(current_rows + [row])
        candidate_tokens = len(tokenizer.encode(candidate))
        if current_rows and candidate_tokens > chunk_token_size:
            pieces.append(
                {
                    "content": wrap(current_rows),
                    "tokens": current_tokens,
                    "sidecar": sidecar,
                }
            )
            current_rows = [row]
            current_tokens = len(tokenizer.encode(wrap([row])))
        else:
            current_rows.append(row)
            current_tokens = candidate_tokens
    if current_rows:
        pieces.append(
            {
                "content": wrap(current_rows),
                "tokens": current_tokens,
                "sidecar": sidecar,
            }
        )
    return pieces


def _table_title(prev_text: str) -> str:
    """Return the caption/title line preceding a table, if any.

    A split table's tail pieces carry only raw JSON rows; without the title
    (e.g. ``Table LONG-TBL-APP: ...``) they are invisible to retrieval for a
    query that names the table.  Copying the title into every piece keeps the
    answer-bearing tail piece retrievable.
    """
    if not prev_text:
        return ""
    for line in reversed(prev_text.splitlines()):
        line = line.strip()
        if not line:
            continue
        if _TABLE_TITLE_RE.match(line):
            return line + "\n"
        return ""
    return ""


def _table_with_preceding_context(
    *,
    table_text: str,
    table_start: int,
    table_end: int,
    prev_text: str,
    prev_start: int,
    tokenizer: Tokenizer,
    chunk_token_size: int,
) -> tuple[str, int, int]:
    """Return a small table chunk that keeps a suffix of its preceding text.

    A lone JSON table is a poor embedding target; including the immediately
    preceding prose restores the surrounding semantic context without breaking
    source-span validation.  The returned ``(content, start, tokens)`` is an
    exact contiguous substring ending at the table.
    """
    table_tokens = tokenizer.encode(table_text)
    if not prev_text:
        return table_text, table_start, len(table_tokens)
    budget_tokens = max(0, chunk_token_size - len(table_tokens))
    if budget_tokens == 0:
        return table_text, table_start, len(table_tokens)
    prev_tokens = tokenizer.encode(prev_text)
    suffix_tokens = prev_tokens[-budget_tokens:]
    suffix = tokenizer.decode(suffix_tokens)
    suffix_start = prev_text.rfind(suffix)
    if suffix_start < 0:
        return table_text, table_start, len(table_tokens)
    suffix = prev_text[suffix_start:]
    start = prev_start + suffix_start
    combined = suffix + table_text
    combined_tokens = tokenizer.encode(combined)
    if len(combined_tokens) > chunk_token_size:
        return table_text, table_start, len(table_tokens)
    return combined, start, len(combined_tokens)


def _table_aware_segments(content: str) -> list[tuple[bool, str, int, int]]:
    """Return ``(is_table, text, start, end)`` segments for table markup."""
    segments: list[tuple[bool, str, int, int]] = []
    cursor = 0
    for match in _TABLE_MARKUP_RE.finditer(content):
        if match.start() > cursor:
            segments.append((False, content[cursor : match.start()], cursor, match.start()))
        segments.append((True, match.group(0), match.start(), match.end()))
        cursor = match.end()
    if cursor < len(content):
        segments.append((False, content[cursor:], cursor, len(content)))
    return segments


def _trimmed_span(content: str, start: int, end: int) -> tuple[int, int]:
    """Return the source span after applying the chunker's ``.strip()``."""
    start = max(0, min(start, len(content)))
    end = max(start, min(end, len(content)))
    while start < end and content[start].isspace():
        start += 1
    while end > start and content[end - 1].isspace():
        end -= 1
    return start, end


def _source_span(content: str, start: int, end: int) -> dict[str, int] | None:
    start, end = _trimmed_span(content, start, end)
    if start >= end:
        return None
    return {"start": start, "end": end}


def _token_window_source_span(
    tokenizer: Tokenizer,
    content: str,
    tokens: list[int],
    start_token: int,
    end_token: int,
    *,
    anchor: tuple[int, int],
) -> tuple[dict[str, int] | None, tuple[int, int]]:
    """Map a decoded token window back to its exact source span.

    ``anchor`` is the previous window's *verified* ``(start_token, start_char)``.
    Window starts are monotonically increasing, so instead of re-decoding the whole
    ``tokens[:start_token]`` prefix (O(N) per window → O(N²) overall) we decode only
    the delta ``tokens[anchor_token:start_token]`` (≈ one chunking step) to predict
    the start char. The predicted offset is then verified against ``content`` exactly
    as a full prefix decode would be: byte-level BPE decode is non-concatenative at a
    multi-byte UTF-8 boundary, so a delta can be off by the few chars of one split
    char — the ±32 ``find`` fallback corrects that, and re-anchoring on the verified
    position each call keeps the error from accumulating. Net cost is O(N) total
    while the located span stays byte-exact.

    Returns ``(span, new_anchor)``. On an unlocatable (U+FFFD) window the span is
    ``None`` and the anchor is returned unchanged so the next window still predicts
    from the last verified position.
    """
    anchor_token, anchor_char = anchor
    window = tokenizer.decode(tokens[start_token:end_token])
    if start_token >= anchor_token:
        start = anchor_char + len(tokenizer.decode(tokens[anchor_token:start_token]))
    else:  # non-monotonic caller (not expected) — fall back to a full prefix decode
        start = len(tokenizer.decode(tokens[:start_token]))
    end = start + len(window)
    if content[start:end] != window:
        found = content.find(
            window,
            max(0, start - 32),
            min(len(content), end + 32 + len(window)),
        )
        if found < 0:
            return None, anchor
        start = found
        end = found + len(window)
    return _source_span(content, start, end), (start_token, start)


def _make_chunk(
    *,
    content: str,
    tokens: int,
    order: int,
    source_span: dict[str, int] | None,
    emit_source_span: bool,
    sidecar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "tokens": tokens,
        "content": content.strip(),
        "chunk_order_index": order,
    }
    if emit_source_span and source_span is not None:
        item["_source_span"] = source_span
    if sidecar is not None:
        item["sidecar"] = sidecar
    return item


def _window_step(chunk_token_size: int, chunk_overlap_token_size: int) -> int:
    """Token-window stride for the sliding-window chunk loops.

    When overlap >= size the stride is <= 0, which makes ``range()`` yield an
    empty sequence (dropping the whole segment silently) or raise the opaque
    ``range() arg 3 must not be zero``. Fail closed with the same invariant the
    API-boundary validators enforce.
    """
    if chunk_overlap_token_size >= chunk_token_size:
        raise ValueError(
            f"chunk_overlap_token_size ({chunk_overlap_token_size}) must be < "
            f"chunk_token_size ({chunk_token_size})"
        )
    return chunk_token_size - chunk_overlap_token_size


def chunking_by_token_size(
    tokenizer: Tokenizer,
    content: str,
    split_by_character: str | None = None,
    split_by_character_only: bool = False,
    chunk_overlap_token_size: int = 100,
    chunk_token_size: int = 1200,
    *,
    _emit_source_span: bool = False,
) -> list[dict[str, Any]]:
    """Legacy 6-arg fixed-token chunker (default for ``LightRAG.chunking_func``).

    Signature is preserved for backward compatibility with externally
    supplied ``chunking_func`` implementations. New file-based chunking
    dispatch uses :func:`chunking_by_fixed_token` instead.
    """
    tokens = tokenizer.encode(content)
    results: list[dict[str, Any]] = []
    if split_by_character:
        raw_chunks = content.split(split_by_character)
        raw_spans: list[tuple[int, int]] = []
        cursor = 0
        for raw_chunk in raw_chunks:
            start = cursor
            end = start + len(raw_chunk)
            raw_spans.append((start, end))
            cursor = end + len(split_by_character)
        new_chunks = []
        if split_by_character_only:
            for chunk, (chunk_start, chunk_end) in zip(raw_chunks, raw_spans):
                _tokens = tokenizer.encode(chunk)
                if len(_tokens) > chunk_token_size:
                    logger.warning(
                        "Chunk split_by_character exceeds token limit: len=%d limit=%d",
                        len(_tokens),
                        chunk_token_size,
                    )
                    raise ChunkTokenLimitExceededError(
                        chunk_tokens=len(_tokens),
                        chunk_token_limit=chunk_token_size,
                        chunk_preview=chunk[:120],
                    )
                span = (
                    _source_span(content, chunk_start, chunk_end)
                    if _emit_source_span
                    else None
                )
                new_chunks.append((len(_tokens), chunk, span))
        else:
            for chunk, (chunk_start, chunk_end) in zip(raw_chunks, raw_spans):
                _tokens = tokenizer.encode(chunk)
                if len(_tokens) > chunk_token_size:
                    # Anchor is chunk-relative (offsets are shifted by chunk_start
                    # below), so it resets per split-by-character segment.
                    anchor = (0, 0)
                    for start in range(
                        0,
                        len(_tokens),
                        _window_step(chunk_token_size, chunk_overlap_token_size),
                    ):
                        end_token = min(start + chunk_token_size, len(_tokens))
                        chunk_content = tokenizer.decode(_tokens[start:end_token])
                        span = None
                        if _emit_source_span:
                            span, anchor = _token_window_source_span(
                                tokenizer,
                                chunk,
                                _tokens,
                                start,
                                end_token,
                                anchor=anchor,
                            )
                        if span is not None:
                            span = {
                                "start": chunk_start + span["start"],
                                "end": chunk_start + span["end"],
                            }
                        new_chunks.append(
                            (
                                min(chunk_token_size, len(_tokens) - start),
                                chunk_content,
                                span,
                            )
                        )
                else:
                    span = (
                        _source_span(content, chunk_start, chunk_end)
                        if _emit_source_span
                        else None
                    )
                    new_chunks.append((len(_tokens), chunk, span))
        for index, (_len, chunk, span) in enumerate(new_chunks):
            results.append(
                _make_chunk(
                    content=chunk,
                    tokens=_len,
                    order=index,
                    source_span=span,
                    emit_source_span=_emit_source_span,
                )
            )
    else:
        if _TABLE_MARKUP_RE.search(content):
            # Table-aware path: keep parsed tables atomic and split oversized
            # tables at JSON row boundaries instead of arbitrary token windows.
            order = 0
            prev_text_segment = ""
            prev_text_start = 0
            table_view_on = _table_view_enabled()
            row_view_on = _table_row_view_enabled()
            emit_views = table_view_on or row_view_on
            structured_envelope = _table_structured_envelope_enabled()
            preceding_context = _table_preceding_context_enabled()
            for is_table, segment, seg_start, seg_end in _table_aware_segments(content):
                if not is_table:
                    prev_text_segment = segment
                    prev_text_start = seg_start
                segment_tokens = tokenizer.encode(segment)
                if is_table and emit_views:
                    for view in _table_views(
                        segment,
                        prev_text_segment,
                        tokenizer,
                        chunk_token_size,
                        table_view=table_view_on,
                        row_view=row_view_on,
                    ):
                        results.append(
                            _make_chunk(
                                content=view["content"],
                                tokens=view["tokens"],
                                order=order,
                                source_span=None,
                                emit_source_span=_emit_source_span,
                                sidecar=view.get("sidecar"),
                            )
                        )
                        order += 1
                    continue
                if len(segment_tokens) <= chunk_token_size:
                    if is_table:
                        if structured_envelope:
                            segment_content = _table_envelope(
                                segment, prev_text_segment
                            )
                            span_start = -1
                            segment_token_count = len(tokenizer.encode(segment_content))
                        elif preceding_context:
                            segment_content, span_start, segment_token_count = (
                                _table_with_preceding_context(
                                    table_text=segment,
                                    table_start=seg_start,
                                    table_end=seg_end,
                                    prev_text=prev_text_segment,
                                    prev_start=prev_text_start,
                                    tokenizer=tokenizer,
                                    chunk_token_size=chunk_token_size,
                                )
                            )
                        else:
                            segment_content = segment
                            span_start = seg_start
                            segment_token_count = len(segment_tokens)
                    else:
                        segment_content = segment
                        span_start = seg_start
                        segment_token_count = len(segment_tokens)
                    results.append(
                        _make_chunk(
                            content=segment_content,
                            tokens=segment_token_count,
                            order=order,
                            source_span=(
                                _source_span(content, span_start, seg_end)
                                if _emit_source_span and span_start >= 0
                                else None
                            ),
                            emit_source_span=_emit_source_span,
                            sidecar=(
                                _table_sidecar(segment)
                                if is_table and structured_envelope
                                else None
                            ),
                        )
                    )
                    order += 1
                elif is_table:
                    title = (
                        _table_title(prev_text_segment)
                        if preceding_context or structured_envelope
                        else ""
                    )
                    for piece in _split_table_pieces(
                        segment,
                        tokenizer,
                        chunk_token_size,
                        title=title,
                        structured_envelope=structured_envelope,
                    ):
                        results.append(
                            _make_chunk(
                                content=piece["content"],
                                tokens=piece["tokens"],
                                order=order,
                                source_span=(
                                    None
                                ),
                                emit_source_span=_emit_source_span,
                                sidecar=piece.get("sidecar"),
                            )
                        )
                        order += 1
                else:
                    anchor = (0, 0)
                    for start in range(
                        0,
                        len(segment_tokens),
                        _window_step(chunk_token_size, chunk_overlap_token_size),
                    ):
                        end = min(start + chunk_token_size, len(segment_tokens))
                        chunk_content = tokenizer.decode(segment_tokens[start:end])
                        span = None
                        if _emit_source_span:
                            span, anchor = _token_window_source_span(
                                tokenizer,
                                segment,
                                segment_tokens,
                                start,
                                end,
                                anchor=anchor,
                            )
                            if span is not None:
                                span = {
                                    "start": seg_start + span["start"],
                                    "end": seg_start + span["end"],
                                }
                        results.append(
                            _make_chunk(
                                content=chunk_content,
                                tokens=min(chunk_token_size, len(segment_tokens) - start),
                                order=order,
                                source_span=span,
                                emit_source_span=_emit_source_span,
                            )
                        )
                        order += 1
        else:
            anchor = (0, 0)
            for index, start in enumerate(
                range(
                    0,
                    len(tokens),
                    _window_step(chunk_token_size, chunk_overlap_token_size),
                )
            ):
                end = min(start + chunk_token_size, len(tokens))
                chunk_content = tokenizer.decode(tokens[start:end])
                span = None
                if _emit_source_span:
                    span, anchor = _token_window_source_span(
                        tokenizer, content, tokens, start, end, anchor=anchor
                    )
                results.append(
                    _make_chunk(
                        content=chunk_content,
                        tokens=min(chunk_token_size, len(tokens) - start),
                        order=index,
                        source_span=span,
                        emit_source_span=_emit_source_span,
                    )
                )
    return results


def chunking_by_fixed_token(
    tokenizer: Tokenizer,
    content: str,
    chunk_token_size: int = 1200,
    *,
    chunk_overlap_token_size: int = 100,
    split_by_character: str | None = None,
    split_by_character_only: bool = False,
    _emit_source_span: bool = False,
) -> list[dict[str, Any]]:
    """Fixed-token chunker — file-chunker contract for the ``"F"`` strategy.

    Implements the same fixed-window algorithm as
    :func:`chunking_by_token_size`, exposed under the standard
    file-chunker signature ``(tokenizer, content, chunk_token_size, *,
    <strategy kwargs>)`` so the file-based chunking dispatcher in
    ``process_single_document`` can call every strategy uniformly.
    """
    return chunking_by_token_size(
        tokenizer,
        content,
        split_by_character=split_by_character,
        split_by_character_only=split_by_character_only,
        chunk_overlap_token_size=chunk_overlap_token_size,
        chunk_token_size=chunk_token_size,
        _emit_source_span=_emit_source_span,
    )
