"""Lifecycle helpers for one isolated LightRAG evaluation execution unit."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_eval_tests.experiments.common import capture_runtime_snapshot


class ExecutionUnitPrerequisiteError(RuntimeError):
    """A missing model backend that must fail before an evaluation is queued."""

    phase = "environment_not_ready"
    retryable = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def allocate_execution_unit(
    *, run_id: str, output_dir: Path, profile: dict[str, Any]
) -> dict[str, Any]:
    """Allocate identifiers and storage that cannot collide with another run."""
    suffix = uuid.uuid4().hex[:10]
    workspace_id = f"eval_{run_id.replace('-', '_')[:32]}_{suffix}"
    storage_id = f"storage-{suffix}"
    workspace_dir = output_dir / "isolated" / storage_id
    configuration = profile.get("configuration") or {}
    mode = str(configuration.get("execution_mode") or "managed_local")
    endpoint = configuration.get("runtime_endpoint")
    if mode not in {"managed_local", "assigned"}:
        raise ValueError("environment profile execution_mode must be managed_local or assigned")
    if mode == "assigned" and not isinstance(endpoint, str):
        raise ValueError("assigned environment profile requires runtime_endpoint")
    unit = {
        "schema_version": "1.1",
        "run_id": run_id,
        "workspace_id": workspace_id,
        "storage_id": storage_id,
        "storage_dir": str(workspace_dir),
        "mode": mode,
        "profile": {"id": profile.get("id"), "version": profile.get("version")},
        "allocated_at": _now(),
        "lifecycle_status": "allocated",
        "retention_policy": str(configuration.get("retention_policy") or "retain"),
        "runtime_endpoint": endpoint if mode == "assigned" else None,
    }
    if unit["retention_policy"] not in {"retain", "archive", "cleanup"}:
        raise ValueError("environment profile retention_policy must be retain, archive, or cleanup")
    workspace_dir.mkdir(parents=True, exist_ok=False)
    _write_unit(output_dir, unit)
    return unit


def load_execution_unit(output_dir: Path) -> dict[str, Any] | None:
    """Load the sole execution unit owned by this run, if allocation survived a restart."""
    try:
        value = json.loads((output_dir / "execution_unit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _profile_environment(profile: dict[str, Any], unit: dict[str, Any]) -> dict[str, str]:
    """Map non-secret profile references to the server's configured roles."""
    config = profile.get("configuration") or {}
    query = config.get("query") or config.get("extraction") or {}
    embedding = config.get("embedding") or {}
    env = dict(os.environ)
    if query.get("provider"):
        env["LLM_BINDING"] = str(query["provider"])
    if query.get("model"):
        env["LLM_MODEL"] = str(query["model"])
    if query.get("endpoint"):
        env["LLM_BINDING_HOST"] = str(query["endpoint"])
    if embedding.get("provider"):
        env["EMBEDDING_BINDING"] = str(embedding["provider"])
    if embedding.get("model"):
        env["EMBEDDING_MODEL"] = str(embedding["model"])
    if embedding.get("endpoint"):
        env["EMBEDDING_BINDING_HOST"] = str(embedding["endpoint"])
    # The managed child is bound to 127.0.0.1 on an unguessable transient
    # port.  It must not inherit the main WebUI's login requirement: the
    # runner owns this process and needs an authenticated configuration
    # snapshot before it has a user token to pass along.  Empty values take
    # precedence over the repository .env because LightRAG loads it with
    # ``override=False``.  Provider credentials intentionally remain intact.
    env["AUTH_ACCOUNTS"] = ""
    env["LIGHTRAG_API_KEY"] = ""
    env["WORKSPACE"] = str(unit["workspace_id"])
    return env


def _provider_endpoint(role: dict[str, Any], *, prefix: str) -> str:
    configured = role.get("endpoint")
    if isinstance(configured, str) and configured.strip():
        return configured.rstrip("/")
    return os.getenv(f"{prefix}_BINDING_HOST", "http://127.0.0.1:11434").rstrip("/")


def _has_provider_credential(*, provider: str, prefix: str) -> bool:
    """Check only that a credential exists; never send a paid provider request."""
    names = [f"{prefix}_BINDING_API_KEY"]
    if provider in {"openai", "azure_openai"}:
        names.extend(["OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"])
    elif provider == "gemini":
        names.append("GEMINI_API_KEY")
    elif provider == "bedrock":
        names.extend(["AWS_ACCESS_KEY_ID", "AWS_PROFILE", "AWS_WEB_IDENTITY_TOKEN_FILE"])
    return any(bool(os.getenv(name)) for name in names)


def _ollama_reachable(endpoint: str) -> bool:
    try:
        request = urllib.request.Request(f"{endpoint.rstrip('/')}/api/tags")
        with urllib.request.urlopen(request, timeout=3):
            return True
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def preflight_execution_unit(profile: dict[str, Any]) -> None:
    """Fail fast when a managed local run has no usable LLM or embedding backend.

    This validates only local Ollama availability or the presence of credentials
    for remote providers.  It deliberately does not call remote APIs, so
    clicking an evaluation never spends tokens merely to validate a form.
    """
    configuration = profile.get("configuration") or {}
    if configuration.get("execution_mode", "managed_local") != "managed_local":
        return
    roles = {
        "LLM": (configuration.get("query") or configuration.get("extraction") or {}, "LLM"),
        "embedding": (configuration.get("embedding") or {}, "EMBEDDING"),
    }
    blockers: list[str] = []
    for label, (raw_role, prefix) in roles.items():
        role = raw_role if isinstance(raw_role, dict) else {}
        provider = str(role.get("provider") or "ollama").strip().lower()
        if provider == "ollama":
            endpoint = _provider_endpoint(role, prefix=prefix)
            if not _ollama_reachable(endpoint):
                blockers.append(f"{label} uses Ollama but {endpoint} is unreachable")
        elif not _has_provider_credential(provider=provider, prefix=prefix):
            blockers.append(f"{label} uses {provider} but its API credential is not configured")
    if blockers:
        raise ExecutionUnitPrerequisiteError("; ".join(blockers))


def _write_unit(output_dir: Path, unit: dict[str, Any]) -> None:
    path = output_dir / "execution_unit.json"
    tmp = output_dir / "execution_unit.json.tmp"
    tmp.write_text(json.dumps(unit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def start_execution_unit(
    *,
    output_dir: Path,
    profile: dict[str, Any],
    unit: dict[str, Any],
    api_key: str | None = None,
    access_token: str | None = None,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Start a local isolated server or verify an assigned one is reachable."""
    if unit["mode"] == "assigned":
        endpoint = str(unit["runtime_endpoint"])
        snapshot = capture_runtime_snapshot(
            rag_api_url=endpoint, api_key=api_key, access_token=access_token
        )
        if snapshot.get("status") != "captured":
            raise RuntimeError(f"assigned execution unit is unavailable: {snapshot.get('reason')}")
        unit.update(
            {
                "started_at": unit.get("started_at") or _now(),
                "runtime_snapshot": snapshot,
                "lifecycle_status": "running",
            }
        )
        _write_unit(output_dir, unit)
        return unit

    existing_endpoint = unit.get("runtime_endpoint")
    if isinstance(existing_endpoint, str) and existing_endpoint:
        existing = capture_runtime_snapshot(
            rag_api_url=existing_endpoint,
            api_key=api_key,
            access_token=access_token,
            timeout_seconds=2,
        )
        if existing.get("status") == "captured":
            unit.update({"runtime_snapshot": existing, "lifecycle_status": "running"})
            _write_unit(output_dir, unit)
            return unit

    unit.update({"lifecycle_status": "starting", "starting_at": _now()})
    _write_unit(output_dir, unit)
    port = _free_local_port()
    endpoint = f"http://127.0.0.1:{port}"
    log_path = output_dir / "execution_unit.log"
    log_handle = log_path.open("a", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "lightrag.api.lightrag_server",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--working-dir",
        str(unit["storage_dir"]),
        "--workspace",
        str(unit["workspace_id"]),
    ]
    try:
        try:
            proc = subprocess.Popen(
                command,
                cwd=Path(__file__).resolve().parents[2],
                env=_profile_environment(profile, unit),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                # Keep the server in the evaluation process group.  Job
                # cancellation then terminates the runner and this server together.
            )
        finally:
            log_handle.close()
        deadline = time.monotonic() + timeout_seconds
        snapshot: dict[str, Any] = {}
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"managed execution unit exited with code {proc.returncode}")
            snapshot = capture_runtime_snapshot(
                rag_api_url=endpoint,
                api_key=api_key,
                access_token=access_token,
                timeout_seconds=2,
            )
            if snapshot.get("status") == "captured":
                unit.update(
                    {
                        "started_at": _now(),
                        "pid": proc.pid,
                        "runtime_endpoint": endpoint,
                        "runtime_snapshot": snapshot,
                        "lifecycle_status": "running",
                    }
                )
                _write_unit(output_dir, unit)
                return unit
            time.sleep(0.5)
        proc.terminate()
        raise TimeoutError("managed execution unit did not become healthy before timeout")
    except Exception as exc:
        unit.update({"lifecycle_status": "failed", "failure": str(exc)})
        _write_unit(output_dir, unit)
        raise


def stop_execution_unit(unit: dict[str, Any]) -> None:
    """Stop only the local process belonging to this unit; assigned units persist."""
    if unit.get("mode") != "managed_local":
        return
    pid = unit.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return
    try:
        # The managed server intentionally shares the runner's process group;
        # killing that group here would also kill the runner.  The job manager
        # owns group termination on cancellation, while normal teardown only
        # stops this known child PID.
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return


def finalize_execution_unit(
    *, output_dir: Path, unit: dict[str, Any], outcome: str
) -> dict[str, Any]:
    """Persist terminal state and apply the profile's explicit retention policy.

    ``outcome`` is intentionally separate from a process liveness check: an
    interrupted run must remain diagnosable and never look like a completed
    environment merely because its local server already exited.
    """
    if outcome not in {"complete", "failed", "interrupted"}:
        raise ValueError("unsupported execution unit outcome")
    stop_execution_unit(unit)
    policy = str(unit.get("retention_policy") or "retain")
    unit["finished_at"] = _now()
    unit["run_outcome"] = outcome
    if policy == "cleanup":
        storage_dir = Path(str(unit.get("storage_dir") or ""))
        isolated_root = (output_dir / "isolated").resolve()
        try:
            target = storage_dir.resolve()
            allowed = target.parent == isolated_root and target.name == str(unit.get("storage_id"))
        except OSError:
            allowed = False
        if allowed and target.exists():
            shutil.rmtree(target)
            unit["storage_removed_at"] = _now()
            unit["lifecycle_status"] = "cleaned"
        else:
            unit["lifecycle_status"] = "cleanup_skipped"
            unit["cleanup_reason"] = "storage target was absent or outside this run's isolated root"
    elif policy == "archive":
        unit["lifecycle_status"] = "archived"
    else:
        unit["lifecycle_status"] = "completed" if outcome == "complete" else outcome
    _write_unit(output_dir, unit)
    return unit
