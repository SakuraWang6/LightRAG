"""Supervise a long evaluation run: auto-restart on crash, optional hang kill.

The experiment itself is run as a child process so the supervisor can:

* detect an abrupt crash (child exit code != 0) and restart it.  Runners that
  declare ``supports_resume`` persist ``partial.json`` and resume automatically;
* optionally detect a hidden interpreter hang via the child's ``.heartbeat``
  liveness file (``--supervision heartbeat``).  Hang detection is OFF by
  default: LLM stalls are already bounded by the chat-layer hard timeout, so
  crash-restart is the default capability and hang-kill is an explicit
  advanced option;
* give up after ``--max-restarts`` failures so a broken run does not loop
  forever.

The child is spawned in its own session (``start_new_session=True``) so signals
and termination reach the whole process tree (run.py and any subprocesses it
spawns, e.g. online_baseline's retrieval/answer children).  Restart continuity
is preserved across supervisor relaunches by inheriting ``started_at`` and
``restarts`` from the existing ``run.json``.

Run it under ``launchctl`` with ``KeepAlive`` (``SuccessfulExit: false``) for a
second OS-level restart layer that survives terminal/session reaping.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]

_PROXY_KEYS = (
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "no_proxy",
)

_SUPERVISION_CHOICES = ("auto", "none", "heartbeat")

# Mutable holder so signal handlers can reach the live child process.
_state: dict[str, Any] = {"proc": None}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(output_dir: Path, message: str) -> None:
    line = f"{_utcnow()} {message}"
    print(line, flush=True)
    try:
        with open(output_dir / "run.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _activity_mtime(output_dir: Path) -> float | None:
    """Liveness signal: max of ``.heartbeat`` and ``run.log`` mtimes.

    ``.heartbeat`` is the primary interpreter-alive signal; ``run.log`` growth
    is a real auxiliary signal (both are considered, so a child that emits
    output without touching the heartbeat still counts as alive).
    """
    mtimes: list[float] = []
    for name in (".heartbeat", "run.log"):
        try:
            mtimes.append((output_dir / name).stat().st_mtime)
        except OSError:
            continue
    return max(mtimes) if mtimes else None


def _stale(last_activity: float | None, now: float, stale_seconds: int) -> bool:
    """True when the last activity is at least ``stale_seconds`` old.

    ``None`` (no activity file yet) is a grace period, never stale.
    """
    if last_activity is None:
        return False
    return now - last_activity >= stale_seconds


def _should_monitor_stale(supervision: str) -> bool:
    return supervision == "heartbeat"


def _existing_run_state(output_dir: Path) -> tuple[str, int] | None:
    """Inherit ``started_at`` / ``restarts`` from an existing run.json."""
    try:
        payload = json.loads((output_dir / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    started_at = payload.get("started_at")
    if not started_at:
        return None
    return str(started_at), int(payload.get("restarts") or 0)


def _build_command(
    args: argparse.Namespace,
    *,
    heartbeat: bool,
    restart_count: int,
    original_started_at: str | None,
    restart_mode: str = "none",
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "memory_eval_tests.experiments.run",
        "--experiment",
        args.experiment,
        "--dataset",
        str(args.dataset),
        "--output-dir",
        str(args.output_dir),
    ]
    for key in (
        "model",
        "mode",
        "top_k",
        "chunk_top_k",
        "num_ctx",
        "num_predict",
        "temperature",
        "ollama_url",
        "rag_api_url",
        "storage_dir",
        "engine",
        "max_cases",
    ):
        value = getattr(args, key, None)
        if value is not None:
            cmd.append(f"--{key.replace('_', '-')}")
            cmd.append(str(value))
    for flag, value in (
        ("--api-key", args.api_key),
        ("--access-token", args.access_token),
        ("--runs-root", str(args.runs_root) if args.runs_root is not None else None),
        ("--run-id", args.run_id),
    ):
        if value is not None:
            cmd.append(flag)
            cmd.append(value)
    if heartbeat:
        cmd.append("--heartbeat")
    if restart_count > 0:
        cmd.append("--restart-count")
        cmd.append(str(restart_count))
    if original_started_at:
        cmd.append("--original-started-at")
        cmd.append(original_started_at)
    if restart_mode != "none":
        cmd.append("--restart-mode")
        cmd.append(restart_mode)
    if args.skip_kg:
        cmd.append("--skip-kg")
    for extra in args.extra:
        cmd.extend(["--extra", extra])
    return cmd


def _child_env(args: argparse.Namespace) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _PROXY_KEYS}
    if getattr(args, "keep_proxy", False):
        for key in _PROXY_KEYS:
            if key in os.environ:
                env[key] = os.environ[key]
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    if args.storage_dir is not None:
        env["WORKING_DIR"] = str(args.storage_dir)
    return env


def _terminate_tree(proc: subprocess.Popen) -> None:
    """Terminate the child's whole process group (SIGTERM -> 30s -> SIGKILL)."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=30)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _acquire_lock(output_dir: Path):
    """Fail fast when another supervise process monitors the same output dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    handle = open(output_dir / ".supervise.lock", "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SystemExit(
            f"another supervise process is already monitoring {output_dir}"
        ) from None
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def _install_signal_handlers(output_dir: Path) -> None:
    def _handle(signum: int, frame) -> None:
        proc = _state.get("proc")
        if proc is not None and proc.poll() is None:
            _log(output_dir, f"received signal {signum}; terminating process tree")
            _terminate_tree(proc)
        raise SystemExit(130 if signum == signal.SIGINT else 143)

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)


def _note_restart(output_dir: Path, attempt: int, resume: bool) -> None:
    """Transient progress hint; the envelope ``restarts`` field is authoritative."""
    try:
        payload = json.loads((output_dir / "progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {"status": "running", "done": 0, "total": 1, "phase": ""}
    payload["status"] = "running"
    payload["message"] = f"第 {attempt} 次重启（{'续跑' if resume else '从头重试'}）"
    payload["updated_at"] = _utcnow()
    try:
        (output_dir / "progress.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--mode", default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--chunk-top-k", type=int, default=None)
    parser.add_argument("--num-ctx", type=int, default=None)
    parser.add_argument("--num-predict", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--rag-api-url", default="http://127.0.0.1:9621")
    parser.add_argument(
        "--api-key", default=None, help="X-API-Key for the LightRAG API."
    )
    parser.add_argument(
        "--access-token",
        default=None,
        help="Bearer access token for the LightRAG API.",
    )
    parser.add_argument("--runs-root", type=Path, default=None)
    parser.add_argument(
        "--keep-proxy",
        action="store_true",
        help="Keep http(s)/all proxy env vars for the child (needed by "
        "experiments calling external APIs, e.g. frozen_prompt_llm_eval).",
    )
    parser.add_argument("--storage-dir", type=Path, default=None)
    parser.add_argument("--engine", default=None)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--skip-kg", action="store_true")
    parser.add_argument("--extra", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--max-restarts", type=int, default=3)
    parser.add_argument(
        "--supervision",
        choices=_SUPERVISION_CHOICES,
        default="auto",
        help="auto -> experiment spec; heartbeat enables hang detection via .heartbeat.",
    )
    parser.add_argument(
        "--stale-minutes",
        type=int,
        default=60,
        help="Heartbeat age that counts as stale in heartbeat mode.",
    )
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    lock = _acquire_lock(output_dir)

    from memory_eval_tests.experiments.registry import get_spec

    spec = get_spec(args.experiment)
    supervision = args.supervision if args.supervision != "auto" else spec.supervision
    monitor_stale = _should_monitor_stale(supervision)
    forward_heartbeat = supervision == "heartbeat"

    _install_signal_handlers(output_dir)
    _log(
        output_dir,
        f"supervise experiment={spec.id} supervision={supervision} "
        f"monitor_stale={monitor_stale} max_restarts={args.max_restarts}",
    )

    existing = _existing_run_state(output_dir)
    restart_count = existing[1] if existing else 0
    original_started_at = existing[0] if existing else None
    cmd = _build_command(
        args,
        heartbeat=forward_heartbeat,
        restart_count=restart_count,
        original_started_at=original_started_at,
        restart_mode="resume"
        if spec.supports_resume
        else "fresh"
        if restart_count > 0
        else "none",
    )
    env = _child_env(args)
    attempts = 0
    while True:
        attempts += 1
        resume_capable = spec.supports_resume
        _log(
            output_dir,
            f"start attempt {attempts}: {' '.join(cmd)}",
        )
        _log(
            output_dir,
            f"attempt {attempts} "
            f"{'resume from partial.json' if resume_capable else 'fresh start'}",
        )
        if attempts > 1:
            _note_restart(output_dir, attempts - 1, resume_capable)
        proc = subprocess.Popen(
            cmd,
            cwd=_REPO_ROOT,
            env=env,
            start_new_session=True,
        )
        _state["proc"] = proc
        last_activity = _activity_mtime(output_dir)
        while proc.poll() is None:
            time.sleep(args.poll_seconds)
            if not monitor_stale:
                continue
            current = _activity_mtime(output_dir)
            if current is not None and (
                last_activity is None or current > last_activity
            ):
                last_activity = current
                continue
            base = current if current is not None else last_activity
            if _stale(base, time.time(), args.stale_minutes * 60):
                _log(
                    output_dir,
                    f"stale heartbeat (no update for {args.stale_minutes}m); "
                    "terminating process tree",
                )
                _terminate_tree(proc)
                break
        code = proc.wait()
        _state["proc"] = None
        _log(output_dir, f"attempt {attempts} exited with code {code}")
        if code == 0:
            _log(output_dir, "run finished successfully")
            lock.close()
            return 0
        if attempts > args.max_restarts:
            _log(output_dir, f"giving up after {args.max_restarts} restarts")
            lock.close()
            return 1
        state = _existing_run_state(output_dir)
        if state is not None:
            if original_started_at is None:
                original_started_at = state[0]
            restart_count = state[1]
        restart_count += 1
        cmd = _build_command(
            args,
            heartbeat=forward_heartbeat,
            restart_count=restart_count,
            original_started_at=original_started_at,
            restart_mode="resume" if spec.supports_resume else "fresh",
        )
        _log(output_dir, "restarting in 10s")
        time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())
