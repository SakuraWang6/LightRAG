"""Unified experiment CLI: ``python -m memory_eval_tests.experiments.run``.

Every experiment shares the same conditions, envelope writer and progress
supervision; each registered spec only implements its runner and defaults.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from memory_eval_tests.experiments.common import (
    BASELINE_DEFAULTS,
    RunContext,
    build_execution_manifest,
    capture_runtime_snapshot,
    capture_environment,
    redact_launch_extra,
    write_envelope,
    write_progress,
)
from memory_eval_tests.experiments.registry import get_spec

_LAUNCH_KEYS = (
    "model",
    "mode",
    "top_k",
    "chunk_top_k",
    "max_cases",
    "num_ctx",
    "num_predict",
    "temperature",
    "engine",
    "kg",
)


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _launch_params(baseline: dict[str, Any], extra_items: list[str]) -> dict[str, Any]:
    """Snapshot the launch-time parameters for exact one-click reproduction.

    Written only by the harness (run.py) into the envelope; offline/direct
    writers do not emit it and the console falls back to conditions.
    """
    params: dict[str, Any] = {
        key: baseline[key] for key in _LAUNCH_KEYS if key in baseline
    }
    params["extra"] = redact_launch_extra(list(extra_items))
    return params


def _parameter_sources(args: argparse.Namespace, baseline: dict[str, Any]) -> dict[str, str]:
    """Make default/template/user provenance explicit for every launch value."""
    sources = {key: "default" for key in baseline}
    for key, value in (
        ("model", args.model),
        ("mode", args.mode),
        ("top_k", args.top_k),
        ("chunk_top_k", args.chunk_top_k),
        ("num_ctx", args.num_ctx),
        ("num_predict", args.num_predict),
        ("temperature", args.temperature),
        ("engine", args.engine),
    ):
        if value is not None:
            sources[key] = "user"
    if args.skip_kg:
        sources["kg"] = "user"
    if args.max_cases:
        sources["max_cases"] = "user"
    return sources


def _heartbeat_loop(output_dir: Path, stop: threading.Event) -> None:
    """Touch ``.heartbeat`` every 30s while the runner process is alive.

    The supervisor treats this as a liveness signal: it proves the interpreter
    is alive (and not GIL-blocked), not that the runner is making progress.
    """
    while not stop.is_set():
        try:
            (output_dir / ".heartbeat").write_text(
                datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        stop.wait(30)


def _install_sigterm_handler(output_dir: Path) -> None:
    def _handle(signum: int, frame) -> None:
        _log(output_dir, "SIGTERM received; marking progress and interrupting runner")
        write_progress(
            output_dir,
            status="terminating",
            done=0,
            total=1,
            phase="terminating",
            message="SIGTERM received",
        )
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle)


def _log(output_dir: Path, message: str) -> None:
    """Append a timestamped harness-level line to ``run.log``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "run.log").open("a", encoding="utf-8") as handle:
        handle.write(
            f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {message}\n"
        )


class _Tee:
    """Write through to the real stream while appending to run.log."""

    def __init__(self, real, log) -> None:
        self.real = real
        self.log = log

    def write(self, data: str) -> int:
        self.real.write(data)
        self.log.write(data)
        return len(data)

    def flush(self) -> None:
        self.real.flush()
        self.log.flush()

    def isatty(self) -> bool:
        return self.real.isatty()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)


@contextmanager
def _tee_log(output_dir: Path) -> Iterator[None]:
    """Capture the runner's stdout/stderr into ``run.log`` during execution."""
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "run.log").open("a", encoding="utf-8") as log:
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = _Tee(old_out, log)
        sys.stderr = _Tee(old_err, log)
        try:
            yield
        finally:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            finally:
                sys.stdout, sys.stderr = old_out, old_err


def main() -> None:
    parser = argparse.ArgumentParser(description="Standardized evaluation harness")
    parser.add_argument("--experiment", required=True, help="Registered experiment id")
    parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="Dataset directory under memory_data_service/generated",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Run directory (run.json + report.md + progress.json are written here)",
    )
    parser.add_argument(
        "--run-id", default=None, help="Optional run id (default: output-dir name)"
    )
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
        "--api-key",
        default=None,
        help="X-API-Key for the LightRAG API (defaults to LIGHTRAG_API_KEY).",
    )
    parser.add_argument(
        "--access-token",
        default=None,
        help="Bearer access token for the LightRAG API (defaults to LIGHTRAG_ACCESS_TOKEN).",
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=None,
        help="Runs root used for scan-index invalidation (defaults to MEMORY_EVAL_RUNS_ROOT or repo runs/).",
    )
    parser.add_argument(
        "--restart-count",
        type=int,
        default=0,
        help="Number of times the supervisor has restarted this run (display metadata).",
    )
    parser.add_argument(
        "--original-started-at",
        default=None,
        help="Original started_at carried across supervisor restarts.",
    )
    parser.add_argument(
        "--heartbeat",
        action="store_true",
        help="Touch output_dir/.heartbeat every 30s so a supervisor can detect liveness.",
    )
    parser.add_argument(
        "--restart-mode",
        choices=("none", "resume", "fresh"),
        default="none",
        help="How the last supervisor restart behaved (display metadata only).",
    )
    parser.add_argument("--storage-dir", type=Path, default=None)
    parser.add_argument("--engine", default=None)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument(
        "--skip-kg",
        action="store_true",
        help="Disable KG extraction (isolated storage required)",
    )
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Experiment-specific option",
    )

    args = parser.parse_args()
    spec = get_spec(args.experiment)
    baseline = dict(spec.default_baseline)
    baseline.update({k: v for k, v in BASELINE_DEFAULTS.items() if k not in baseline})
    for key, value in (
        ("model", args.model),
        ("mode", args.mode),
        ("top_k", args.top_k),
        ("chunk_top_k", args.chunk_top_k),
        ("num_ctx", args.num_ctx),
        ("num_predict", args.num_predict),
        ("temperature", args.temperature),
        ("engine", args.engine),
    ):
        if value is not None:
            baseline[key] = value
    if args.skip_kg:
        baseline["kg"] = False
    baseline["max_cases"] = args.max_cases

    extra: dict[str, str] = {}
    for item in args.extra:
        key, _, value = item.partition("=")
        extra[key.strip()] = value.strip()
    launch_params = _launch_params(baseline, args.extra)
    parameter_sources = _parameter_sources(args, baseline)

    storage_dir = args.storage_dir or (args.output_dir / "rag_storage")
    # The in-process LightRAG instance resolves its working directory from the
    # environment; pin it to this run's storage so every experiment reads the
    # right index/cache regardless of the launching shell.
    os.environ["WORKING_DIR"] = str(storage_dir)
    environment = capture_environment(
        rag_api_url=args.rag_api_url,
        ollama_url=args.ollama_url,
        api_key=args.api_key,
        access_token=args.access_token,
        storage_dir=str(storage_dir),
    )
    run_id = args.run_id or args.output_dir.name
    runs_root = args.runs_root or Path(
        os.getenv(
            "MEMORY_EVAL_RUNS_ROOT",
            str(Path(__file__).resolve().parents[2] / "memory_eval_tests" / "runs"),
        )
    )
    context = RunContext(
        spec=spec,
        dataset=args.dataset,
        output_dir=args.output_dir,
        baseline=baseline,
        environment=environment,
        variables=spec.variables,
        run_id=run_id,
        extra=extra,
        restarts=args.restart_count,
        last_restart_resume=(
            None if args.restart_mode == "none" else args.restart_mode == "resume"
        ),
        started_at=args.original_started_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    context.execution_manifest = build_execution_manifest(
        dataset=args.dataset,
        experiment_id=spec.id,
        experiment_type=spec.kind,
        parameters=baseline,
        parameter_sources=parameter_sources,
        started_at=context.started_at,
    )
    context.runtime_snapshot = capture_runtime_snapshot(
        rag_api_url=args.rag_api_url,
        api_key=args.api_key,
        access_token=args.access_token,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _install_sigterm_handler(args.output_dir)
    _log(
        args.output_dir,
        f"starting experiment={spec.id} run_id={run_id} dataset={args.dataset}",
    )
    # Publish an initial envelope immediately so the console can supervise the
    # run while it is in progress; the final write below replaces it.
    write_envelope(
        args.output_dir,
        context=context,
        status="running",
        methods=[],
        report_rel_path=None,
        write_progress_file=False,
        runs_root=runs_root,
        extra={"launch_params": launch_params},
    )
    write_progress(args.output_dir, status="queued", done=0, total=1, phase="starting")
    stop_heartbeat = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    if args.heartbeat:
        heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            args=(args.output_dir, stop_heartbeat),
            daemon=True,
        )
        heartbeat_thread.start()
    with _tee_log(args.output_dir):
        try:
            payload = spec.runner(context)
            methods = payload.get("methods", [])
            report_md = payload.get("report", "")
            report_path = args.output_dir / "report.md"
            report_path.write_text(report_md, encoding="utf-8")
            status = payload.get("status", "complete")
            write_envelope(
                args.output_dir,
                context=context,
                status=status,
                methods=methods,
                report_rel_path=report_path.name,
                extra={
                    **(payload.get("extra") or {}),
                    "launch_params": launch_params,
                },
                runs_root=runs_root,
            )
            _log(
                args.output_dir,
                f"finished status={status} cases={sum(len(m.get('results') or []) for m in methods)}",
            )
        except Exception as exc:  # keep the failure visible for the console monitor
            _log(args.output_dir, f"failed {type(exc).__name__}: {exc}")
            write_progress(
                args.output_dir,
                status="failed",
                done=0,
                total=1,
                phase="failed",
                message=f"{type(exc).__name__}: {exc}",
            )
            print(traceback.format_exc())
            raise
        finally:
            stop_heartbeat.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=2)


if __name__ == "__main__":
    main()
