"""Tests for CLI HTTP authentication parity with the WebUI client."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_eval_tests.common.http import auth_headers, post_json, upload_file

pytestmark = pytest.mark.offline


class _FakeResponse:
    status = 200

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def _header(captured: dict, name: str) -> str:
    lowered = name.lower()
    return next(
        value for key, value in captured["headers"].items() if key.lower() == lowered
    )


def test_auth_headers_combine_api_key_and_bearer() -> None:
    assert auth_headers(api_key="secret", access_token="token") == {
        "X-API-Key": "secret",
        "Authorization": "Bearer token",
    }
    assert auth_headers() == {}


def test_post_json_sends_auth_headers(monkeypatch) -> None:
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["headers"] = {key: value for key, value in request.header_items()}
        captured["url"] = request.full_url
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(
        "memory_eval_tests.common.http.urllib.request.urlopen",
        fake_urlopen,
    )
    result = post_json(
        "http://api.test/query",
        {"query": "q"},
        api_key="k",
        access_token="t",
    )
    assert result == {"ok": True}
    assert _header(captured, "X-API-Key") == "k"
    assert _header(captured, "Authorization") == "Bearer t"


def test_upload_file_sends_auth_headers(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}
    source = tmp_path / "doc.docx"
    source.write_bytes(b"doc")

    def fake_urlopen(request, timeout=None):
        captured["headers"] = {key: value for key, value in request.header_items()}
        return _FakeResponse(b'{"status": "success"}')

    monkeypatch.setattr(
        "memory_eval_tests.common.http.urllib.request.urlopen",
        fake_urlopen,
    )
    result = upload_file(source, "http://api.test/documents/upload", api_key="k")
    assert result["status"] == "success"
    assert _header(captured, "X-API-Key") == "k"
    assert "multipart/form-data; boundary=" in _header(captured, "Content-Type")


def test_retrieval_eval_forwards_credentials(monkeypatch, tmp_path: Path) -> None:
    from memory_eval_tests.online.retrieval_eval import evaluate_api

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "oracle.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "id": "Q1",
                        "question": "Q?",
                        "question_type": "direct_numeric",
                        "expected_behavior": "answer",
                        "evidence_fact_ids": ["FACT-1"],
                    }
                ],
                "facts": [{"fact_id": "FACT-1", "answer": "42", "expected_text": "42"}],
            }
        ),
        encoding="utf-8",
    )
    seen: dict = {}

    def fake_post(url, payload, **kwargs):
        seen.update(kwargs)
        return {
            "status": "ok",
            "data": {"references": [{"file_path": "a.docx", "content": ["no match"]}]},
            "metadata": {},
        }

    monkeypatch.setattr(
        "memory_eval_tests.online.retrieval_eval._post_json",
        fake_post,
    )
    evaluate_api(
        dataset_source=str(dataset),
        rag_api_url="http://api.test",
        api_key="k",
        access_token="t",
    )
    assert seen == {"api_key": "k", "access_token": "t"}


def test_index_runner_forwards_credentials(monkeypatch, tmp_path: Path) -> None:
    from memory_eval_tests.online.index_runner import upload_dataset_files

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "dataset_id": "d",
                "files": [{"name": "doc.docx", "format": "docx", "status": "created"}],
            }
        ),
        encoding="utf-8",
    )
    (dataset / "doc.docx").write_bytes(b"doc")
    seen: dict = {}

    def fake_upload(path, url, **kwargs):
        seen.update(kwargs)
        return {"status": "success"}

    monkeypatch.setattr(
        "memory_eval_tests.online.index_runner._upload_file",
        fake_upload,
    )
    upload_dataset_files(
        dataset_source=str(dataset),
        rag_api_url="http://api.test",
        api_key="k",
        access_token="t",
    )
    assert seen == {"api_key": "k", "access_token": "t"}


def test_index_runner_reuses_confirmed_content_hashes(monkeypatch, tmp_path: Path) -> None:
    from memory_eval_tests.online.index_runner import _sha256, upload_dataset_files

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(
        json.dumps(
            {"dataset_id": "d", "files": [{"name": "doc.docx", "format": "docx", "status": "created"}]}
        ),
        encoding="utf-8",
    )
    document = dataset / "doc.docx"
    document.write_bytes(b"already processed")
    monkeypatch.setattr(
        "memory_eval_tests.online.index_runner._upload_file",
        lambda *_args, **_kwargs: pytest.fail("confirmed file must not be uploaded twice"),
    )
    result = upload_dataset_files(
        dataset_source=str(dataset),
        rag_api_url="http://api.test",
        wait=True,
        confirmed_hashes={_sha256(document)},
    )
    assert result["passed"] is True
    assert result["uploaded"][0]["reused"] is True
    assert result["uploaded"][0]["content_sha256"] == _sha256(document)
