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
    persisted = json.loads((tmp_path / "run-a" / "execution_unit.json").read_text())
    assert persisted["profile"] == {"id": "profile-a", "version": 2}


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


def test_end_to_end_runner_requires_published_profile_and_writes_receipts(
    tmp_path: Path, monkeypatch
) -> None:
    import memory_eval_tests.experiments.end_to_end_baseline as end_to_end

    profile = {**_profile(), "status": "published"}
    context = RunContext(
        spec=ExperimentSpec(id="end_to_end_baseline", label="E2E", description="d", runner=lambda _c: {}),
        dataset=tmp_path / "dataset",
        output_dir=tmp_path / "run",
        baseline={"top_k": 5, "chunk_top_k": 5},
        environment={"rag_api_url": "http://old.test"},
        variables=[],
        run_id="e2e-run",
        extra={"environment_profile_id": "profile-a", "environment_profile_version": "2"},
        runs_root=tmp_path / "runs",
    )
    monkeypatch.setattr(end_to_end.eval_profiles, "get_profile_version", lambda *_args: profile)
    monkeypatch.setattr(
        end_to_end,
        "allocate_execution_unit",
        lambda **_kwargs: {"workspace_id": "ws", "storage_id": "store", "runtime_endpoint": None},
    )
    def fake_start_execution_unit(
        *, output_dir, profile, unit, api_key=None, access_token=None
    ):
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


def test_prepare_binds_workspace_to_immutable_execution_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    import memory_eval_tests.experiments.end_to_end_baseline as end_to_end

    profile = {**_profile(), "status": "published"}
    context = RunContext(
        spec=ExperimentSpec(id="end_to_end_baseline", label="E2E", description="d", runner=lambda _c: {}),
        dataset=tmp_path / "dataset",
        output_dir=tmp_path / "run",
        baseline={}, environment={}, variables=[], run_id="e2e-run",
        extra={"environment_profile_id": "profile-a", "environment_profile_version": "2"},
        runs_root=tmp_path / "runs", execution_manifest={"manifest_version": "1.0"},
    )
    context.output_dir.mkdir()
    monkeypatch.setattr(end_to_end.eval_profiles, "get_profile_version", lambda *_args: profile)
    end_to_end._prepare(context)
    bound = context.execution_manifest["execution_unit"]
    assert bound["workspace_id"] == context.execution_unit["workspace_id"]
    assert bound["storage_id"] == context.execution_unit["storage_id"]


def test_end_to_end_uses_every_created_manifest_format(tmp_path: Path) -> None:
    from memory_eval_tests.experiments.end_to_end_baseline import _dataset_formats

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {"name": "a.docx", "format": "docx", "status": "created"},
                    {"name": "b.pdf", "format": "pdf", "status": "created"},
                    {"name": "ignored.docx", "format": "docx", "status": "skipped"},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert _dataset_formats(dataset) == ["docx", "pdf"]
