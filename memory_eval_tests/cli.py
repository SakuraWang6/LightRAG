"""Run one complete, isolated LightRAG evaluation."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_eval_tests.artifacts import (
    BASELINE_DEFAULTS,
    RunContext,
    append_run_event,
    build_execution_manifest,
    build_failure,
    capture_environment,
    read_progress,
    redact_launch_extra,
    redact_sensitive_text,
    selected_case_ids,
    write_envelope,
    write_progress,
)
from memory_eval_tests.workflow import definition

_LAUNCH_KEYS = (
    "model",
    "mode",
    "top_k",
    "chunk_top_k",
    "max_cases",
    "num_ctx",
    "num_predict",
    "max_total_tokens",
    "temperature",
    "engine",
    "kg",
)


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _launch_params(
    baseline: dict[str, Any], extra_items: list[str], case_ids: list[str] | None
) -> dict[str, Any]:
    """Snapshot the launch-time parameters for exact one-click reproduction.

    Written only by the product evaluation harness into its envelope.
    """
    params: dict[str, Any] = {
        key: baseline[key] for key in _LAUNCH_KEYS if key in baseline
    }
    params["extra"] = redact_launch_extra(list(extra_items))
    # The comparison contract must know the exact oracle subset, not merely
    # the requested max_cases cap. ``None`` deliberately represents an
    # unreadable oracle and prevents that run from being ranked.
    params["case_ids"] = case_ids
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
        ("max_total_tokens", args.max_total_tokens),
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
            f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {redact_sensitive_text(message)}\n"
        )


def _completed_case_count(methods: list[dict[str, Any]]) -> int:
    """Count answer sheets once for product E2E runs, not each pipeline stage."""
    answer_rows = [
        row
        for method in methods
        if method.get("method") == "answer"
        for row in (method.get("results") or [])
        if isinstance(row, dict)
    ]
    if answer_rows:
        return len(answer_rows)
    return sum(len(method.get("results") or []) for method in methods)


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
    parser = argparse.ArgumentParser(description="LightRAG end-to-end evaluation")
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
    parser.add_argument(
        "--label", default=None, help="User-facing evaluation name stored in the envelope."
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--mode", default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--chunk-top-k", type=int, default=None)
    parser.add_argument("--num-ctx", type=int, default=None)
    parser.add_argument("--num-predict", type=int, default=None)
    parser.add_argument("--max-total-tokens", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=None)
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
    parser.add_argument("--engine", default=None)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument(
        "--question-types",
        default=None,
        help="Comma-separated question types to evaluate (e.g. direct_numeric,table_cell).",
    )
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
        help="Evaluation-specific option",
    )

    args = parser.parse_args()
    baseline = dict(definition.default_baseline)
    baseline.update({k: v for k, v in BASELINE_DEFAULTS.items() if k not in baseline})
    for key, value in (
        ("model", args.model),
        ("mode", args.mode),
        ("top_k", args.top_k),
        ("chunk_top_k", args.chunk_top_k),
        ("num_ctx", args.num_ctx),
        ("num_predict", args.num_predict),
        ("max_total_tokens", args.max_total_tokens),
        ("temperature", args.temperature),
        ("engine", args.engine),
    ):
        if value is not None:
            baseline[key] = value
    if args.skip_kg:
        baseline["kg"] = False
        if args.mode is None:
            baseline["mode"] = "naive"
        elif args.mode != "naive":
            parser.error("--skip-kg requires --mode naive")
    baseline["max_cases"] = args.max_cases
    if args.question_types:
        baseline["question_types"] = [
            item.strip() for item in args.question_types.split(",") if item.strip()
        ]

    extra: dict[str, str] = {}
    for item in args.extra:
        key, _, value = item.partition("=")
        extra[key.strip()] = value.strip()
    case_ids = selected_case_ids(args.dataset, args.max_cases)
    launch_params = _launch_params(baseline, args.extra, case_ids)
    parameter_sources = _parameter_sources(args, baseline)

    environment = capture_environment(
        api_key=args.api_key,
        access_token=args.access_token,
    )
    run_id = args.run_id or args.output_dir.name
    runs_root = args.runs_root or Path(
        os.getenv(
            "MEMORY_EVAL_RUNS_ROOT",
            str(Path(__file__).resolve().parents[2] / "memory_eval_tests" / "runs"),
        )
    )
    context = RunContext(
        definition=definition,
        dataset=args.dataset,
        output_dir=args.output_dir,
        baseline=baseline,
        environment=environment,
        run_id=run_id,
        label=args.label,
        extra=extra,
        restarts=args.restart_count,
        last_restart_resume=(
            None if args.restart_mode == "none" else args.restart_mode == "resume"
        ),
        started_at=args.original_started_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        runs_root=runs_root,
    )
    context.execution_manifest = build_execution_manifest(
        dataset=args.dataset,
        evaluation_id=definition.id,
        evaluation_type="evaluation",
        parameters=baseline,
        parameter_sources=parameter_sources,
        started_at=context.started_at,
    )
    context.execution_manifest["case_selection"] = {
        "algorithm": "deterministic_even_stride_v1",
        "requested_max_cases": args.max_cases,
        "case_ids": case_ids,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    preflight_error: Exception | None = None
    if definition.prepare is not None:
        try:
            definition.prepare(context)
        except Exception as exc:
            preflight_error = exc
    context.runtime_snapshot = {
        "snapshot_version": "1.0",
        "status": "provisioning",
        "reason": "isolated execution unit has not started yet",
    }
    _install_sigterm_handler(args.output_dir)
    _log(
        args.output_dir,
        f"starting evaluation={definition.id} run_id={run_id} dataset={args.dataset}",
    )
    append_run_event(
        args.output_dir,
        phase="starting",
        severity="info",
        message=f"starting evaluation for dataset {args.dataset.name}",
    )
    # Publish an initial envelope immediately so the console can supervise the
    # run while it is in progress; the final write below replaces it.
    initial_extra: dict[str, Any] = {"launch_params": launch_params}
    initial_status = "running"
    if preflight_error is not None:
        offset = append_run_event(
            args.output_dir,
            phase="preflight",
            severity="error",
            message=f"{type(preflight_error).__name__}: {preflight_error}",
            error_type=type(preflight_error).__name__,
        )
        initial_status = "failed"
        initial_extra["failure"] = build_failure(
            phase=str(getattr(preflight_error, "phase", "preflight")),
            error=preflight_error,
            retryable=bool(getattr(preflight_error, "retryable", False)),
            recommendation="fix the environment profile or isolated execution unit before retrying",
            log_offset=offset,
        )
    write_envelope(
        args.output_dir,
        context=context,
        status=initial_status,
        methods=[],
        report_rel_path=None,
        write_progress_file=False,
        runs_root=runs_root,
        extra=initial_extra,
    )
    if preflight_error is not None:
        write_progress(
            args.output_dir,
            status="failed",
            done=0,
            total=1,
            phase="preflight",
            message=f"{type(preflight_error).__name__}: {preflight_error}",
        )
        raise preflight_error
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
            payload = definition.runner(context)
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
                write_progress_file=False,
            )
            _log(
                args.output_dir,
                f"finished status={status} cases={_completed_case_count(methods)}",
            )
            saved_progress = read_progress(args.output_dir)
            total = int(saved_progress.get("total") or 1)
            context.progress(
                status,
                total,
                total,
                "complete",
                f"run finished with status {status}",
            )
        except Exception as exc:  # keep the failure visible for the console monitor
            _log(args.output_dir, f"failed {type(exc).__name__}: {exc}")
            offset = append_run_event(
                args.output_dir,
                phase="execution",
                severity="error",
                message=f"{type(exc).__name__}: {exc}",
                error_type=type(exc).__name__,
            )
            failure = build_failure(
                phase=str(getattr(exc, "phase", "execution")),
                error=exc,
                retryable=bool(getattr(exc, "retryable", False)),
                recommendation=(
                    "retry after checking ingestion and the isolated execution unit"
                    if getattr(exc, "phase", "execution") == "ingestion"
                    else "inspect the error summary and run.log; fix the environment or input before retrying"
                ),
                log_offset=offset,
            )
            write_envelope(
                args.output_dir,
                context=context,
                status="failed",
                methods=[],
                extra={"launch_params": launch_params, "failure": failure},
                runs_root=runs_root,
                write_progress_file=False,
            )
            write_progress(
                args.output_dir,
                status="failed",
                done=0,
                total=1,
                phase="failed",
                message=f"{type(exc).__name__}: {exc}",
            )
            print(redact_sensitive_text(traceback.format_exc()))
            raise
        finally:
            stop_heartbeat.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=2)


if __name__ == "__main__":
    main()
