"""Focused contract tests for the single LightRAG product evaluation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from lightrag.api import eval_comparison
from memory_eval_tests import artifacts, cli, http
from memory_eval_tests import execution, ingestion, workflow
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


def test_evaluation_runtime_concurrency_controls_apply() -> None:
    """Per-run concurrency must reach the child server and honour --extra."""
    options = workflow._runtime_options(
        {
            "num_ctx": 16384,
            "num_predict": 4096,
            "temperature": 0,
            "extraction_llm_timeout_seconds": 1800,
            "extraction_max_async": 3,
            "query_max_async": 4,
        },
        {"extraction_max_async": "2", "query_max_async": "3"},
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
    assert environment["QUERY_MAX_ASYNC_LLM"] == "3"
    assert environment["EXTRACT_MAX_ASYNC_LLM"] == "2"


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


def test_upload_file_sends_process_options_multipart_field(
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
            pass

        @staticmethod
        def putrequest(method: str, target: str) -> None:
            pass

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
        document,
        "http://127.0.0.1:9621/documents/upload",
        timeout=12,
        process_options="Fi",
    ) == {"status": "success"}
    body = b"".join(sent)
    assert b'name="process_options"' in body
    assert b"\r\nFi\r\n" in body
    assert int(headers["Content-Length"]) == len(body)


def test_upload_dataset_files_forwards_process_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    document = dataset / "document.docx"
    document.write_bytes(b"document-bytes")
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "dataset_id": "sample",
                "formats": ["docx"],
                "files": [
                    {
                        "name": "document.docx",
                        "format": "docx",
                        "role": "source_document",
                        "status": "created",
                        "path": str(document),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_upload(path, url, **kwargs):
        captured["path"] = path
        captured["kwargs"] = kwargs
        return {"status": "success", "track_id": "track-1"}

    monkeypatch.setattr(ingestion, "_http_upload_file", fake_upload)
    result = ingestion.upload_dataset_files(
        dataset_source=str(dataset),
        rag_api_url="http://127.0.0.1:1",
        process_options="Fi",
    )
    assert result["uploaded"][0]["status"] == "success"
    assert captured["kwargs"]["process_options"] == "Fi"


def test_receipt_uses_processing_terminal_state_and_chunk_count() -> None:
    """Receipts must not call a timed-out upload successful, and they must
    propagate the chunk_count already present in the track-status payload."""
    unit = {"workspace_id": "w", "storage_id": "s", "started_at": "now"}
    upload = {
        "waited": True,
        "passed": False,
        "uploaded": [
            {
                "status": "success",
                "file_name": "a.docx",
                "content_sha256": "a",
                "track_id": "t1",
                "message": "uploaded",
                "reused": False,
                "track_status": {
                    "passed": True,
                    "documents": [{"chunks_count": 7}],
                },
            },
            {
                "status": "success",
                "file_name": "b.docx",
                "content_sha256": "b",
                "track_id": "t2",
                "message": "uploaded",
                "reused": False,
                "track_status": {
                    "passed": False,
                    "timed_out": True,
                    "error": "still processing",
                    "documents": [{"chunks_count": 3}],
                },
            },
        ],
    }
    ingestion_receipt, index_receipt = workflow._receipt(upload, unit)
    assert ingestion_receipt["successful_documents"] == 1
    assert ingestion_receipt["failed_documents"] == 1
    assert ingestion_receipt["passed"] is False
    assert ingestion_receipt["documents"][1]["failure_reason"] == "still processing"
    assert index_receipt["chunk_count"] == 10


def test_execution_manifest_reads_nested_generation_provenance(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "dataset_id": "d1",
                "pages": 1,
                "tier": "smoke",
                "profile": "rich",
                "language": "zh",
                "formats": ["docx"],
                "title": "D",
                "files": [],
                "oracle_file": "oracle.json",
                "generation_provenance": {
                    "generator_code_version": "generator-sha",
                    "template_version": "template-v9",
                    "seed": 42,
                },
            }
        ),
        encoding="utf-8",
    )
    (dataset / "oracle.json").write_text("{}", encoding="utf-8")

    manifest = artifacts.build_execution_manifest(
        dataset=dataset,
        evaluation_id="end_to_end_baseline",
        evaluation_type="evaluation",
        parameters={"mode": "mix"},
        parameter_sources={"mode": "default"},
    )
    assert manifest["dataset"]["generator_version"] == "generator-sha"
    assert manifest["dataset"]["template_version"] == "template-v9"
    assert manifest["dataset"]["random_seed"] == 42


def test_effective_vlm_auto_detects_figures_from_manifest(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    manifest = dataset / "manifest.json"

    manifest.write_text(
        json.dumps({"modalities": ["text", "figures"]}), encoding="utf-8"
    )
    assert workflow._effective_vlm({}, dataset) is True

    manifest.write_text(json.dumps({"modalities": ["text"]}), encoding="utf-8")
    assert workflow._effective_vlm({}, dataset) is False

    manifest.write_text(
        json.dumps({"modalities": ["text"]}), encoding="utf-8"
    )
    assert workflow._effective_vlm({"vlm": True}, dataset) is True
    assert workflow._effective_vlm({"vlm": False}, dataset) is False

    # Legacy manifests without a modalities list fall back to figure files.
    manifest.write_text(
        json.dumps(
            {
                "files": [
                    {"name": "zh_figure_0004.png", "role": "evaluation_artifact"}
                ]
            }
        ),
        encoding="utf-8",
    )
    assert workflow._effective_vlm({}, dataset) is True


def test_ingestion_process_options_reflects_vlm_and_override() -> None:
    assert workflow._ingestion_process_options({"vlm": True}) == "Fi"
    assert workflow._ingestion_process_options({"vlm": False}) == "F"
    assert (
        workflow._ingestion_process_options(
            {"vlm": True}, {"process_options": "Fit"}
        )
        == "Fit"
    )


def test_ingestion_timeout_scales_with_dataset_pages(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    manifest = dataset / "manifest.json"

    manifest.write_text(json.dumps({"pages": 200}), encoding="utf-8")
    assert workflow._ingestion_timeout_seconds({}, None, dataset) == 18000

    manifest.write_text(json.dumps({"pages": 20}), encoding="utf-8")
    assert workflow._ingestion_timeout_seconds({}, None, dataset) == 5400

    # Explicit override always wins over the page-based default.
    assert (
        workflow._ingestion_timeout_seconds(
            {}, {"ingestion_timeout_seconds": "21600"}, dataset
        )
        == 21600
    )
    assert (
        workflow._ingestion_timeout_seconds(
            {"ingestion_timeout_seconds": 7200}, None, dataset
        )
        == 7200
    )


def test_ingestion_timeout_accounts_for_vlm_figures(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    manifest = dataset / "manifest.json"
    files = [
        {"name": f"zh_figure_{i:04d}.png", "role": "evaluation_artifact"}
        for i in range(50)
    ]

    manifest.write_text(
        json.dumps({"pages": 200, "files": files}), encoding="utf-8"
    )
    # 200 pages * 90s + 50 figures * 180s = 27000s (7.5h), beyond the old
    # 18000s ceiling that timed out the VLM-heavy 200P stress dataset.
    assert workflow._ingestion_timeout_seconds({}, None, dataset) == 27000

    # The 12h ceiling still bounds very large stress documents.
    huge_files = [
        {"name": f"zh_figure_{i:04d}.png", "role": "evaluation_artifact"}
        for i in range(100)
    ]
    manifest.write_text(
        json.dumps({"pages": 400, "files": huge_files}), encoding="utf-8"
    )
    assert workflow._ingestion_timeout_seconds({}, None, dataset) == 43200


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
