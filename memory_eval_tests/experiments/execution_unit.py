"""Lifecycle helpers for one isolated LightRAG evaluation execution unit."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_eval_tests.experiments.common import capture_runtime_snapshot


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
        "schema_version": "1.0",
        "run_id": run_id,
        "workspace_id": workspace_id,
        "storage_id": storage_id,
        "storage_dir": str(workspace_dir),
        "mode": mode,
        "profile": {"id": profile.get("id"), "version": profile.get("version")},
        "allocated_at": _now(),
        "runtime_endpoint": endpoint if mode == "assigned" else None,
    }
    workspace_dir.mkdir(parents=True, exist_ok=False)
    _write_unit(output_dir, unit)
    return unit


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
    env["WORKSPACE"] = str(unit["workspace_id"])
    return env


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
        unit.update({"started_at": _now(), "runtime_snapshot": snapshot})
        _write_unit(output_dir, unit)
        return unit

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
        proc = subprocess.Popen(
            command,
            cwd=Path(__file__).resolve().parents[2],
            env=_profile_environment(profile, unit),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
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
                }
            )
            _write_unit(output_dir, unit)
            return unit
        time.sleep(0.5)
    proc.terminate()
    raise TimeoutError("managed execution unit did not become healthy before timeout")


def stop_execution_unit(unit: dict[str, Any]) -> None:
    """Stop only the local process belonging to this unit; assigned units persist."""
    if unit.get("mode") != "managed_local":
        return
    pid = unit.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return
    try:
        os.killpg(pid, 15)
    except (ProcessLookupError, PermissionError):
        return
