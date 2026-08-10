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
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_eval_tests.experiments.supervise import (
    RunParams,
    build_run_command,
    build_supervise_command,
)

_JOBS_DIR = ".jobs"
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_PAGE_CAP = 1000
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_DISPATCH_LOCK = threading.Lock()
_DISPATCH_LOOP_STARTED = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _max_active_jobs() -> int:
    raw = os.getenv("MEMORY_EVAL_MAX_ACTIVE_JOBS")
    if raw and raw.strip().isdigit():
        return max(1, int(raw.strip()))
    return 1


def _hold_blocks(runs_root: Path) -> bool:
    """True while MEMORY_EVAL_WAIT_FOR_RUN has not reached status complete."""
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
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
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
        ).stdout.strip()
        if out:
            started = datetime.strptime(out, "%a %b %d %H:%M:%S %Y")
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
    if job.get("status") in {"canceled", "stale", "pending"}:
        return job["status"]
    liveness = job_liveness(job)
    if liveness == "reused":
        return "stale"
    if liveness == "alive":
        return "running"
    exit_code = job.get("exit_code")
    if job.get("kind") == "dataset":
        dataset_id = job.get("dataset_id")
        manifest = datasets_root / str(dataset_id) / "manifest.json"
        if exit_code is not None:
            return "succeeded" if exit_code == 0 and manifest.exists() else "failed"
        return "succeeded" if manifest.exists() else "failed"
    try:
        envelope = json.loads(
            (Path(job["output_dir"]) / "run.json").read_text(encoding="utf-8")
        )
        status = envelope.get("status")
    except (OSError, ValueError):
        status = None
    if exit_code is not None:
        return "succeeded" if exit_code == 0 else "failed"
    return "succeeded" if status == "complete" else "failed"


def _refresh_job(
    job: dict[str, Any],
    *,
    runs_root: Path,
    datasets_root: Path,
) -> dict[str, Any]:
    job["status"] = _derive_status(
        job, runs_root=runs_root, datasets_root=datasets_root
    )
    if job["status"] in {"succeeded", "failed", "canceled", "stale"} and not job.get(
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
    job = _read_job(jobs_root, job_id)
    if job is not None:
        job["exit_code"] = code
        _write_job(jobs_root, job)
    # A finished job frees a slot: start the next queued job, if any.
    _dispatch(
        jobs_root.parent,
        datasets_root=datasets_root or _default_datasets_root(jobs_root.parent),
    )


def _unique_run_dir(runs_root: Path, experiment: str) -> Path:
    while True:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = runs_root / f"{experiment}-{ts}-{uuid.uuid4().hex[:4]}"
        if not candidate.exists():
            return candidate


def _params_from_json(payload: dict[str, Any]) -> RunParams:
    data = dict(payload)
    for key in ("dataset", "output_dir", "runs_root", "storage_dir"):
        if data.get(key) is not None:
            data[key] = Path(data[key])
    data["extra"] = list(data.get("extra") or [])
    return RunParams(**data)


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
) -> dict[str, Any]:
    jobs = jobs_root(runs_root)
    job = _read_job(jobs, job_id)
    if job is None:
        raise KeyError(f"job {job_id} not found")
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
    _write_job(jobs, job)
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
) -> dict[str, Any]:
    jobs = jobs_root(runs_root)
    job = _read_job(jobs, job_id)
    if job is None:
        raise KeyError(f"job {job_id} not found")
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
        "--pages",
        str(params["pages"]),
        "--formats",
        ",".join(params["formats"]),
        "--modalities",
        ",".join(params["modalities"]),
        "--dataset-id",
        str(job["dataset_id"]),
        "--output-root",
        str(datasets_root),
    ]
    if params.get("force"):
        cmd.append("--force")
    if params.get("allow_oversized_generation"):
        cmd.append("--allow-oversized-generation")
    log_handle = open(job_dir / "run.log", "a", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=_REPO_ROOT,
        env=dict(os.environ),
        start_new_session=True,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    log_handle.close()
    job.update(
        {
            "pid": proc.pid,
            "process_started_at": _probe_process_start(proc.pid),
            "status": "running",
            "started_at": _now_iso(),
            "supervise": False,
        }
    )
    _write_job(jobs, job)
    threading.Thread(
        target=_record_exit,
        args=(job_id, jobs, proc, datasets_root),
        daemon=True,
    ).start()
    return job


def _dispatch(runs_root: Path, datasets_root: Path | None = None) -> None:
    """Start the oldest pending job when a slot is free (FIFO queue)."""
    _start_dispatch_loop(runs_root, datasets_root)
    with _DISPATCH_LOCK:
        datasets_root = datasets_root or _default_datasets_root(runs_root)
        if _hold_blocks(runs_root):
            return
        jobs = jobs_root(runs_root)
        while True:
            raw = [
                _refresh_job(j, runs_root=runs_root, datasets_root=datasets_root)
                for j in _raw_jobs(runs_root)
            ]
            active = [j for j in raw if j.get("status") == "running"]
            if len(active) >= _max_active_jobs():
                return
            pending = [j for j in raw if j.get("status") == "pending"]
            if not pending:
                return
            job = pending[0]
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
                    )
                else:
                    _spawn_dataset_job(
                        job_id=job["id"],
                        runs_root=runs_root,
                        datasets_root=datasets_root,
                        params=job["params"],
                    )
                return
            except Exception:
                # A broken queued job must not block the rest of the queue.
                failed = _read_job(jobs, job["id"])
                if failed is not None:
                    failed["status"] = "failed"
                    failed["finished_at"] = _now_iso()
                    _write_job(jobs, failed)


def _start_dispatch_loop(runs_root: Path, datasets_root: Path | None = None) -> None:
    """Daemon poller so queued jobs auto-start when a hold gate clears."""
    global _DISPATCH_LOOP_STARTED
    if _DISPATCH_LOOP_STARTED:
        return
    _DISPATCH_LOOP_STARTED = True
    datasets_root = datasets_root or _default_datasets_root(runs_root)

    def _loop() -> None:
        while True:
            time.sleep(60)
            try:
                _dispatch(runs_root, datasets_root)
            except Exception:
                pass

    threading.Thread(target=_loop, daemon=True).start()


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
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    jobs_root(runs_root).mkdir(parents=True, exist_ok=True)
    params.output_dir = Path(output_dir) if output_dir else _unique_run_dir(runs_root, params.experiment)
    params.output_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "id": _job_id("run"),
        "kind": "run",
        "experiment": params.experiment,
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
    }
    _write_job(jobs_root(runs_root), job)
    _dispatch(runs_root, datasets_root)
    refreshed = _read_job(jobs_root(runs_root), job["id"])
    return refreshed if refreshed is not None else job


def start_dataset_job(
    *,
    runs_root: Path,
    datasets_root: Path | None = None,
    dataset_id: str,
    tier: str,
    profile: str,
    pages: int,
    formats: list[str],
    modalities: list[str],
    force: bool = False,
    allow_oversized_generation: bool = False,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", dataset_id):
        raise ValueError("invalid dataset_id")
    if pages > DEFAULT_DATASET_PAGE_CAP and not allow_oversized_generation:
        raise ValueError(
            f"pages {pages} exceeds default cap {DEFAULT_DATASET_PAGE_CAP}; "
            "set allow_oversized_generation to override"
        )
    job_dir = jobs_root(runs_root) / _job_id("dataset")
    job_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "id": job_dir.name,
        "kind": "dataset",
        "dataset_id": dataset_id,
        "output_dir": str(job_dir),
        "supervise": False,
        "status": "pending",
        "created_at": _now_iso(),
        "params": {
            "tier": tier,
            "profile": profile,
            "pages": pages,
            "formats": formats,
            "modalities": modalities,
            "force": force,
            "allow_oversized_generation": allow_oversized_generation,
        },
    }
    _write_job(jobs_root(runs_root), job)
    _dispatch(runs_root, datasets_root)
    refreshed = _read_job(jobs_root(runs_root), job["id"])
    return refreshed if refreshed is not None else job


def list_jobs(*, runs_root: Path, datasets_root: Path) -> list[dict[str, Any]]:
    jobs = [
        _refresh_job(job, runs_root=runs_root, datasets_root=datasets_root)
        for job in _raw_jobs(runs_root)
    ]
    return sorted(jobs, key=lambda job: job.get("started_at") or "", reverse=True)


def get_job(
    *, runs_root: Path, datasets_root: Path, job_id: str
) -> dict[str, Any] | None:
    if not _valid_job_id(job_id):
        return None
    job = _read_job(jobs_root(runs_root), job_id)
    if job is None:
        return None
    return _refresh_job(job, runs_root=runs_root, datasets_root=datasets_root)


def _terminate_process_tree(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    def _escalate() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.5)
        try:
            os.killpg(pid, signal.SIGKILL)
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
    job = _read_job(jobs_root(runs_root), job_id)
    if job is None:
        return None
    job = _refresh_job(job, runs_root=runs_root, datasets_root=datasets_root)
    if job["status"] == "pending":
        job["status"] = "canceled"
        job["finished_at"] = _now_iso()
        _write_job(jobs_root(runs_root), job)
        return job
    if job["status"] in {"succeeded", "failed", "canceled", "stale"}:
        return job
    pid = job.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        job["status"] = "failed"
        job["finished_at"] = _now_iso()
        _write_job(jobs_root(runs_root), job)
        return job
    _terminate_process_tree(pid)
    job["status"] = "canceled"
    job["finished_at"] = _now_iso()
    _write_job(jobs_root(runs_root), job)
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
