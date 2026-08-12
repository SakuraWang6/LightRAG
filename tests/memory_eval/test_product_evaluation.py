"""Focused contract tests for the single LightRAG product evaluation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from lightrag.api import eval_comparison
from memory_eval_tests import artifacts, cli, http
from memory_eval_tests import execution, workflow
from memory_eval_tests.artifacts import (
    EvaluationDefinition,
    RunContext,
    read_progress,
    write_envelope,
    write_progress,
)


def test_envelope_has_a_single_evaluation_contract(tmp_path: Path) -> None:
    definition = EvaluationDefinition(
        id="end_to_end_baseline",
        label="端到端测评",
        description="产品链路",
        runner=lambda _: {},
    )
    context = RunContext(
        definition=definition,
        dataset=tmp_path / "missing-dataset",
        output_dir=tmp_path / "evaluation",
        baseline={"mode": "mix"},
        environment={},
        run_id="evaluation-one",
        runtime_snapshot={"status": "unavailable"},
    )
    path = write_envelope(
        context.output_dir,
        context=context,
        status="complete",
        methods=[],
        runs_root=tmp_path,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["evaluation"]["id"] == "end_to_end_baseline"
    assert "kind" not in payload
    assert "experiment" not in payload
    assert "variables" not in payload


def test_progress_is_valid_json_after_each_update(tmp_path: Path) -> None:
    write_progress(tmp_path, status="running", done=1, total=3, phase="ingestion")
    assert read_progress(tmp_path)["phase"] == "ingestion"
    assert not (tmp_path / ".progress.json.tmp").exists()


def test_manifest_resolves_the_repository_root_for_git_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Result:
        stdout = "abc123\n"

    def fake_run(*args: object, **kwargs: object) -> Result:
        captured["cwd"] = kwargs["cwd"]
        return Result()

    monkeypatch.setattr(artifacts.subprocess, "run", fake_run)
    assert artifacts._git_commit() == "abc123"
    assert captured["cwd"] == Path(artifacts.__file__).resolve().parents[1]


def test_environment_snapshot_excludes_obsolete_runtime_coordinates() -> None:
    environment = artifacts.capture_environment()

    assert "rag_api_url" not in environment
    assert "ollama_url" not in environment
    assert "storage_dir" not in environment


def test_evaluation_runtime_stabilizes_local_extraction() -> None:
    options = workflow._runtime_options(
        {
            "num_ctx": 16384,
            "num_predict": 4096,
            "temperature": 0,
            "extraction_llm_timeout_seconds": 1800,
            "extraction_max_async": 1,
        }
    )
    environment = execution._profile_environment(
        {
            "configuration": {
                "query": {"provider": "ollama", "model": "qwen3:8b"},
                "embedding": {"provider": "ollama", "model": "bge-m3:latest"},
                "parser_engine": "native",
            }
        },
        {
            "storage_dir": "/tmp/test-storage",
            "input_dir": "/tmp/test-input",
            "workspace_id": "test-workspace",
        },
        options,
    )

    assert environment["EXTRACT_LLM_TIMEOUT"] == "1800"
    assert environment["EXTRACT_MAX_ASYNC_LLM"] == "1"
    # Loopback backends must bypass any inherited proxy; a stalled proxy
    # connection otherwise surfaces as httpx ReadTimeout on fast extractions.
    assert environment["NO_PROXY"] == "127.0.0.1,localhost"
    assert environment["no_proxy"] == "127.0.0.1,localhost"


def test_comparison_requires_exact_case_set_and_scorer_inventory() -> None:
    base = {
        "status": "complete",
        "evaluation": {"id": "end_to_end_baseline"},
        "execution_manifest": {
            "dataset": {"manifest_sha256": "dataset-sha"},
            "case_selection": {"case_ids": ["Q-1", "Q-2"]},
            "execution_unit": {
                "profile": {"id": "server-default", "version": 1},
                "configuration_fingerprint": "config-sha",
            },
        },
        "scorers": [{"name": "deterministic-answer-rules", "version": "1.1"}],
    }
    assert eval_comparison.compare_contract([base, dict(base)])["comparable"] is True

    changed_cases = {
        **base,
        "execution_manifest": {
            **base["execution_manifest"],
            "case_selection": {"case_ids": ["Q-1"]},
        },
    }
    result = eval_comparison.compare_contract([base, changed_cases])
    assert result["comparable"] is False
    assert "case_set" in result["incompatible_fields"]

    missing_scorers = {**base, "scorers": []}
    result = eval_comparison.compare_contract([base, missing_scorers])
    assert result["comparable"] is False
    assert "scorers" in result["incompatible_fields"]

    result = eval_comparison.compare_contract(
        [{**base, "scorers": []}, {**base, "scorers": []}]
    )
    assert result["comparable"] is False
    assert "scorers" in result["incompatible_fields"]


def test_upload_streams_document_instead_of_reading_the_whole_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "document.docx"
    document.write_bytes(b"document-bytes")
    sent: list[bytes] = []
    headers: dict[str, str] = {}

    class Response:
        status = 200

        @staticmethod
        def read() -> bytes:
            return b'{"status":"success"}'

    class Connection:
        def __init__(self, host: str, timeout: int) -> None:
            assert host == "127.0.0.1:9621"
            assert timeout == 12

        @staticmethod
        def putrequest(method: str, target: str) -> None:
            assert method == "POST"
            assert target == "/documents/upload"

        @staticmethod
        def putheader(name: str, value: str) -> None:
            headers[name] = value

        @staticmethod
        def endheaders() -> None:
            return None

        @staticmethod
        def send(chunk: bytes) -> None:
            sent.append(chunk)

        @staticmethod
        def getresponse() -> Response:
            return Response()

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(http.http.client, "HTTPConnection", Connection)
    assert http.upload_file(
        document, "http://127.0.0.1:9621/documents/upload", timeout=12
    ) == {"status": "success"}
    assert b"".join(sent).count(b"document-bytes") == 1
    assert int(headers["Content-Length"]) == sum(len(chunk) for chunk in sent)


def test_disabling_kg_requires_vector_query_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "memory_eval_tests.cli",
            "--dataset",
            str(tmp_path / "dataset"),
            "--output-dir",
            str(tmp_path / "run"),
            "--skip-kg",
            "--mode",
            "mix",
        ],
    )
    with pytest.raises(SystemExit, match="2"):
        cli.main()
