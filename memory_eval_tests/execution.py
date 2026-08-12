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

from memory_eval_tests.artifacts import capture_runtime_snapshot


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
    input_dir = workspace_dir / "inputs"
    configuration = profile.get("configuration") or {}
    mode = str(configuration.get("execution_mode") or "managed_local")
    endpoint = configuration.get("runtime_endpoint")
    if mode not in {"managed_local", "assigned"}:
        raise ValueError("environment profile execution_mode must be managed_local or assigned")
    if mode == "assigned" and not isinstance(endpoint, str):
        raise ValueError("assigned environment profile requires runtime_endpoint")
    unit = {
        "schema_version": "1.2",
        "run_id": run_id,
        "workspace_id": workspace_id,
        "storage_id": storage_id,
        "storage_dir": str(workspace_dir),
        "input_dir": str(input_dir),
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
    input_dir.mkdir(parents=True, exist_ok=False)
    _write_unit(output_dir, unit)
    return unit


def load_execution_unit(output_dir: Path) -> dict[str, Any] | None:
    """Load the sole execution unit owned by this run, if allocation survived a restart."""
    try:
        value = json.loads((output_dir / "execution_unit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


_GENERATION_OPTION_SPECS: dict[str, tuple[str, str | None, bool]] = {
    # provider -> (binding option environment prefix, output-token option, supports num_ctx)
    # ``lollms`` is configured through the Ollama-shaped option container in
    # lightrag_server, hence the same option prefix.
    "ollama": ("OLLAMA_LLM", "NUM_PREDICT", True),
    "lollms": ("OLLAMA_LLM", "NUM_PREDICT", True),
    "openai": ("OPENAI_LLM", "MAX_COMPLETION_TOKENS", False),
    "azure_openai": ("OPENAI_LLM", "MAX_COMPLETION_TOKENS", False),
    "gemini": ("GEMINI_LLM", "MAX_OUTPUT_TOKENS", False),
    "bedrock": ("BEDROCK_LLM", "MAX_TOKENS", False),
}


def _apply_generation_options(
    env: dict[str, str],
    *,
    provider: Any,
    role: str | None,
    options: dict[str, Any] | None,
) -> None:
    """Apply declared per-run generation controls to one LLM role.

    LightRAG reads provider options at child-server startup.  The evaluation
    runner therefore cannot put these values on individual ``/query`` calls:
    it must translate them to the role-aware environment options consumed by
    ``BindingOptions.options_dict_for_role``.  Unsupported controls are not
    invented for providers that have no matching LightRAG option.
    """
    if not options:
        return
    spec = _GENERATION_OPTION_SPECS.get(str(provider or "").strip().lower())
    if spec is None:
        return
    prefix, output_option, supports_num_ctx = spec
    role_prefix = f"{role}_" if role else ""

    temperature = options.get("temperature")
    if isinstance(temperature, (int, float)) and not isinstance(temperature, bool):
        env[f"{role_prefix}{prefix}_TEMPERATURE"] = str(temperature)

    num_predict = options.get("num_predict")
    if (
        output_option
        and isinstance(num_predict, int)
        and not isinstance(num_predict, bool)
        and num_predict > 0
    ):
        env[f"{role_prefix}{prefix}_{output_option}"] = str(num_predict)

    num_ctx = options.get("num_ctx")
    if (
        supports_num_ctx
        and isinstance(num_ctx, int)
        and not isinstance(num_ctx, bool)
        and num_ctx > 0
    ):
        env[f"{role_prefix}{prefix}_NUM_CTX"] = str(num_ctx)

    max_async = options.get("max_async")
    if isinstance(max_async, int) and not isinstance(max_async, bool) and max_async > 0:
        env[f"{role_prefix}MAX_ASYNC_LLM"] = str(max_async)


def _apply_extraction_safeguards(
    env: dict[str, str], options: dict[str, Any] | None
) -> None:
    """Apply extraction-only output integrity controls to a managed child.

    Entity extraction has a much larger, structured response than a normal
    answer.  Keeping its record cap and format under the evaluation runner's
    control prevents a user's answer-length choice from silently producing a
    truncated knowledge graph.
    """
    if not options:
        return
    if options.get("use_json") is True:
        env["ENTITY_EXTRACTION_USE_JSON"] = "true"
    for option_key, env_key in (
        ("max_records", "MAX_EXTRACTION_RECORDS"),
        ("max_entities", "MAX_EXTRACTION_ENTITIES"),
        ("max_gleaning", "MAX_GLEANING"),
    ):
        value = options.get(option_key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            env[env_key] = str(value)


def _apply_extraction_execution_options(
    env: dict[str, str], options: dict[str, Any] | None
) -> None:
    """Apply stable, role-scoped execution limits for local KG extraction."""
    if not options:
        return
    timeout = options.get("timeout_seconds")
    if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout > 0:
        env["EXTRACT_LLM_TIMEOUT"] = str(timeout)
    max_async = options.get("max_async")
    if isinstance(max_async, int) and not isinstance(max_async, bool) and max_async > 0:
        env["EXTRACT_MAX_ASYNC_LLM"] = str(max_async)


def _profile_environment(
    profile: dict[str, Any],
    unit: dict[str, Any],
    runtime_options: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Map the supported, non-secret profile settings to child-server env vars."""
    config = profile.get("configuration") or {}
    primary = config.get("query") or config.get("extraction") or {}
    env = dict(os.environ)
    # The isolated server only talks to loopback backends (Ollama,
    # embeddings).  Inherited proxy variables would route those requests
    # through a local proxy; a stalled proxy connection surfaces as httpx
    # ReadTimeout on otherwise fast extractions (observed on 200-page
    # documents where chunk-009 took >1800s in-run but 190s standalone).
    # External traffic (e.g. tiktoken first-use download) keeps the proxy.
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    _set_role_environment(env, primary, prefix="LLM")
    for name, prefix in (("extraction", "EXTRACT_LLM"), ("query", "QUERY_LLM"), ("vlm", "VLM_LLM")):
        role = config.get(name)
        if isinstance(role, dict):
            _set_role_environment(env, role, prefix=prefix)
    embedding = config.get("embedding") or {}
    _set_role_environment(env, embedding, prefix="EMBEDDING")
    reranker = config.get("reranker")
    if isinstance(reranker, dict):
        _set_role_environment(env, reranker, prefix="RERANK")
    parser_engine = config.get("parser_engine")
    if isinstance(parser_engine, str) and parser_engine:
        # The parser's ``!`` process option is the supported, per-document
        # switch that skips entity/relation extraction while retaining chunks
        # for vector retrieval.  The isolated server owns no unrelated docs,
        # so a wildcard rule is exactly the run-level KG toggle we need.
        skip_kg = bool((runtime_options or {}).get("skip_kg"))
        env["LIGHTRAG_PARSER"] = f"*:{parser_engine}{'-!' if skip_kg else ''}"

    # Base options cover the default LLM role.  Query and extraction are then
    # set explicitly: extraction has its own, larger response budget because
    # a structured KG payload is not comparable to a user-facing answer.
    generation = (runtime_options or {}).get("generation")
    generation = generation if isinstance(generation, dict) else None
    _apply_generation_options(
        env,
        provider=(primary or {}).get("provider"),
        role=None,
        options=generation,
    )
    extraction_generation = (runtime_options or {}).get("extraction_generation")
    extraction_generation = (
        extraction_generation if isinstance(extraction_generation, dict) else generation
    )
    for role_name, role_prefix, options in (
        ("query", "QUERY", generation),
        ("extraction", "EXTRACT", extraction_generation),
    ):
        role = config.get(role_name)
        # LightRAG's extraction role inherits the base binding when a profile
        # declares only a query role.  Its role-specific options still apply.
        provider_role = role if isinstance(role, dict) else primary
        _apply_generation_options(
            env,
            provider=(provider_role or {}).get("provider"),
            role=role_prefix,
            options=options,
        )
    extraction_safeguards = (runtime_options or {}).get("extraction_safeguards")
    _apply_extraction_safeguards(
        env,
        extraction_safeguards if isinstance(extraction_safeguards, dict) else None,
    )
    extraction_execution = (runtime_options or {}).get("extraction_execution")
    _apply_extraction_execution_options(
        env,
        extraction_execution if isinstance(extraction_execution, dict) else None,
    )
    storage_prefixes = {
        "kv": "LIGHTRAG_KV_STORAGE",
        "vector": "LIGHTRAG_VECTOR_STORAGE",
        "graph": "LIGHTRAG_GRAPH_STORAGE",
        "doc_status": "LIGHTRAG_DOC_STATUS_STORAGE",
    }
    for key, env_name in storage_prefixes.items():
        value = (config.get("storage_backends") or {}).get(key)
        if value:
            env[env_name] = str(value)
    concurrency = config.get("concurrency") or {}
    if concurrency.get("max_async_llm"):
        env["MAX_ASYNC_LLM"] = str(concurrency["max_async_llm"])
    if concurrency.get("max_parallel_insert"):
        env["MAX_PARALLEL_INSERT"] = str(concurrency["max_parallel_insert"])
    # The managed child is bound to 127.0.0.1 on an unguessable transient
    # port.  It must not inherit the main WebUI's login requirement: the
    # runner owns this process and needs an authenticated configuration
    # snapshot before it has a user token to pass along.  Empty values take
    # precedence over the repository .env because LightRAG loads it with
    # ``override=False``.  Provider credentials intentionally remain intact.
    env["AUTH_ACCOUNTS"] = ""
    env["LIGHTRAG_API_KEY"] = ""
    env["INPUT_DIR"] = str(unit["input_dir"])
    env["WORKSPACE"] = str(unit["workspace_id"])
    return env


def _set_role_environment(env: dict[str, str], role: dict[str, Any], *, prefix: str) -> None:
    """Set one provider/model pair without accepting profile-supplied endpoints.

    Endpoint and secret references are rejected when the profile is saved.  The
    child therefore uses the trusted deployment endpoint and credential already
    configured for its provider, rather than sending inherited credentials to
    an arbitrary user-supplied host.
    """
    if role.get("provider"):
        env[f"{prefix}_BINDING"] = str(role["provider"])
    if role.get("model"):
        env[f"{prefix}_MODEL"] = str(role["model"])


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


def _ensure_run_input_dir(unit: dict[str, Any]) -> Path:
    """Backfill pre-1.2 units while keeping every upload inside its run."""
    configured = unit.get("input_dir")
    input_dir = Path(str(configured)) if configured else Path(str(unit["storage_dir"])) / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    unit["input_dir"] = str(input_dir)
    return input_dir


def start_execution_unit(
    *,
    output_dir: Path,
    profile: dict[str, Any],
    unit: dict[str, Any],
    api_key: str | None = None,
    access_token: str | None = None,
    runtime_options: dict[str, Any] | None = None,
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

    _ensure_run_input_dir(unit)

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
    if runtime_options:
        # This contains only run controls, never provider credentials.  Keep it
        # beside the child log so an operator can verify what actually ran.
        unit["runtime_options"] = json.loads(json.dumps(runtime_options))
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
        "--input-dir",
        str(unit["input_dir"]),
        "--workspace",
        str(unit["workspace_id"]),
    ]
    try:
        try:
            proc = subprocess.Popen(
                command,
                cwd=Path(__file__).resolve().parents[2],
                env=_profile_environment(profile, unit, runtime_options),
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
