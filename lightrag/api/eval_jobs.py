"""In-process evaluation job manager (file-backed, single-server).

Jobs are the file-based counterpart of a run: each job writes
``runs/.jobs/<job_id>/job.json`` holding the top-level pid, its process start
time, kind (run | dataset), parameters and output location.  Active jobs are
derived from those files plus a pid + start-time liveness probe, so cancel
survives API restarts and multiple uvicorn workers stay consistent.

Credentials are never written into job.json; the run wizard also rejects
infrastructure parameters (server address, API key/token) by design.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lightrag.utils import logger
from memory_eval_tests.artifacts import build_failure, mark_envelope_failed
from memory_eval_tests.runner import (
    RunParams,
    build_run_command,
    build_supervise_command,
)
from memory_data_service.storage import list_datasets

_JOBS_DIR = ".jobs"
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_PAGE_CAP = 1000
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_DISPATCH_LOCK = threading.Lock()
_DISPATCH_LOOP_STARTED = False
_CLAIM_LOCK_FILE = ".claim.lock"
_TERMINAL_STATUSES = frozenset({"complete", "failed", "cancelled", "stale"})
_ACTIVE_STATUSES = frozenset({"claiming", "running", "cancelling"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _max_active_jobs() -> int:
    """Return the execution capacity for model-backed evaluation runs only."""
    raw = os.getenv("MEMORY_EVAL_MAX_ACTIVE_JOBS")
    if raw and raw.strip().isdigit():
        return max(1, int(raw.strip()))
    return 1


def _max_active_dataset_jobs() -> int:
    """Return the independent capacity for local dataset-generation jobs.

    Dataset generation writes deterministic files and does not call the local
    Ollama model.  It must therefore never be held behind a model-backed run.
    A conservative default still avoids several DOCX writers contending for
    the same machine; installations that have CPU and I/O headroom can raise
    it without changing evaluation-run concurrency.
    """
    raw = os.getenv("MEMORY_EVAL_MAX_ACTIVE_DATASET_JOBS")
    if raw and raw.strip().isdigit():
        return max(1, int(raw.strip()))
    return 1


def _lease_seconds() -> int:
    raw = os.getenv("MEMORY_EVAL_JOB_LEASE_SECONDS")
    if raw and raw.strip().isdigit():
        return max(30, int(raw.strip()))
    return 120


def _lease_expires_at() -> str:
    return datetime.fromtimestamp(
        time.time() + _lease_seconds(), timezone.utc
    ).isoformat(timespec="seconds")


def _lease_is_expired(claim: Any) -> bool:
    if not isinstance(claim, dict):
        return True
    value = claim.get("lease_expires_at")
    if not isinstance(value, str):
        return True
    try:
        return datetime.fromisoformat(value) <= datetime.now(timezone.utc)
    except ValueError:
        return True


def _claim_owner() -> dict[str, Any]:
    pid = os.getpid()
    process_started_at = _probe_process_start(pid)
    owner_id = f"{socket.gethostname()}:{pid}:{process_started_at or 'unknown'}:{uuid.uuid4().hex}"
    return {
        "owner_id": owner_id,
        "pid": pid,
        "process_started_at": process_started_at,
        "claimed_at": _now_iso(),
        "lease_expires_at": _lease_expires_at(),
    }


@contextmanager
def _claim_file_lock(runs_root: Path):
    """Cross-process lock for the pending → claiming → running transition."""
    root = jobs_root(runs_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / _CLAIM_LOCK_FILE
    with path.open("a+", encoding="utf-8") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write("0")
        handle.flush()
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:  # pragma: no cover - exercised on Windows only
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        try:
            yield
        finally:
            unlock()


def _hold_blocks(runs_root: Path) -> bool:
    """True while the optional *run* queue hold has not reached completion."""
    hold = os.getenv("MEMORY_EVAL_WAIT_FOR_RUN")
    if not hold:
        return False
    hold_path = Path(hold)
    if not hold_path.is_absolute():
        hold_path = _REPO_ROOT / hold_path
    try:
        envelope = json.loads((hold_path / "run.json").read_text(encoding="utf-8"))
        return envelope.get("status") != "complete"
    except (OSError, ValueError):
        # The gate run does not exist (yet): keep waiting rather than skip.
        return True


def jobs_root(runs_root: Path) -> Path:
    return runs_root / _JOBS_DIR


def _default_datasets_root(runs_root: Path) -> Path:
    return Path(runs_root).parent.parent / "memory_data_service" / "generated"


def _job_id(kind: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{kind}-{ts}-{uuid.uuid4().hex[:4]}"


def _probe_process_start(pid: int) -> int | None:
    """Return a stable process-start identifier for ``pid`` (or None)."""
    # Linux: /proc/<pid>/stat field 22 is starttime in clock ticks since boot.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            fields = handle.read().split()
        return int(fields[21])
    except (OSError, ValueError, IndexError):
        pass
    # Fallback (macOS/BSD): `ps -o lstart=`.
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
        if out:
            started = datetime.strptime(
                out, "%a %b %d %H:%M:%S %Y"
            ).replace(tzinfo=timezone.utc)
            return int(started.timestamp())
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def job_liveness(job: dict[str, Any]) -> str:
    """Return ``alive`` / ``dead`` / ``reused`` for a job's recorded process.

    ``reused`` means the pid now belongs to a different process (start time
    mismatch) — cancellation must refuse such jobs instead of risking a kill.
    """
    pid = job.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return "dead"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead"
    except PermissionError:
        pass
    stored = job.get("process_started_at")
    if stored is None:
        return "alive"
    current = _probe_process_start(pid)
    if current is None:
        return "alive"
    return "alive" if current == stored else "reused"


def _write_job(jobs_root: Path, job: dict[str, Any]) -> None:
    job_dir = jobs_root / job["id"]
    job_dir.mkdir(parents=True, exist_ok=True)
    tmp = job_dir / "job.json.tmp"
    tmp.write_text(
        json.dumps(job, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(job_dir / "job.json")


def _read_job(jobs_root: Path, job_id: str) -> dict[str, Any] | None:
    try:
        return json.loads((jobs_root / job_id / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _raw_jobs(runs_root: Path) -> list[dict[str, Any]]:
    root = jobs_root(runs_root)
    if not root.is_dir():
        return []
    found = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        job = _read_job(root, entry.name)
        if job is not None:
            found.append(job)
    return found


def _derive_status(
    job: dict[str, Any],
    *,
    runs_root: Path,
    datasets_root: Path,
) -> str:
    status = {"canceled": "cancelled", "succeeded": "complete"}.get(
        job.get("status"), job.get("status")
    )
    if status in _TERMINAL_STATUSES or status == "pending":
        return status
    if status == "claiming":
        return "pending" if _lease_is_expired(job.get("claim")) else "claiming"
    liveness = job_liveness(job)
    if liveness == "reused":
        return "stale"
    if status == "cancelling":
        return "cancelling" if liveness == "alive" else "cancelled"
    if liveness == "alive":
        return "running"
    exit_code = job.get("exit_code")
    if job.get("kind") == "dataset":
        dataset_id = job.get("dataset_id")
        manifest = datasets_root / str(dataset_id) / "manifest.json"
        if exit_code is not None:
            return "complete" if exit_code == 0 and manifest.exists() else "failed"
        return "complete" if manifest.exists() else "failed"
    try:
        envelope = json.loads(
            (Path(job["output_dir"]) / "run.json").read_text(encoding="utf-8")
        )
        status = envelope.get("status")
    except (OSError, ValueError):
        status = None
    if exit_code is not None:
        return "complete" if exit_code == 0 else "failed"
    return "complete" if status == "complete" else "failed"


def _refresh_job(
    job: dict[str, Any],
    *,
    runs_root: Path,
    datasets_root: Path,
    recover_expired_claim: bool = False,
) -> dict[str, Any]:
    """Refresh one job while the caller holds ``_claim_file_lock``.

    This is a read-modify-write operation.  Calling it from an unlocked read
    route can otherwise overwrite a concurrent cancellation or lease renewal.
    """
    previous = job.get("status")
    job["status"] = _derive_status(
        job, runs_root=runs_root, datasets_root=datasets_root
    )
    if previous == "claiming" and job["status"] == "pending":
        if not recover_expired_claim:
            # Only the dispatcher holds the cross-process claim lock.  A read
            # endpoint may report this job as reclaimable, but must not write a
            # stale copy over a new claim made by another API worker.
            return job
        job.pop("claim", None)
        job["lease_expires_at"] = None
        job["recovered_at"] = _now_iso()
    if job["status"] in _TERMINAL_STATUSES and not job.get(
        "finished_at"
    ):
        job["finished_at"] = _now_iso()
    _write_job(jobs_root(runs_root), job)
    return job


def _params_to_json(params: RunParams) -> dict[str, Any]:
    excluded = {"api_key", "access_token"}
    payload: dict[str, Any] = {}
    for key, value in vars(params).items():
        if key in excluded:
            continue
        if isinstance(value, Path):
            payload[key] = str(value)
        elif key == "extra":
            payload[key] = list(value)
        else:
            payload[key] = value
    return payload


def _valid_job_id(job_id: str) -> bool:
    return bool(_JOB_ID_RE.fullmatch(job_id)) and job_id not in {".", ".."}


def _record_exit(
    job_id: str,
    jobs_root: Path,
    proc: subprocess.Popen,
    datasets_root: Path | None = None,
) -> None:
    """Reap the child and persist its exit code for status derivation."""
    try:
        code = proc.wait()
    except Exception:
        return
    with _claim_file_lock(jobs_root.parent):
        job = _read_job(jobs_root, job_id)
        if job is not None:
            job["exit_code"] = code
            _write_job(jobs_root, job)
    # A finished job frees a slot: start the next queued job, if any.
    _dispatch(
        jobs_root.parent,
        datasets_root=datasets_root or _default_datasets_root(jobs_root.parent),
    )


def _renew_job_lease(
    *, jobs: Path, job_id: str, proc: subprocess.Popen, owner_id: str
) -> None:
    """Renew a running job's lease while its direct child remains alive."""
    poll = getattr(proc, "poll", None)
    if not callable(poll):
        return
    interval = max(10, _lease_seconds() // 3)
    while poll() is None:
        time.sleep(interval)
        with _claim_file_lock(jobs.parent):
            job = _read_job(jobs, job_id)
            if job is None:
                return
            claim = job.get("claim") or {}
            if job.get("status") not in {"running", "cancelling"} or claim.get(
                "owner_id"
            ) != owner_id:
                return
            claim["lease_expires_at"] = _lease_expires_at()
            job["claim"] = claim
            job["lease_expires_at"] = claim["lease_expires_at"]
            _write_job(jobs, job)


def _unique_run_dir(runs_root: Path) -> Path:
    while True:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        candidate = runs_root / f"evaluation-{ts}-{uuid.uuid4().hex[:4]}"
        if not candidate.exists():
            return candidate


def _params_from_json(payload: dict[str, Any]) -> RunParams:
    data = dict(payload)
    # Jobs persisted before the isolated-runtime refactor may contain these
    # no-op connection overrides.  Consume them so pending legacy jobs remain
    # resumable while new jobs cannot declare them.
    for key in ("ollama_url", "rag_api_url", "storage_dir"):
        data.pop(key, None)
    for key in ("dataset", "output_dir", "runs_root"):
        if data.get(key) is not None:
            data[key] = Path(data[key])
    data["extra"] = list(data.get("extra") or [])
    return RunParams(**data)


def _write_queued_run_envelope(*, runs_root: Path, params: RunParams) -> None:
    """Publish a provisional run record before dispatching its child process.

    A queued job is already a user-visible evaluation.  Without this envelope
    the run index ignores it until a worker starts the CLI, making jobs queued
    behind another run disappear from the measurement page.
    """
    from memory_eval_tests.artifacts import (
        BASELINE_DEFAULTS,
        RunContext,
        append_run_event,
        build_execution_manifest,
        capture_environment,
        redact_launch_extra,
        selected_case_ids,
        write_envelope,
        write_progress,
    )
    from memory_eval_tests.workflow import definition

    baseline = dict(definition.default_baseline)
    baseline.update(
        {key: value for key, value in BASELINE_DEFAULTS.items() if key not in baseline}
    )
    parameter_sources = {key: "default" for key in baseline}
    for key in (
        "model",
        "mode",
        "top_k",
        "chunk_top_k",
        "num_ctx",
        "num_predict",
        "max_total_tokens",
        "temperature",
        "engine",
    ):
        value = getattr(params, key)
        if value is not None:
            baseline[key] = value
            parameter_sources[key] = "user"
    baseline["max_cases"] = params.max_cases
    if params.max_cases:
        parameter_sources["max_cases"] = "user"
    if params.skip_kg:
        baseline["kg"] = False
        baseline["mode"] = "naive"
        parameter_sources["kg"] = "user"
        parameter_sources["mode"] = "user"

    started_at = _now_iso()
    case_ids = selected_case_ids(params.dataset, params.max_cases)
    manifest = build_execution_manifest(
        dataset=params.dataset,
        evaluation_id=definition.id,
        evaluation_type="evaluation",
        parameters=baseline,
        parameter_sources=parameter_sources,
        started_at=started_at,
    )
    manifest.update(
        {
            "provisional": True,
            "case_selection": {
                "algorithm": "deterministic_even_stride_v1",
                "requested_max_cases": params.max_cases,
                "case_ids": case_ids,
            },
        }
    )
    context = RunContext(
        definition=definition,
        dataset=params.dataset,
        output_dir=params.output_dir,
        baseline=baseline,
        environment=capture_environment(
            api_key=params.api_key,
            access_token=params.access_token,
        ),
        run_id=params.run_id or params.output_dir.name,
        label=params.label,
        started_at=started_at,
        runs_root=runs_root,
    )
    context.execution_manifest = manifest
    context.runtime_snapshot = {
        "snapshot_version": "1.0",
        "status": "queued",
        "reason": "waiting for an execution slot",
    }
    write_envelope(
        params.output_dir,
        context=context,
        status="queued",
        methods=[],
        write_progress_file=False,
        runs_root=runs_root,
        extra={
            "launch_params": {
                **{
                    key: baseline[key]
                    for key in (
                        "model", "mode", "top_k", "chunk_top_k", "max_cases",
                        "num_ctx", "num_predict", "max_total_tokens", "temperature",
                        "engine", "kg",
                    )
                    if key in baseline
                },
                "case_ids": case_ids,
                "extra": redact_launch_extra(list(params.extra)),
            }
        },
    )
    write_progress(
        params.output_dir,
        status="queued",
        done=0,
        total=1,
        phase="starting",
        message="等待执行队列",
    )
    append_run_event(
        params.output_dir,
        phase="starting",
        severity="info",
        message="evaluation job queued",
    )


def _spawn_run_job(
    *,
    job_id: str,
    runs_root: Path,
    datasets_root: Path | None,
    params: RunParams,
    supervise: bool,
    supervision: str,
    stale_minutes: int,
    max_restarts: int,
    poll_seconds: int,
    owner_id: str,
) -> dict[str, Any]:
    jobs = jobs_root(runs_root)
    job = _read_job(jobs, job_id)
    if job is None:
        raise KeyError(f"job {job_id} not found")
    if job.get("status") != "claiming" or (job.get("claim") or {}).get(
        "owner_id"
    ) != owner_id:
        raise RuntimeError(f"job {job_id} is no longer claimed by this worker")
    params.output_dir = Path(job["output_dir"])
    params.output_dir.mkdir(parents=True, exist_ok=True)
    cmd = (
        build_supervise_command(
            params,
            supervision=supervision,
            stale_minutes=stale_minutes,
            max_restarts=max_restarts,
            poll_seconds=poll_seconds,
        )
        if supervise
        else build_run_command(params)
    )
    child_env = dict(os.environ)
    proc = subprocess.Popen(
        cmd,
        cwd=_REPO_ROOT,
        env=child_env,
        start_new_session=True,
    )
    job.update(
        {
            "pid": proc.pid,
            "process_started_at": _probe_process_start(proc.pid),
            "status": "running",
            "started_at": _now_iso(),
            "supervise": bool(supervise),
        }
    )
    job["claim"]["lease_expires_at"] = _lease_expires_at()
    job["lease_expires_at"] = job["claim"]["lease_expires_at"]
    _write_job(jobs, job)
    threading.Thread(
        target=_renew_job_lease,
        kwargs={"jobs": jobs, "job_id": job_id, "proc": proc, "owner_id": owner_id},
        daemon=True,
    ).start()
    threading.Thread(
        target=_record_exit,
        args=(job_id, jobs, proc, datasets_root),
        daemon=True,
    ).start()
    return job


def _spawn_dataset_job(
    *,
    job_id: str,
    runs_root: Path,
    datasets_root: Path | None,
    params: dict[str, Any],
    owner_id: str,
) -> dict[str, Any]:
    jobs = jobs_root(runs_root)
    job = _read_job(jobs, job_id)
    if job is None:
        raise KeyError(f"job {job_id} not found")
    if job.get("status") != "claiming" or (job.get("claim") or {}).get(
        "owner_id"
    ) != owner_id:
        raise RuntimeError(f"job {job_id} is no longer claimed by this worker")
    job_dir = jobs / job_id
    cmd = [
        sys.executable,
        "-m",
        "memory_data_service.cli",
        "generate",
        "--tier",
        str(params["tier"]),
        "--profile",
        str(params["profile"]),
        "--language",
        str(params.get("language") or "en"),
        "--pages",
        str(params["pages"]),
        "--formats",
        ",".join(params["formats"]),
        "--modalities",
        ",".join(params["modalities"]),
        "--dataset-id",
        str(job["dataset_id"]),
        "--title",
        str(params.get("display_name") or "LightRAG Synthetic Rich Memory Document"),
        "--display-name",
        str(params.get("display_name") or ""),
        "--output-root",
        str(datasets_root),
    ]
    if params.get("force"):
        cmd.append("--force")
    if params.get("allow_oversized_generation"):
        cmd.append("--allow-oversized-generation")
    with open(job_dir / "run.log", "a", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            cmd,
            cwd=_REPO_ROOT,
            env=dict(os.environ),
            start_new_session=True,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
    job.update(
        {
            "pid": proc.pid,
            "process_started_at": _probe_process_start(proc.pid),
            "status": "running",
            "started_at": _now_iso(),
            "supervise": False,
        }
    )
    job["claim"]["lease_expires_at"] = _lease_expires_at()
    job["lease_expires_at"] = job["claim"]["lease_expires_at"]
    _write_job(jobs, job)
    threading.Thread(
        target=_renew_job_lease,
        kwargs={"jobs": jobs, "job_id": job_id, "proc": proc, "owner_id": owner_id},
        daemon=True,
    ).start()
    threading.Thread(
        target=_record_exit,
        args=(job_id, jobs, proc, datasets_root),
        daemon=True,
    ).start()
    return job


def _dispatch(runs_root: Path, datasets_root: Path | None = None) -> None:
    """Dispatch independent FIFO queues for runs and dataset generation.

    A ``run`` owns a local LightRAG/Ollama execution unit and is intentionally
    capacity-limited.  A ``dataset`` job only generates deterministic source
    files, so it has a separate capacity and can run while a model evaluation
    is active.  The job records remain in one directory for audit and cancel
    operations; scheduling is separated by ``kind``.
    """
    if os.getenv("LIGHTRAG_DISABLE_EVAL_JOBS"):
        # Isolated evaluation child servers must not claim or spawn jobs;
        # only the main API server owns the queue.
        return
    _start_dispatch_loop(runs_root, datasets_root)
    with _DISPATCH_LOCK:
        datasets_root = datasets_root or _default_datasets_root(runs_root)
        jobs = jobs_root(runs_root)
        with _claim_file_lock(runs_root):
            # Re-scan after each launch so both per-kind capacities are real,
            # rather than a misleading one-job-per-dispatch cap.
            while True:
                raw = [
                    _refresh_job(
                        j,
                        runs_root=runs_root,
                        datasets_root=datasets_root,
                        recover_expired_claim=True,
                    )
                    for j in _raw_jobs(runs_root)
                ]
                active = {
                    kind: [
                        job
                        for job in raw
                        if job.get("kind") == kind
                        and job.get("status") in _ACTIVE_STATUSES
                    ]
                    for kind in ("run", "dataset")
                }
                pending = {
                    kind: sorted(
                        (
                            job
                            for job in raw
                            if job.get("kind") == kind
                            and job.get("status") == "pending"
                        ),
                        key=lambda job: (
                            job.get("created_at") or "",
                            job.get("id") or "",
                        ),
                    )
                    for kind in ("run", "dataset")
                }
                launchable: list[dict[str, Any]] = []
                if (
                    not _hold_blocks(runs_root)
                    and len(active["run"]) < _max_active_jobs()
                    and pending["run"]
                ):
                    launchable.append(pending["run"][0])
                if (
                    len(active["dataset"]) < _max_active_dataset_jobs()
                    and pending["dataset"]
                ):
                    launchable.append(pending["dataset"][0])
                if not launchable:
                    return
                # Keep chronological fairness when both independent queues
                # have capacity, while never allowing one kind to consume the
                # other kind's slot.
                job = min(
                    launchable,
                    key=lambda candidate: (
                        candidate.get("created_at") or "",
                        candidate.get("id") or "",
                    ),
                )
                claim = _claim_owner()
                job.update(
                    {
                        "status": "claiming",
                        "claim": claim,
                        "lease_expires_at": claim["lease_expires_at"],
                    }
                )
                _write_job(jobs, job)
                try:
                    if job["kind"] == "run":
                        _spawn_run_job(
                            job_id=job["id"],
                            runs_root=runs_root,
                            datasets_root=datasets_root,
                            params=_params_from_json(job["params"]),
                            supervise=bool(job.get("supervise", False)),
                            supervision=str(job.get("supervision") or "auto"),
                            stale_minutes=int(job.get("stale_minutes") or 60),
                            max_restarts=int(job.get("max_restarts") or 3),
                            poll_seconds=int(job.get("poll_seconds") or 30),
                            owner_id=claim["owner_id"],
                        )
                    else:
                        _spawn_dataset_job(
                            job_id=job["id"],
                            runs_root=runs_root,
                            datasets_root=datasets_root,
                            params=job["params"],
                            owner_id=claim["owner_id"],
                        )
                except Exception as exc:
                    # A broken queued job must not block the rest of the queue.
                    failed = _read_job(jobs, job["id"])
                    if failed is not None:
                        failed["status"] = "failed"
                        failed["finished_at"] = _now_iso()
                        failed["failure"] = f"{type(exc).__name__}: {exc}"
                        _write_job(jobs, failed)
                    if job["kind"] == "run":
                        mark_envelope_failed(
                            Path(job["output_dir"]),
                            failure=build_failure(
                                phase="dispatch",
                                error=exc,
                                retryable=True,
                                recommendation="inspect the job failure and retry the evaluation",
                                log_offset=0,
                            ),
                            runs_root=runs_root,
                        )


def _start_dispatch_loop(runs_root: Path, datasets_root: Path | None = None) -> None:
    """Daemon poller so queued jobs auto-start when a hold gate clears."""
    if os.getenv("LIGHTRAG_DISABLE_EVAL_JOBS"):
        return
    global _DISPATCH_LOOP_STARTED
    if _DISPATCH_LOOP_STARTED:
        return
    _DISPATCH_LOOP_STARTED = True
    datasets_root = datasets_root or _default_datasets_root(runs_root)

    def _loop() -> None:
        while True:
            time.sleep(15)
            try:
                _dispatch(runs_root, datasets_root)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                logger.error("eval job dispatch loop failed: %s", exc)

    threading.Thread(target=_loop, daemon=True).start()


def resume_pending_jobs(
    *,
    runs_root: Path,
    datasets_root: Path | None = None,
    delay_seconds: float = 0,
) -> None:
    """Resume durable pending jobs after an API-server restart.

    Job records outlive the in-process dispatcher.  A queued job can therefore
    remain pending when the server that created it exits while a hold gate is
    active.  Start the poller again and dispatch once the replacement server is
    ready to accept the run's API calls.
    """
    datasets_root = datasets_root or _default_datasets_root(runs_root)
    _start_dispatch_loop(runs_root, datasets_root)

    if delay_seconds <= 0:
        _dispatch(runs_root, datasets_root)
        return

    timer = threading.Timer(delay_seconds, _dispatch, args=(runs_root, datasets_root))
    timer.daemon = True
    timer.start()


def start_run_job(
    *,
    runs_root: Path,
    datasets_root: Path | None = None,
    params: RunParams,
    supervise: bool,
    supervision: str,
    stale_minutes: int,
    max_restarts: int,
    poll_seconds: int,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    jobs_root(runs_root).mkdir(parents=True, exist_ok=True)
    params.output_dir = (
        Path(output_dir)
        if output_dir
        else _unique_run_dir(runs_root)
    )
    params.output_dir.mkdir(parents=True, exist_ok=True)
    _write_queued_run_envelope(runs_root=runs_root, params=params)
    job = {
        "id": _job_id("run"),
        "kind": "run",
        "evaluation": "end_to_end",
        "label": params.label,
        "dataset": str(params.dataset),
        "output_dir": str(params.output_dir),
        "supervise": bool(supervise),
        "status": "pending",
        "created_at": _now_iso(),
        "params": _params_to_json(params),
        "supervision": supervision,
        "stale_minutes": stale_minutes,
        "max_restarts": max_restarts,
        "poll_seconds": poll_seconds,
        "claim": None,
        "lease_expires_at": None,
        "events_path": str(params.output_dir / "events.jsonl"),
    }
    _write_job(jobs_root(runs_root), job)
    _dispatch(runs_root, datasets_root)
    refreshed = _read_job(jobs_root(runs_root), job["id"])
    return refreshed if refreshed is not None else job


def start_dataset_job(
    *,
    runs_root: Path,
    datasets_root: Path | None = None,
    dataset_id: str | None,
    tier: str,
    profile: str,
    pages: int,
    formats: list[str],
    modalities: list[str],
    display_name: str = "",
    language: str = "en",
    force: bool = False,
    allow_oversized_generation: bool = False,
) -> dict[str, Any]:
    if dataset_id is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", dataset_id):
        raise ValueError("invalid dataset_id")
    if language not in {"en", "zh"}:
        raise ValueError("invalid dataset language")
    if pages > DEFAULT_DATASET_PAGE_CAP and not allow_oversized_generation:
        raise ValueError(
            f"pages {pages} exceeds default cap {DEFAULT_DATASET_PAGE_CAP}; "
            "set allow_oversized_generation to override"
        )
    job_dir = jobs_root(runs_root) / _job_id("dataset")
    job_dir.mkdir(parents=True, exist_ok=True)
    resolved_dataset_id = dataset_id
    if resolved_dataset_id is None:
        # The WebUI creates datasets by name without a dataset_id.  With force
        # checked, "same name" must mean "overwrite that dataset", otherwise
        # every generation silently becomes a new duplicate directory.
        if force and display_name:
            datasets_root = datasets_root or _default_datasets_root(runs_root)
            try:
                existing = [
                    summary
                    for summary in list_datasets(datasets_root)
                    if (summary.display_name or "").strip()
                    == display_name.strip()
                ]
            except (OSError, ValueError):
                existing = []
            if existing:
                resolved_dataset_id = sorted(
                    existing, key=lambda summary: summary.created_at
                )[-1].dataset_id
        resolved_dataset_id = resolved_dataset_id or job_dir.name
    job = {
        "id": job_dir.name,
        "kind": "dataset",
        "dataset_id": resolved_dataset_id,
        "display_name": display_name,
        "output_dir": str(job_dir),
        "supervise": False,
        "status": "pending",
        "created_at": _now_iso(),
        "params": {
            "tier": tier,
            "profile": profile,
            "language": language,
            "display_name": display_name,
            "pages": pages,
            "formats": formats,
            "modalities": modalities,
            "force": force,
            "allow_oversized_generation": allow_oversized_generation,
        },
        "claim": None,
        "lease_expires_at": None,
        "events_path": str(job_dir / "events.jsonl"),
    }
    _write_job(jobs_root(runs_root), job)
    _dispatch(runs_root, datasets_root)
    refreshed = _read_job(jobs_root(runs_root), job["id"])
    return refreshed if refreshed is not None else job


def list_jobs(*, runs_root: Path, datasets_root: Path) -> list[dict[str, Any]]:
    with _claim_file_lock(runs_root):
        jobs = [
            _refresh_job(job, runs_root=runs_root, datasets_root=datasets_root)
            for job in _raw_jobs(runs_root)
        ]
    jobs = sorted(
        jobs,
        key=lambda job: (job.get("created_at") or "", job.get("id") or ""),
        reverse=True,
    )
    active_counts = {
        kind: sum(
            1
            for job in jobs
            if job.get("kind") == kind and job.get("status") == "running"
        )
        for kind in ("run", "dataset")
    }
    positions: dict[str, int] = {}
    for kind in ("run", "dataset"):
        pending = sorted(
            (
                job
                for job in jobs
                if job.get("kind") == kind and job.get("status") == "pending"
            ),
            key=lambda job: (job.get("created_at") or "", job.get("id") or ""),
        )
        positions.update(
            {job["id"]: index for index, job in enumerate(pending, start=1)}
        )
    for job in jobs:
        job["active_count"] = active_counts.get(job.get("kind"), 0)
        job["queue_position"] = positions.get(job["id"])
    return jobs


def delete_job(*, runs_root: Path, job_id: str) -> bool:
    """Remove a job's audit directory (``runs/.jobs/<job_id>``)."""
    if not _valid_job_id(job_id):
        return False
    with _claim_file_lock(runs_root):
        target = jobs_root(runs_root) / job_id
        if not target.exists():
            return False
        shutil.rmtree(target)
        return True


def _tracked_child_pids(job: dict[str, Any]) -> list[int]:
    """Read a verified supervisor child group, refusing stale PID records."""
    output_dir = job.get("output_dir")
    if not isinstance(output_dir, str):
        return []
    try:
        payload = json.loads((Path(output_dir) / ".supervise-child.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    pid = payload.get("pid")
    pgid = payload.get("pgid")
    started = payload.get("process_started_at")
    if (
        not isinstance(pid, int)
        or pid <= 0
        or not isinstance(pgid, int)
        or pgid != pid
        or not isinstance(started, int)
    ):
        return []
    return [pgid] if _probe_process_start(pid) == started else []


def wait_job_exit(job: dict[str, Any], timeout: float = 35.0) -> bool:
    """Poll until the job and any separately-sessioned supervisor child exit."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        children_alive = any(_pid_alive(pid) for pid in _tracked_child_pids(job))
        if job_liveness(job) != "alive" and not children_alive:
            return True
        time.sleep(0.5)
    return False


def get_job(
    *, runs_root: Path, datasets_root: Path, job_id: str
) -> dict[str, Any] | None:
    if not _valid_job_id(job_id):
        return None
    with _claim_file_lock(runs_root):
        job = _read_job(jobs_root(runs_root), job_id)
        if job is None:
            return None
        return _refresh_job(job, runs_root=runs_root, datasets_root=datasets_root)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _terminate_process_tree(pid: int, extra_pids: list[int] | None = None) -> None:
    process_groups = {pid, *(extra_pids or [])}
    for group_id in process_groups:
        try:
            os.killpg(group_id, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    def _escalate() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if not any(_pid_alive(group_id) for group_id in process_groups):
                return
            time.sleep(0.5)
        for group_id in process_groups:
            try:
                os.killpg(group_id, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    threading.Thread(target=_escalate, daemon=True).start()


def cancel_job(
    *,
    runs_root: Path,
    datasets_root: Path,
    job_id: str,
) -> dict[str, Any] | None:
    if not _valid_job_id(job_id):
        return None
    with _claim_file_lock(runs_root):
        job = _read_job(jobs_root(runs_root), job_id)
        if job is None:
            return None
        job = _refresh_job(job, runs_root=runs_root, datasets_root=datasets_root)
        if job["status"] in {"pending", "claiming"}:
            job["status"] = "cancelled"
            job["finished_at"] = _now_iso()
            _write_job(jobs_root(runs_root), job)
            return job
        if job["status"] in _TERMINAL_STATUSES:
            return job
        pid = job.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            job["status"] = "failed"
            job["finished_at"] = _now_iso()
            _write_job(jobs_root(runs_root), job)
            return job
        job["status"] = "cancelling"
        _write_job(jobs_root(runs_root), job)
    _terminate_process_tree(pid, _tracked_child_pids(job))
    return job


def active_dataset_job(
    *,
    runs_root: Path,
    datasets_root: Path,
    dataset_id: str,
) -> dict[str, Any] | None:
    for job in list_jobs(runs_root=runs_root, datasets_root=datasets_root):
        if (
            job.get("kind") == "dataset"
            and job.get("dataset_id") == dataset_id
            and job.get("status") == "running"
        ):
            return job
    return None


def job_log_tail(job: dict[str, Any], lines: int = 200) -> list[str]:
    log_path = Path(job["output_dir"]) / "run.log"
    try:
        content = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return content[-lines:]
