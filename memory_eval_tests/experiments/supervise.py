"""Supervise a long evaluation run: auto-restart on crash or stale progress.

The experiment itself is run as a child process so the supervisor can:

* detect an abrupt crash (child exit code != 0) and restart it with resume
  (runners persist ``partial.json`` and resume automatically);
* detect a hidden stall (no ``progress.json`` update for ``--stale-minutes``
  while the child is still alive) and restart it;
* give up after ``--max-restarts`` failures so a genuinely broken run does not
  loop forever.

Run it under ``launchctl`` with ``KeepAlive`` (``SuccessfulExit: false``) for a
second OS-level restart layer that survives terminal/session reaping.
"""

from __future__ import annotations

import argparse
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


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(output_dir: Path, message: str) -> None:
    line = f"{_utcnow()} {message}"
    print(line, flush=True)
    try:
        with open(output_dir / "supervise.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _progress_mtime(output_dir: Path) -> float | None:
    try:
        return (output_dir / "progress.json").stat().st_mtime
    except OSError:
        return None


def _build_command(args: argparse.Namespace) -> list[str]:
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
    if args.skip_kg:
        cmd.append("--skip-kg")
    for extra in args.extra:
        cmd.extend(["--extra", extra])
    return cmd


def _child_env(args: argparse.Namespace) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _PROXY_KEYS}
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    if args.storage_dir is not None:
        env["WORKING_DIR"] = str(args.storage_dir)
    return env


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30)
    except (subprocess.TimeoutExpired, ProcessLookupError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--mode", default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--chunk-top-k", type=int, default=None)
    parser.add_argument("--num-ctx", type=int, default=None)
    parser.add_argument("--num-predict", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--rag-api-url", default="http://127.0.0.1:9621")
    parser.add_argument("--storage-dir", type=Path, default=None)
    parser.add_argument("--engine", default=None)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--skip-kg", action="store_true")
    parser.add_argument("--extra", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--max-restarts", type=int, default=3)
    parser.add_argument("--stale-minutes", type=int, default=25)
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = _build_command(args)
    env = _child_env(args)
    attempts = 0
    while True:
        attempts += 1
        _log(output_dir, f"start attempt {attempts}: {' '.join(cmd)}")
        proc = subprocess.Popen(cmd, cwd=_REPO_ROOT, env=env)
        last_progress = _progress_mtime(output_dir)
        while proc.poll() is None:
            time.sleep(args.poll_seconds)
            current = _progress_mtime(output_dir)
            if current is not None and (last_progress is None or current > last_progress):
                last_progress = current
                continue
            base = current if current is not None else last_progress
            if base is not None and time.time() - base > args.stale_minutes * 60:
                _log(
                    output_dir,
                    f"stale progress (no update for {args.stale_minutes}m); killing child",
                )
                _terminate(proc)
                break
        code = proc.wait()
        _log(output_dir, f"attempt {attempts} exited with code {code}")
        if code == 0:
            _log(output_dir, "run finished successfully")
            return 0
        if attempts > args.max_restarts:
            _log(output_dir, f"giving up after {args.max_restarts} restarts")
            return 1
        _log(output_dir, "restarting in 10s (resume from partial.json)")
        time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())
