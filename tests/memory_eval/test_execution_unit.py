"""Tests for isolated end-to-end evaluation execution units."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_eval_tests.experiments import execution_unit
from memory_eval_tests.experiments.common import ExperimentSpec, RunContext

pytestmark = pytest.mark.offline


def _profile(mode: str = "assigned") -> dict:
    return {
        "id": "profile-a",
        "version": 2,
        "configuration": {
            "execution_mode": mode,
            "runtime_endpoint": "http://assigned.test:9621" if mode == "assigned" else None,
            "query": {"provider": "openai", "model": "query-model"},
            "embedding": {"provider": "openai", "model": "embed-model"},
            "parser_engine": "native",
        },
    }


def test_allocated_units_never_share_workspace_or_storage(tmp_path: Path) -> None:
    profile = _profile()
    first = execution_unit.allocate_execution_unit(
        run_id="end-to-end-a", output_dir=tmp_path / "run-a", profile=profile
    )
    second = execution_unit.allocate_execution_unit(
        run_id="end-to-end-b", output_dir=tmp_path / "run-b", profile=profile
    )
    assert first["workspace_id"] != second["workspace_id"]
    assert first["storage_id"] != second["storage_id"]
    assert Path(first["storage_dir"]).is_dir()
    assert Path(first["input_dir"]).is_dir()
    assert Path(first["input_dir"]).is_relative_to(Path(first["storage_dir"]))
    persisted = json.loads((tmp_path / "run-a" / "execution_unit.json").read_text())
    assert persisted["profile"] == {"id": "profile-a", "version": 2}


def test_managed_unit_does_not_inherit_main_server_auth(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ACCOUNTS", "operator:secret")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "main-server-key")
    unit = execution_unit.allocate_execution_unit(
        run_id="end-to-end", output_dir=tmp_path / "run", profile=_profile("managed_local")
    )
    environment = execution_unit._profile_environment(_profile("managed_local"), unit)
    assert environment["AUTH_ACCOUNTS"] == ""
    assert environment["LIGHTRAG_API_KEY"] == ""
    assert environment["INPUT_DIR"] == unit["input_dir"]


def test_managed_unit_applies_supported_profile_settings(tmp_path: Path) -> None:
    profile = _profile("managed_local")
    profile["configuration"].update(
        {
            "extraction": {"provider": "openai", "model": "extract-model"},
            "storage_backends": {"kv": "JsonKVStorage", "vector": "NanoVectorDBStorage"},
            "concurrency": {"max_async_llm": 2, "max_parallel_insert": 4},
        }
    )
    unit = execution_unit.allocate_execution_unit(
        run_id="end-to-end", output_dir=tmp_path / "run", profile=profile
    )
    environment = execution_unit._profile_environment(profile, unit)
    assert environment["LLM_MODEL"] == "query-model"
    assert environment["EXTRACT_LLM_MODEL"] == "extract-model"
    assert environment["LIGHTRAG_PARSER"] == "*:native"
    assert environment["MAX_ASYNC_LLM"] == "2"


def test_managed_unit_applies_evaluation_runtime_options(tmp_path: Path) -> None:
    profile = _profile("managed_local")
    profile["configuration"]["extraction"] = {
        "provider": "openai",
        "model": "extract-model",
    }
    unit = execution_unit.allocate_execution_unit(
        run_id="end-to-end", output_dir=tmp_path / "run", profile=profile
    )
    environment = execution_unit._profile_environment(
        profile,
        unit,
        {
            "skip_kg": True,
            "generation": {"num_ctx": 16384, "num_predict": 2048, "temperature": 0.2},
            "extraction_generation": {"num_ctx": 16384, "num_predict": 8192, "temperature": 0.2},
            "extraction_safeguards": {"use_json": True, "max_records": 40, "max_entities": 16, "max_gleaning": 0},
        },
    )
    assert environment["LIGHTRAG_PARSER"] == "*:native-!"
    # OpenAI has no num_ctx runtime option, but output/temperature apply to
    # the base fallback and both explicitly configured roles.
    assert environment["OPENAI_LLM_MAX_COMPLETION_TOKENS"] == "2048"
    assert environment["QUERY_OPENAI_LLM_MAX_COMPLETION_TOKENS"] == "2048"
    assert environment["EXTRACT_OPENAI_LLM_TEMPERATURE"] == "0.2"
    assert environment["EXTRACT_OPENAI_LLM_MAX_COMPLETION_TOKENS"] == "8192"
    assert environment["ENTITY_EXTRACTION_USE_JSON"] == "true"
    assert environment["MAX_EXTRACTION_RECORDS"] == "40"
    assert environment["MAX_EXTRACTION_ENTITIES"] == "16"
    assert environment["MAX_GLEANING"] == "0"
    assert "QUERY_OPENAI_LLM_NUM_CTX" not in environment


def test_managed_ollama_unit_applies_context_window(tmp_path: Path) -> None:
    profile = _profile("managed_local")
    profile["configuration"]["query"] = {"provider": "ollama", "model": "qwen3:8b"}
    profile["configuration"]["embedding"] = {"provider": "ollama", "model": "bge-m3"}
    unit = execution_unit.allocate_execution_unit(
        run_id="end-to-end", output_dir=tmp_path / "run", profile=profile
    )
    environment = execution_unit._profile_environment(
        profile,
        unit,
        {"generation": {"num_ctx": 32768, "num_predict": 4096, "temperature": 0}},
    )
    assert environment["OLLAMA_LLM_NUM_CTX"] == "32768"
    assert environment["QUERY_OLLAMA_LLM_NUM_CTX"] == "32768"
    assert environment["QUERY_OLLAMA_LLM_NUM_PREDICT"] == "4096"


def test_managed_ollama_unit_separates_extraction_generation_budget(tmp_path: Path) -> None:
    profile = _profile("managed_local")
    profile["configuration"]["query"] = {"provider": "ollama", "model": "qwen3:8b"}
    unit = execution_unit.allocate_execution_unit(
        run_id="end-to-end", output_dir=tmp_path / "run", profile=profile
    )
    environment = execution_unit._profile_environment(
        profile,
        unit,
        {
            "generation": {"num_ctx": 16384, "num_predict": 4096, "temperature": 0},
            "extraction_generation": {"num_ctx": 16384, "num_predict": 8192, "temperature": 0},
        },
    )
    assert environment["QUERY_OLLAMA_LLM_NUM_PREDICT"] == "4096"
    assert environment["EXTRACT_OLLAMA_LLM_NUM_PREDICT"] == "8192"


def test_managed_unit_preflight_rejects_missing_ollama(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(execution_unit, "_ollama_reachable", lambda _endpoint: False)
    profile = _profile("managed_local")
    profile["configuration"]["query"] = {"provider": "ollama", "model": "qwen3:8b"}
    profile["configuration"]["embedding"] = {"provider": "ollama", "model": "bge-m3"}
    with pytest.raises(execution_unit.ExecutionUnitPrerequisiteError, match="unreachable"):
        execution_unit.preflight_execution_unit(profile)


def test_managed_unit_preflight_accepts_credentialed_remote_provider(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    execution_unit.preflight_execution_unit(_profile("managed_local"))


def test_assigned_unit_records_actual_runtime_snapshot(tmp_path: Path, monkeypatch) -> None:
    profile = _profile()
    unit = execution_unit.allocate_execution_unit(
        run_id="end-to-end", output_dir=tmp_path / "run", profile=profile
    )
    monkeypatch.setattr(
        execution_unit,
        "capture_runtime_snapshot",
        lambda **kwargs: {"status": "captured", "source_endpoint": kwargs["rag_api_url"]},
    )
    started = execution_unit.start_execution_unit(
        output_dir=tmp_path / "run", profile=profile, unit=unit
    )
    assert started["runtime_endpoint"] == "http://assigned.test:9621"
    assert started["runtime_snapshot"]["status"] == "captured"


def test_assigned_unit_requires_explicit_endpoint(tmp_path: Path) -> None:
    profile = _profile()
    profile["configuration"].pop("runtime_endpoint")
    with pytest.raises(ValueError, match="runtime_endpoint"):
        execution_unit.allocate_execution_unit(
            run_id="end-to-end", output_dir=tmp_path / "run", profile=profile
        )


def test_finalized_interrupted_unit_is_not_reported_as_complete(tmp_path: Path) -> None:
    unit = execution_unit.allocate_execution_unit(
        run_id="end-to-end", output_dir=tmp_path / "run", profile=_profile()
    )
    finalized = execution_unit.finalize_execution_unit(
        output_dir=tmp_path / "run", unit=unit, outcome="interrupted"
    )
    assert finalized["lifecycle_status"] == "interrupted"
    persisted = execution_unit.load_execution_unit(tmp_path / "run")
    assert persisted is not None
    assert persisted["run_outcome"] == "interrupted"


def test_cleanup_retention_only_removes_the_run_owned_storage(tmp_path: Path) -> None:
    profile = _profile()
    profile["configuration"]["retention_policy"] = "cleanup"
    run_dir = tmp_path / "run"
    unit = execution_unit.allocate_execution_unit(
        run_id="end-to-end", output_dir=run_dir, profile=profile
    )
    storage = Path(unit["storage_dir"])
    execution_unit.finalize_execution_unit(output_dir=run_dir, unit=unit, outcome="complete")
    assert not storage.exists()
    persisted = execution_unit.load_execution_unit(run_dir)
    assert persisted is not None
    assert persisted["lifecycle_status"] == "cleaned"


def test_end_to_end_runner_uses_server_configuration_and_writes_receipts(
    tmp_path: Path, monkeypatch
) -> None:
    import memory_eval_tests.experiments.end_to_end_baseline as end_to_end

    monkeypatch.setattr(end_to_end, "preflight_execution_unit", lambda _profile: None)
    context = RunContext(
        spec=ExperimentSpec(id="end_to_end_baseline", label="E2E", description="d", runner=lambda _c: {}),
        dataset=tmp_path / "dataset",
        output_dir=tmp_path / "run",
        baseline={"top_k": 5, "chunk_top_k": 3, "kg": False, "num_predict": 2048},
        environment={"rag_api_url": "http://old.test"},
        variables=[],
        run_id="e2e-run",
        runs_root=tmp_path / "runs",
    )
    monkeypatch.setattr(
        end_to_end,
        "allocate_execution_unit",
        lambda **_kwargs: {"workspace_id": "ws", "storage_id": "store", "runtime_endpoint": None},
    )
    start_call: dict = {}

    def fake_start_execution_unit(
        *, output_dir, profile, unit, api_key=None, access_token=None, runtime_options=None
    ):
        start_call["runtime_options"] = runtime_options
        return {
            "workspace_id": "ws",
            "storage_id": "store",
            "runtime_endpoint": "http://isolated.test",
            "runtime_snapshot": {"status": "captured"},
            "started_at": "2026-08-10T00:00:00+00:00",
        }

    monkeypatch.setattr(end_to_end, "start_execution_unit", fake_start_execution_unit)
    monkeypatch.setattr(
        end_to_end,
        "upload_dataset_files",
        lambda **_kwargs: {
            "uploaded": [{"status": "success", "track_status": {"passed": True}}],
            "passed": True,
            "elapsed_seconds": 1.0,
        },
    )
    monkeypatch.setattr(
        end_to_end,
        "evaluate_api",
        lambda **_kwargs: {"cases": 1, "average_recall": 1.0, "results": []},
    )
    answer_call: dict = {}

    def fake_evaluate_answers(**kwargs):
        answer_call.update(kwargs)
        return {"cases": 1, "answer_accuracy": 1.0, "results": []}

    monkeypatch.setattr(end_to_end, "evaluate_answers", fake_evaluate_answers)

    context.dataset.mkdir()
    (context.dataset / "source.docx").write_bytes(b"source")
    (context.dataset / "manifest.json").write_text(
        json.dumps(
            {
                "formats": ["docx"],
                "files": [
                    {
                        "name": "source.docx",
                        "format": "docx",
                        "role": "source_document",
                        "status": "created",
                    },
                    {
                        "name": "oracle.json",
                        "format": "json",
                        "role": "evaluation_artifact",
                        "status": "created",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (context.dataset / "oracle.json").write_text(
        json.dumps({"facts": [], "questions": []}), encoding="utf-8"
    )
    context.output_dir.mkdir()
    result = end_to_end._runner(context)
    assert result["status"] == "complete"
    assert context.environment["rag_api_url"] == "http://isolated.test"
    assert json.loads((context.output_dir / "ingestion_receipt.json").read_text())["passed"] is True
    assert json.loads((context.output_dir / "index_receipt.json").read_text())["workspace_id"] == "ws"
    assert json.loads((context.output_dir / "diagnosis.json").read_text())["case_count"] == 0
    assert "失败归因" in result["report"]
    assert answer_call["evaluation_trace"] is True
    assert answer_call["enable_rerank"] is False
    assert start_call["runtime_options"]["skip_kg"] is True
    assert start_call["runtime_options"]["generation"]["num_predict"] == 2048
    assert start_call["runtime_options"]["extraction_generation"]["num_predict"] == 8192
    assert start_call["runtime_options"]["extraction_safeguards"]["use_json"] is True


def test_product_retrieval_results_drop_raw_candidate_payloads() -> None:
    from memory_eval_tests.experiments.end_to_end_baseline import _product_retrieval_results

    assert _product_retrieval_results(
        [{"question_id": "Q-1", "hit_evidence": [{"text": "evidence"}], "top_k_candidates": [{"text": "raw"}]}]
    ) == [{"question_id": "Q-1", "hit_evidence": [{"text": "evidence"}]}]


def test_prepare_binds_workspace_to_immutable_execution_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    import memory_eval_tests.experiments.end_to_end_baseline as end_to_end

    context = RunContext(
        spec=ExperimentSpec(id="end_to_end_baseline", label="E2E", description="d", runner=lambda _c: {}),
        dataset=tmp_path / "dataset",
        output_dir=tmp_path / "run",
        baseline={}, environment={}, variables=[], run_id="e2e-run",
        runs_root=tmp_path / "runs", execution_manifest={"manifest_version": "1.0"},
    )
    context.output_dir.mkdir()
    context.dataset.mkdir()
    (context.dataset / "manifest.json").write_text(
        json.dumps(
            {
                "formats": ["docx"],
                "files": [{"name": "source.docx", "format": "docx", "status": "created"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(end_to_end, "preflight_execution_unit", lambda _profile: None)
    end_to_end._prepare(context)
    bound = context.execution_manifest["execution_unit"]
    assert bound["workspace_id"] == context.execution_unit["workspace_id"]
    assert bound["storage_id"] == context.execution_unit["storage_id"]
    assert len(bound["configuration_fingerprint"]) == 64
    assert bound["effective_configuration"]["parser_engine"] == "native"


def test_server_profile_uses_selected_model_without_hidden_defaults(tmp_path: Path, monkeypatch) -> None:
    import memory_eval_tests.experiments.end_to_end_baseline as end_to_end

    context = RunContext(
        spec=ExperimentSpec(id="end_to_end_baseline", label="E2E", description="d", runner=lambda _c: {}),
        dataset=tmp_path / "dataset",
        output_dir=tmp_path / "run",
        baseline={"model": "query-override", "top_k": 5, "mode": "mix"},
        environment={"llm_binding": "ollama", "embedding_binding": "ollama"}, variables=[], run_id="e2e-run",
        runs_root=tmp_path / "runs",
        execution_manifest={
            "parameters": {
                "top_k": {"value": 5, "source": "default"},
                "mode": {"value": "mix", "source": "user"},
            }
        },
    )
    context.output_dir.mkdir()
    context.dataset.mkdir()
    (context.dataset / "manifest.json").write_text(
        json.dumps(
            {
                "formats": ["docx"],
                "files": [{"name": "source.docx", "format": "docx", "status": "created"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(end_to_end, "preflight_execution_unit", lambda _profile: None)
    end_to_end._prepare(context)
    assert context.environment_profile["configuration"]["query"]["model"] == "query-override"
    assert context.baseline["top_k"] == 5
    assert context.baseline["mode"] == "mix"


def test_end_to_end_indexes_only_source_documents_not_oracle_artifacts(tmp_path: Path) -> None:
    from memory_eval_tests.experiments.end_to_end_baseline import _source_documents

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "formats": ["docx", "pdf"],
                "files": [
                    {"name": "a.docx", "format": "docx", "role": "source_document", "status": "created"},
                    {"name": "b.pdf", "format": "pdf", "role": "source_document", "status": "created"},
                    {"name": "oracle.json", "format": "json", "role": "evaluation_artifact", "status": "created"},
                    {"name": "facts.json", "format": "json", "role": "evaluation_artifact", "status": "created"},
                    {"name": "ignored.docx", "format": "docx", "status": "skipped"},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert _source_documents(dataset) == ["a.docx", "b.pdf"]


def test_end_to_end_legacy_manifest_still_excludes_json_artifacts(tmp_path: Path) -> None:
    from memory_eval_tests.experiments.end_to_end_baseline import _source_documents

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "formats": ["docx"],
                "files": [
                    {"name": "source.docx", "format": "docx", "status": "created"},
                    {"name": "oracle.json", "format": "json", "status": "created"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert _source_documents(dataset) == ["source.docx"]


def test_end_to_end_legacy_manifest_without_formats_still_indexes_docx(tmp_path: Path) -> None:
    from memory_eval_tests.experiments.end_to_end_baseline import _source_documents

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {"name": "source.docx", "format": "docx", "status": "created"},
                    {"name": "oracle.json", "format": "json", "status": "created"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert _source_documents(dataset) == ["source.docx"]
