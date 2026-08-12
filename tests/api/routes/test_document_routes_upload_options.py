"""Upload-time ``process_options`` override plumbing for ``/documents/upload``.

The eval framework uploads source documents with an explicit selector (e.g.
``Fi`` to enable VLM image analysis).  This test pins that the selector is
forwarded through ``pipeline_index_file`` into ``apipeline_enqueue_documents``
so the pipeline stores it as the document's ``process_options`` and the VLM
analysis stage is no longer silently skipped.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest

_original_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
_dr = importlib.import_module("lightrag.api.routers.document_routes")
sys.argv = _original_argv

from lightrag.parser.routing import validate_process_options  # noqa: E402

pytestmark = pytest.mark.offline


class _RecordingRag:
    """Minimal stub whose ``apipeline_enqueue_documents`` records kwargs."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def apipeline_enqueue_documents(self, *args, **kwargs):
        self.calls.append(kwargs)
        return True


def _run(file_path: Path, override: str | None):
    rag = _RecordingRag()
    ok, track = asyncio.run(
        _dr.pipeline_enqueue_file(
            rag,
            file_path,
            "track-1",
            process_options_override=override,
        )
    )
    assert ok is True
    assert track == "track-1"
    return rag.calls[0]


def test_pipeline_enqueue_file_forwards_process_options_override(
    tmp_path: Path,
) -> None:
    document = tmp_path / "document.docx"
    document.write_bytes(b"x")
    captured = _run(document, "Fi")
    assert captured["process_options"] == "Fi"


def test_pipeline_enqueue_file_keeps_default_without_override(
    tmp_path: Path,
) -> None:
    document = tmp_path / "document.docx"
    document.write_bytes(b"x")
    captured = _run(document, None)
    assert captured["process_options"] == "F"


def test_process_options_selector_is_validated() -> None:
    assert validate_process_options("Fi") == []
    assert validate_process_options("Fit") == []
    assert validate_process_options("X") != []
