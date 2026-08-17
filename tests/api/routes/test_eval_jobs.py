"""File-backed job safety tests for the product evaluation workflow."""

from __future__ import annotations

import json
from pathlib import Path

from lightrag.api import eval_jobs
from memory_data_service.schemas import DatasetSummary
from lightrag.api.eval_index import scan_runs
from memory_eval_tests.runner import RunParams, build_run_command


def test_run_command_uses_only_the_product_cli(tmp_path: Path) -> None:
    command = build_run_command(
        RunParams(dataset=tmp_path / "dataset", output_dir=tmp_path / "output")
    )
    assert command[:3] == [command[0], "-m", "memory_eval_tests.cli"]
    assert "--experiment" not in command
    assert "--ollama-url" not in command
    assert "--rag-api-url" not in command
    assert "--storage-dir" not in command


def test_build_run_command_serializes_vlm(tmp_path: Path) -> None:
    base = RunParams(dataset=tmp_path / "dataset", output_dir=tmp_path / "output")
    cmd = build_run_command(base)
    assert "--vlm" not in cmd
    assert "--no-vlm" not in cmd

    on = build_run_command(
        RunParams(
            dataset=tmp_path / "dataset",
            output_dir=tmp_path / "output",
            vlm=True,
        )
    )
    assert "--vlm" in on
    assert "--no-vlm" not in on

    off = build_run_command(
        RunParams(
            dataset=tmp_path / "dataset",
            output_dir=tmp_path / "output",
            vlm=False,
        )
    )
    assert "--no-vlm" in off
    assert "--vlm" not in off


def test_job_params_roundtrip_keeps_vlm(tmp_path: Path) -> None:
    payload = eval_jobs._params_to_json(
        RunParams(
            dataset=tmp_path / "dataset",
            output_dir=tmp_path / "output",
            vlm=True,
        )
    )
    assert payload["vlm"] is True
    restored = eval_jobs._params_from_json(payload)
    assert restored.vlm is True


def test_legacy_job_parameters_do_not_block_resumption(tmp_path: Path) -> None:
    params = eval_jobs._params_from_json(
        {
            "dataset": str(tmp_path / "dataset"),
            "output_dir": str(tmp_path / "output"),
            "ollama_url": "http://obsolete.invalid",
            "rag_api_url": "http://obsolete.invalid",
            "storage_dir": str(tmp_path / "obsolete-storage"),
        }
    )

    assert params.dataset == tmp_path / "dataset"
    assert params.output_dir == tmp_path / "output"


def test_job_file_is_written_atomically(tmp_path: Path) -> None:
    jobs = eval_jobs.jobs_root(tmp_path)
    job = {
        "id": "evaluation-1",
        "kind": "run",
        "evaluation": "end_to_end",
        "output_dir": str(tmp_path / "evaluation-1"),
        "status": "pending",
    }
    eval_jobs._write_job(jobs, job)

    path = jobs / "evaluation-1" / "job.json"
    assert json.loads(path.read_text(encoding="utf-8")) == job
    assert not (jobs / "evaluation-1" / "job.json.tmp").exists()


def test_dataset_job_persists_requested_language(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(eval_jobs, "_dispatch", lambda *_args, **_kwargs: None)

    job = eval_jobs.start_dataset_job(
        runs_root=tmp_path,
        dataset_id=None,
        display_name="中文检索质量测评",
        tier="smoke",
        profile="rich",
        pages=2,
        formats=["docx"],
        modalities=["text"],
        language="zh",
    )

    assert job["params"]["language"] == "zh"
    assert job["params"]["display_name"] == "中文检索质量测评"
    assert job["display_name"] == "中文检索质量测评"
    assert job["dataset_id"] == job["id"]


def test_pending_run_is_visible_before_a_worker_starts(
    tmp_path: Path, monkeypatch
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "dataset_id": "sample",
                "pages": 2,
                "tier": "smoke",
                "profile": "rich",
                "formats": ["docx"],
            }
        ),
        encoding="utf-8",
    )
    (dataset / "oracle.json").write_text(
        json.dumps({"questions": [{"id": "Q-1"}]}), encoding="utf-8"
    )
    monkeypatch.setattr(eval_jobs, "_dispatch", lambda *_args, **_kwargs: None)

    job = eval_jobs.start_run_job(
        runs_root=tmp_path,
        params=RunParams(
            dataset=dataset, output_dir=tmp_path / "unused", label="queued"
        ),
        supervise=False,
        supervision="auto",
        stale_minutes=60,
        max_restarts=0,
        poll_seconds=30,
    )

    envelope = json.loads((Path(job["output_dir"]) / "run.json").read_text())
    assert envelope["status"] == "queued"
    assert envelope["launch_params"]["case_ids"] == ["Q-1"]
    records = scan_runs(tmp_path, force=True)
    assert [(record["id"], record["status"]) for record in records] == [
        (Path(job["output_dir"]).name, "queued")
    ]


def test_dispatch_interval_defaults_to_15_seconds(monkeypatch) -> None:
    monkeypatch.delenv("MEMORY_EVAL_DISPATCH_INTERVAL_SECONDS", raising=False)
    assert eval_jobs._dispatch_interval_seconds() == 15


def test_dispatch_interval_env_override_and_clamp(monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_EVAL_DISPATCH_INTERVAL_SECONDS", "5")
    assert eval_jobs._dispatch_interval_seconds() == 5
    # Zero or negative intervals would spin the recovery loop; clamp to 1s.
    monkeypatch.setenv("MEMORY_EVAL_DISPATCH_INTERVAL_SECONDS", "0")
    assert eval_jobs._dispatch_interval_seconds() == 1
    # Invalid values fall back to the default instead of crashing the loop.
    monkeypatch.setenv("MEMORY_EVAL_DISPATCH_INTERVAL_SECONDS", "abc")
    assert eval_jobs._dispatch_interval_seconds() == 15


def test_dispatch_fills_each_configured_capacity_slot(
    tmp_path: Path, monkeypatch
) -> None:
    jobs = eval_jobs.jobs_root(tmp_path)
    for job_id in ("run-a", "run-b"):
        eval_jobs._write_job(
            jobs,
            {
                "id": job_id,
                "kind": "run",
                "status": "pending",
                "created_at": job_id,
                "params": {},
                "output_dir": str(tmp_path / job_id),
            },
        )
    started: list[str] = []
    monkeypatch.setenv("MEMORY_EVAL_MAX_ACTIVE_JOBS", "2")
    monkeypatch.setattr(eval_jobs, "_start_dispatch_loop", lambda *_args: None)
    monkeypatch.setattr(
        eval_jobs,
        "_params_from_json",
        lambda _payload: RunParams(tmp_path, tmp_path / "out"),
    )
    monkeypatch.setattr(
        eval_jobs,
        "_claim_owner",
        lambda: {"owner_id": "worker", "lease_expires_at": "2999-01-01T00:00:00+00:00"},
    )
    monkeypatch.setattr(
        eval_jobs,
        "_spawn_run_job",
        lambda *, job_id, **_kwargs: started.append(job_id),
    )

    eval_jobs._dispatch(tmp_path, tmp_path / "datasets")
    assert started == ["run-a", "run-b"]


def test_dataset_generation_uses_a_queue_independent_of_running_evaluations(
    tmp_path: Path, monkeypatch
) -> None:
    jobs = eval_jobs.jobs_root(tmp_path)
    eval_jobs._write_job(
        jobs,
        {
            "id": "run-active",
            "kind": "run",
            "status": "claiming",
            "created_at": "2026-01-01T00:00:00+00:00",
            "claim": {"lease_expires_at": "2999-01-01T00:00:00+00:00"},
            "params": {},
            "output_dir": str(tmp_path / "run-active"),
        },
    )
    eval_jobs._write_job(
        jobs,
        {
            "id": "dataset-pending",
            "kind": "dataset",
            "status": "pending",
            "created_at": "2026-01-01T00:00:01+00:00",
            "params": {},
            "output_dir": str(tmp_path / "dataset-pending"),
        },
    )
    started: list[str] = []
    monkeypatch.setattr(eval_jobs, "_start_dispatch_loop", lambda *_args: None)
    monkeypatch.setattr(
        eval_jobs,
        "_claim_owner",
        lambda: {"owner_id": "worker", "lease_expires_at": "2999-01-01T00:00:00+00:00"},
    )
    monkeypatch.setattr(
        eval_jobs,
        "_spawn_dataset_job",
        lambda *, job_id, **_kwargs: started.append(job_id),
    )

    eval_jobs._dispatch(tmp_path, tmp_path / "datasets")

    assert started == ["dataset-pending"]


def test_queue_positions_are_scoped_to_job_kind(tmp_path: Path) -> None:
    jobs = eval_jobs.jobs_root(tmp_path)
    for job_id, kind, created_at in (
        ("run-first", "run", "2026-01-01T00:00:00+00:00"),
        ("dataset-only", "dataset", "2026-01-01T00:00:01+00:00"),
        ("run-second", "run", "2026-01-01T00:00:02+00:00"),
    ):
        eval_jobs._write_job(
            jobs,
            {
                "id": job_id,
                "kind": kind,
                "status": "pending",
                "created_at": created_at,
                "params": {},
                "output_dir": str(tmp_path / job_id),
            },
        )

    listed = {
        job["id"]: job
        for job in eval_jobs.list_jobs(
            runs_root=tmp_path, datasets_root=tmp_path / "datasets"
        )
    }

    assert listed["run-first"]["queue_position"] == 1
    assert listed["run-second"]["queue_position"] == 2
    assert listed["dataset-only"]["queue_position"] == 1


def test_dataset_force_overwrite_resolves_same_display_name(
    tmp_path: Path, monkeypatch
) -> None:
    """Force + same display name must overwrite the existing dataset.

    The WebUI creates datasets by name without a dataset_id, so without this
    resolution every forced regeneration silently created a new directory.
    """
    fake = [
        DatasetSummary(
            dataset_id="existing-ds",
            display_name="同名数据集",
            title="同名数据集",
            tier="smoke",
            profile="rich",
            language="zh",
            pages=20,
            formats=["docx"],
            modalities=["text"],
            path=str(tmp_path / "datasets" / "existing-ds"),
            created_at="2026-01-01T00:00:00+00:00",
            files=[],
        )
    ]
    monkeypatch.setattr(eval_jobs, "list_datasets", lambda _root: fake)
    runs_root = tmp_path / "runs"
    datasets_root = tmp_path / "datasets"

    job = eval_jobs.start_dataset_job(
        runs_root=runs_root,
        datasets_root=datasets_root,
        dataset_id=None,
        tier="smoke",
        profile="rich",
        pages=20,
        formats=["docx"],
        modalities=["text"],
        display_name="同名数据集",
        language="zh",
        force=True,
    )
    assert job["dataset_id"] == "existing-ds"

    plain = eval_jobs.start_dataset_job(
        runs_root=runs_root,
        datasets_root=datasets_root,
        dataset_id=None,
        tier="smoke",
        profile="rich",
        pages=20,
        formats=["docx"],
        modalities=["text"],
        display_name="同名数据集",
        language="zh",
        force=False,
    )
    assert plain["dataset_id"] != "existing-ds"


def test_dispatch_failure_marks_the_visible_queued_run_as_failed(
    tmp_path: Path, monkeypatch
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(json.dumps({"dataset_id": "sample"}))
    (dataset / "oracle.json").write_text(json.dumps({"questions": [{"id": "Q-1"}]}))
    monkeypatch.setattr(eval_jobs, "_start_dispatch_loop", lambda *_args: None)

    def fail_spawn(**_kwargs) -> None:
        raise RuntimeError("worker binary unavailable")

    monkeypatch.setattr(eval_jobs, "_spawn_run_job", fail_spawn)
    job = eval_jobs.start_run_job(
        runs_root=tmp_path,
        params=RunParams(dataset=dataset, output_dir=tmp_path / "unused"),
        supervise=False,
        supervision="auto",
        stale_minutes=60,
        max_restarts=0,
        poll_seconds=30,
    )

    assert job["status"] == "failed"
    envelope = json.loads((Path(job["output_dir"]) / "run.json").read_text())
    assert envelope["status"] == "failed"
    assert envelope["failure"]["phase"] == "dispatch"
