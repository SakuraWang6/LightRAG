"""Standard run envelope, progress file, and unified condition handling."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = "2.0"
_SCAN_INDEX_NAME = ".eval_index.json"
_SENSITIVE_EXTRA_RE = re.compile(r"(key|token|secret|authorization)", re.IGNORECASE)
_SENSITIVE_EVENT_VALUE_RE = re.compile(
    r"(?i)\b(api[_ -]?key|access[_ -]?token|token|secret|authorization|password)\b\s*([=:])\s*[^\s,;]+"
)

BASELINE_DEFAULTS: dict[str, Any] = {
    "mode": "mix",
    "top_k": 5,
    "chunk_top_k": 5,
    "model": "qwen3:8b",
    "vlm_model": "gemma3:4b",
    "num_ctx": 16384,
    "num_predict": 128,
    "temperature": 0,
    "kg": True,
    "vlm": False,
    "engine": "native",
    "max_cases": 0,
}

# Larger-context arms (Top-20 candidate pools) default to a 32K window.
WIDE_CONTEXT_ARMS = {
    "direct_top20",
    "select3",
    "select5",
    "role_select5",
    "combined_focus",
    "combined_precision",
    "oracle_text",
    "oracle_full",
}

_CONDITION_LABELS = {
    "dataset": "数据集",
    "pages": "文档页数",
    "tier": "规模档",
    "profile": "生成档案",
    "formats": "格式",
    "engine": "解析引擎",
    "model": "生成模型",
    "vlm_model": "VLM",
    "mode": "检索模式",
    "top_k": "Top-K",
    "chunk_top_k": "Chunk Top-K",
    "num_ctx": "上下文窗口",
    "num_predict": "最大输出",
    "temperature": "温度",
    "kg": "KG",
    "vlm": "VLM 抽取",
    "rag_api_url": "RAG API",
    "ollama_url": "Ollama",
    "storage_dir": "存储目录",
    "embedding_model": "Embedding",
    "methods": "方法数",
}


@dataclass
class ExperimentSpec:
    id: str
    label: str
    description: str
    runner: Callable[["RunContext"], dict[str, Any]]
    default_baseline: dict[str, Any] = field(
        default_factory=lambda: dict(BASELINE_DEFAULTS)
    )
    variables: list[dict[str, Any]] = field(default_factory=list)
    kind: str = "experiment"
    supervision: str = "none"
    supports_resume: bool = False
    extra_schema: dict[str, str] = field(default_factory=dict)
    env_required: list[str] = field(default_factory=list)
    prepare: Callable[["RunContext"], None] | None = None


@dataclass
class RunContext:
    spec: ExperimentSpec
    dataset: Path
    output_dir: Path
    baseline: dict[str, Any]
    environment: dict[str, Any]
    variables: list[dict[str, Any]]
    run_id: str
    extra: dict[str, Any] = field(default_factory=dict)
    restarts: int = 0
    last_restart_resume: bool | None = None
    execution_manifest: dict[str, Any] = field(default_factory=dict)
    runtime_snapshot: dict[str, Any] = field(default_factory=dict)
    environment_profile: dict[str, Any] | None = None
    execution_unit: dict[str, Any] | None = None
    runs_root: Path | None = None
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def progress(
        self, status: str, done: int, total: int, phase: str = "", message: str = ""
    ) -> None:
        write_progress(
            self.output_dir,
            status=status,
            done=done,
            total=total,
            phase=phase,
            message=message,
        )


def _dataset_meta(dataset: Path) -> dict[str, Any]:
    manifest = dataset / "manifest.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        "dataset": str(payload.get("dataset_id") or dataset.name),
        "pages": payload.get("pages"),
        "tier": payload.get("tier"),
        "profile": payload.get("profile"),
        "formats": payload.get("formats"),
        "title": payload.get("title"),
    }


def _unknown(reason: str) -> dict[str, str]:
    """Represent a missing provenance value without inventing a default."""
    return {"value": "unknown", "reason": reason}


def _sha256(path: Path) -> str | dict[str, str]:
    """Return a content fingerprint, preserving why one cannot be obtained."""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError as exc:
        return _unknown(f"cannot read {path.name}: {type(exc).__name__}")


def _git_commit() -> str | dict[str, str]:
    try:
        root = Path(__file__).resolve().parents[3]
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        commit = result.stdout.strip()
        return commit or _unknown("git returned an empty revision")
    except (OSError, subprocess.SubprocessError):
        return _unknown("git revision is unavailable")


def _manifest_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return _unknown(f"dataset manifest has none of: {', '.join(keys)}")


def build_execution_manifest(
    *,
    dataset: Path | None,
    experiment_id: str,
    experiment_type: str,
    parameters: dict[str, Any],
    parameter_sources: dict[str, str] | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    """Build the immutable, secret-free statement of an evaluation's inputs.

    This is deliberately best-effort: callers may use an old or remote dataset,
    but absence must remain explicit instead of being silently replaced with a
    local default.  The returned object contains no endpoint credentials.
    """
    dataset_path = Path(dataset) if dataset is not None else None
    manifest_path = dataset_path / "manifest.json" if dataset_path else None
    manifest: dict[str, Any] = {}
    manifest_error: str | None = None
    if manifest_path is None:
        manifest_error = "dataset path was not supplied by this runner"
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            manifest_error = f"cannot read dataset manifest: {type(exc).__name__}"

    source = parameter_sources or {}
    declared_parameters = {
        key: {"value": value, "source": source.get(key, "unknown")}
        for key, value in parameters.items()
    }
    document_files: list[dict[str, Any]] = []
    if manifest and dataset_path is not None:
        for item in manifest.get("files") or []:
            if not isinstance(item, dict) or item.get("status") != "created":
                continue
            file_format = str(item.get("format") or "").lower()
            if file_format not in {"docx", "pdf", "txt", "md", "html"}:
                continue
            name = str(item.get("name") or "")
            path = dataset_path / name
            document_files.append(
                {
                    "name": name or _unknown("manifest file entry has no name"),
                    "format": file_format,
                    "sha256": _sha256(path),
                }
            )

    try:
        from memory_eval_tests import __version__ as framework_version
    except Exception:
        framework_version = _unknown("evaluation framework version is unavailable")

    oracle_name = str(manifest.get("oracle_file") or "oracle.json")
    return {
        "manifest_version": "1.0",
        "captured_at": started_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": {
            "dataset_id": _manifest_value(manifest, "dataset_id")
            if manifest
            else _unknown(manifest_error or "dataset manifest is unavailable"),
            "manifest_sha256": _sha256(manifest_path)
            if manifest_path is not None
            else _unknown(manifest_error or "dataset manifest is unavailable"),
            "oracle_sha256": _sha256(dataset_path / oracle_name)
            if dataset_path is not None and manifest
            else _unknown(manifest_error or "oracle path is unavailable"),
            "document_files": document_files
            if manifest
            else _unknown(manifest_error or "document file list is unavailable"),
            "generator_version": _manifest_value(
                manifest, "generator_version", "generator_code_version"
            )
            if manifest
            else _unknown(manifest_error or "dataset manifest is unavailable"),
            "template_version": _manifest_value(manifest, "template_version")
            if manifest
            else _unknown(manifest_error or "dataset manifest is unavailable"),
            "random_seed": _manifest_value(manifest, "random_seed", "seed")
            if manifest
            else _unknown(manifest_error or "dataset manifest is unavailable"),
        },
        "experiment": {"id": experiment_id, "type": experiment_type},
        "code": {"git_commit": _git_commit(), "framework_version": framework_version},
        "parameters": declared_parameters,
    }


def _existing_execution_manifest(output_dir: Path) -> dict[str, Any] | None:
    """Keep a manifest immutable when the harness rewrites ``run.json``."""
    try:
        value = json.loads((output_dir / "run.json").read_text(encoding="utf-8")).get(
            "execution_manifest"
        )
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _existing_runtime_snapshot(output_dir: Path) -> dict[str, Any] | None:
    """Keep the actual environment observation stable across envelope rewrites."""
    try:
        value = json.loads((output_dir / "run.json").read_text(encoding="utf-8")).get(
            "runtime_snapshot"
        )
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _endpoint_identifier(value: Any) -> str | dict[str, str]:
    """Persist a routable endpoint identity without query strings or credentials."""
    if not isinstance(value, str) or not value.strip():
        return _unknown("provider endpoint is not configured")
    parsed = urllib.parse.urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        return _unknown("provider endpoint is not a valid absolute URL")
    host = parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        return _unknown("provider endpoint has an invalid port")
    if port is not None:
        host = f"{host}:{port}"
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _runtime_unavailable(reason: str) -> dict[str, Any]:
    return {
        "snapshot_version": "1.0",
        "status": "unavailable",
        "reason": reason,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def capture_runtime_snapshot(
    *,
    rag_api_url: str | None,
    api_key: str | None = None,
    access_token: str | None = None,
    timeout_seconds: float = 5,
) -> dict[str, Any]:
    """Read the tested LightRAG instance's authenticated ``/health`` snapshot.

    The browser-declared model is intentionally not an input here.  A failed or
    unauthenticated observation stays explicit so reports cannot present it as
    an effective configuration.
    """
    if not rag_api_url:
        return _runtime_unavailable("RAG API URL was not supplied")
    health_url = f"{rag_api_url.rstrip('/')}/health"
    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    try:
        request = urllib.request.Request(health_url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return _runtime_unavailable(f"health snapshot request failed: {type(exc).__name__}")

    configuration = payload.get("configuration")
    if not isinstance(configuration, dict):
        return _runtime_unavailable(
            "health response did not expose authenticated runtime configuration"
        )
    roles = configuration.get("role_llm_config")
    query_role = roles.get("query") if isinstance(roles, dict) else {}
    effective_model = (
        query_role.get("model")
        if isinstance(query_role, dict) and query_role.get("model")
        else configuration.get("llm_model")
    )
    return {
        "snapshot_version": "1.0",
        "status": "captured",
        "source": "authenticated_health",
        "source_endpoint": _endpoint_identifier(health_url),
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lightrag": {
            "core_version": payload.get("core_version")
            or _unknown("health response omitted core_version"),
            "api_version": payload.get("api_version")
            or _unknown("health response omitted api_version"),
        },
        "llm": {
            "provider": configuration.get("llm_binding")
            or _unknown("health response omitted llm_binding"),
            "model": effective_model or _unknown("health response omitted effective LLM model"),
            "endpoint": _endpoint_identifier(configuration.get("llm_binding_host")),
        },
        "embedding": {
            "provider": configuration.get("embedding_binding")
            or _unknown("health response omitted embedding_binding"),
            "model": configuration.get("embedding_model")
            or _unknown("health response omitted embedding_model"),
            "endpoint": _endpoint_identifier(configuration.get("embedding_binding_host")),
        },
        "vlm": {
            "enabled": bool(configuration.get("vlm_process_enable")),
            "model": configuration.get("vlm_model")
            or _unknown("health response omitted VLM model"),
        },
        "reranker": {
            "enabled": bool(configuration.get("enable_rerank")),
            "provider": configuration.get("rerank_binding")
            or _unknown("reranker is not configured"),
            "model": configuration.get("rerank_model")
            or _unknown("reranker is not configured"),
            "endpoint": _endpoint_identifier(configuration.get("rerank_binding_host")),
        },
        "parser": {
            "routing": configuration.get("parser_routing")
            or _unknown("health response omitted parser routing"),
        },
        "storage": {
            "workspace": configuration.get("workspace")
            or _unknown("health response omitted workspace"),
            "backends": {
                key: configuration.get(key)
                or _unknown(f"health response omitted {key}")
                for key in (
                    "kv_storage",
                    "doc_status_storage",
                    "graph_storage",
                    "vector_storage",
                )
            },
        },
        "retrieval_defaults": {
            "top_k": _unknown("health response has no query top_k default"),
            "chunk_top_k": _unknown("health response has no query chunk_top_k default"),
            "max_total_tokens": _unknown(
                "health response has no query max_total_tokens default"
            ),
        },
    }


def _model_identity(
    *, baseline: dict[str, Any], runtime_snapshot: dict[str, Any]
) -> tuple[Any, Any, bool | None]:
    declared = baseline.get("model") or _unknown("run has no declared model")
    effective = (runtime_snapshot.get("llm") or {}).get("model")
    if not isinstance(declared, str) or not isinstance(effective, str):
        return declared, effective or _unknown("runtime snapshot has no effective model"), None
    return declared, effective, declared != effective


def _redact_environment(environment: dict[str, Any]) -> dict[str, Any]:
    """Strip live credentials before an environment dict is persisted.

    ``api_key`` / ``access_token`` stay plaintext in memory for runner use, but
    the on-disk envelope must never contain them; a non-empty value is replaced
    with a marker so reviewers still know auth was configured.
    """
    redacted = dict(environment)
    for key in ("api_key", "access_token"):
        if redacted.get(key):
            redacted[key] = "configured"
    return redacted


def redact_launch_extra(extra: list[str]) -> list[str]:
    """Redact sensitive KEY=VALUE entries before persisting launch params.

    The CLI lets users pass arbitrary ``--extra KEY=VALUE`` pairs; a key whose
    name looks like a credential (api_key/token/secret/authorization) is
    replaced with ``configured`` so secrets never land in run.json.
    """
    redacted: list[str] = []
    for item in extra:
        key, _, value = item.partition("=")
        if _SENSITIVE_EXTRA_RE.search(key.strip()):
            redacted.append(f"{key.strip()}=configured")
        else:
            redacted.append(item)
    return redacted


def redact_sensitive_text(value: str) -> str:
    """Remove credential-shaped values before writing human-readable artifacts."""
    return _SENSITIVE_EVENT_VALUE_RE.sub(r"\1\2configured", value)


def append_run_event(
    output_dir: Path,
    *,
    phase: str,
    severity: str,
    message: str,
    error_type: str | None = None,
) -> int:
    """Append a redacted lifecycle event and return its one-based log offset."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "events.jsonl"
    try:
        with path.open("a+", encoding="utf-8") as handle:
            handle.seek(0)
            offset = sum(1 for _ in handle) + 1
            handle.seek(0, os.SEEK_END)
            event = {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "phase": phase,
                "severity": severity,
                "message": redact_sensitive_text(message),
            }
            if error_type:
                event["error_type"] = error_type
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return offset
    except OSError:
        return 0


def _existing_failure(output_dir: Path) -> dict[str, Any] | None:
    try:
        value = json.loads((output_dir / "run.json").read_text(encoding="utf-8")).get(
            "failure"
        )
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value else None


def build_failure(
    *,
    phase: str,
    error: BaseException | str,
    retryable: bool,
    recommendation: str,
    log_offset: int,
) -> dict[str, Any]:
    """Create the failure record that is safe to render in the console."""
    error_type = type(error).__name__ if isinstance(error, BaseException) else "Error"
    message = str(error)
    return {
        "phase": phase,
        "error_type": error_type,
        "summary": redact_sensitive_text(message),
        "retryable": retryable,
        "recommendation": recommendation,
        "log_offset": log_offset,
    }


def capture_environment(**overrides: Any) -> dict[str, Any]:
    try:
        from lightrag._version import __api_version__ as api_version
        from lightrag._version import __version__ as core_version
    except Exception:
        core_version, api_version = "unknown", "unknown"
    env: dict[str, Any] = {
        "lightrag_version": core_version,
        "api_version": api_version,
        "rag_api_url": os.getenv("RAG_API_URL", "http://127.0.0.1:9621"),
        "ollama_url": os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
        "api_key": os.getenv("LIGHTRAG_API_KEY"),
        "access_token": os.getenv("LIGHTRAG_ACCESS_TOKEN"),
        "llm_binding": os.getenv("LLM_BINDING", "ollama"),
        "embedding_model": os.getenv("EMBEDDING_MODEL", "bge-m3:latest"),
        "vlm_model": os.getenv("VLM_LLM_MODEL", "gemma3:4b"),
        "vlm_process_enable": os.getenv("VLM_PROCESS_ENABLE", "false").lower()
        in {"1", "true", "yes"},
        "storage_dir": os.getenv("WORKING_DIR", ""),
    }
    env.update({k: v for k, v in overrides.items() if v is not None})
    return env


def build_conditions(
    environment: dict[str, Any],
    baseline: dict[str, Any],
    dataset_meta: dict[str, Any],
    method_count: int | None = None,
) -> list[dict[str, str]]:
    merged: dict[str, Any] = {}
    merged.update(dataset_meta)
    merged.update(baseline)
    merged.update(
        {
            key: environment[key]
            for key in ("rag_api_url", "ollama_url", "storage_dir", "embedding_model")
            if environment.get(key)
        }
    )
    if method_count is not None:
        merged["methods"] = method_count
    order = [
        "dataset",
        "pages",
        "tier",
        "profile",
        "formats",
        "engine",
        "model",
        "mode",
        "top_k",
        "chunk_top_k",
        "num_ctx",
        "num_predict",
        "temperature",
        "kg",
        "vlm_model",
        "vlm",
        "methods",
        "rag_api_url",
        "ollama_url",
        "storage_dir",
        "embedding_model",
    ]
    result = []
    for key in order:
        if key not in merged or merged[key] in (None, ""):
            continue
        value = merged[key]
        if isinstance(value, bool):
            value = "开" if value else "关"
        elif isinstance(value, list):
            value = ",".join(str(item) for item in value)
        result.append(
            {"key": key, "label": _CONDITION_LABELS.get(key, key), "value": str(value)}
        )
    return result


def write_envelope(
    output_dir: Path,
    *,
    context: RunContext,
    status: str,
    methods: list[dict[str, Any]],
    report_rel_path: str | None = None,
    extra: dict[str, Any] | None = None,
    write_progress_file: bool = True,
    runs_root: Path | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    execution_manifest = _existing_execution_manifest(output_dir) or context.execution_manifest
    if not execution_manifest:
        execution_manifest = build_execution_manifest(
            dataset=context.dataset,
            experiment_id=context.spec.id,
            experiment_type=context.spec.kind,
            parameters=context.baseline,
            started_at=context.started_at,
        )
    existing_snapshot = _existing_runtime_snapshot(output_dir)
    # A run may begin before its isolated execution unit is provisioned. Keep
    # a captured snapshot immutable, but allow the provisional observation to
    # be replaced once by the unit's actual configuration.
    if (
        isinstance(existing_snapshot, dict)
        and existing_snapshot.get("status") == "captured"
    ):
        runtime_snapshot = existing_snapshot
    else:
        runtime_snapshot = context.runtime_snapshot or existing_snapshot or {}
    if not runtime_snapshot:
        runtime_snapshot = capture_runtime_snapshot(
            rag_api_url=context.environment.get("rag_api_url"),
            api_key=context.environment.get("api_key"),
            access_token=context.environment.get("access_token"),
        )
    declared_model, effective_model, configuration_mismatch = _model_identity(
        baseline=context.baseline,
        runtime_snapshot=runtime_snapshot,
    )
    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": context.spec.kind,
        "run_id": context.run_id,
        "created_at": now,
        "started_at": context.started_at,
        "restarts": context.restarts,
        "status": status,
        "experiment": {
            "id": context.spec.id,
            "label": context.spec.label,
            "description": context.spec.description,
        },
        "environment": _redact_environment(context.environment),
        "baseline": context.baseline,
        "variables": context.variables,
        "methods": methods,
        "reports": {"report.md": report_rel_path} if report_rel_path else {},
        "execution_manifest": execution_manifest,
        "runtime_snapshot": runtime_snapshot,
        "compatibility_level": "current",
        "declared_model": declared_model,
        "effective_model": effective_model,
        "configuration_mismatch": configuration_mismatch,
    }
    if status in {"complete", "failed"}:
        envelope["finished_at"] = now
    if context.last_restart_resume is not None:
        envelope["last_restart_resume"] = context.last_restart_resume
    if extra:
        envelope.update(extra)
    if status == "failed":
        failure = envelope.get("failure") or _existing_failure(output_dir)
        if not failure:
            failure = {
                "phase": "unknown",
                "error_type": "UnknownError",
                "summary": "failure details were not recorded by this runner",
                "retryable": None,
                "recommendation": "inspect events.jsonl and run.log before retrying",
                "log_offset": 0,
            }
        envelope["failure"] = failure
    envelope["events_path"] = "events.jsonl"
    path = output_dir / "run.json"
    path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _invalidate_scan_index(runs_root)
    if write_progress_file:
        write_progress(
            output_dir, status=status, done=1, total=1, phase="done", message=""
        )
    return path


def write_simple_envelope(
    output_dir: Path,
    *,
    kind: str,
    run_id: str,
    experiment: dict[str, Any],
    baseline: dict[str, Any],
    environment: dict[str, Any],
    methods: list[dict[str, Any]],
    status: str,
    report_rel_path: str | None = None,
    extra: dict[str, Any] | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    restarts: int = 0,
    runs_root: Path | None = None,
    dataset_path: Path | None = None,
    parameter_sources: dict[str, str] | None = None,
) -> Path:
    """Envelope writer for non-registry runs (offline/online evaluators)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    execution_manifest = _existing_execution_manifest(output_dir) or build_execution_manifest(
        dataset=dataset_path,
        experiment_id=str(experiment.get("id") or "unknown"),
        experiment_type=kind,
        parameters=baseline,
        parameter_sources=parameter_sources,
        started_at=started_at or now,
    )
    runtime_snapshot = _existing_runtime_snapshot(output_dir)
    if runtime_snapshot is None:
        runtime_snapshot = (
            {
                "snapshot_version": "1.0",
                "status": "not_applicable",
                "reason": "offline runs do not measure a LightRAG server instance",
                "captured_at": now,
            }
            if kind == "offline"
            else capture_runtime_snapshot(
                rag_api_url=environment.get("rag_api_url"),
                api_key=environment.get("api_key"),
                access_token=environment.get("access_token"),
            )
        )
    declared_model, effective_model, configuration_mismatch = _model_identity(
        baseline=baseline,
        runtime_snapshot=runtime_snapshot,
    )
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "run_id": run_id,
        "created_at": now,
        "status": status,
        "restarts": restarts,
        "experiment": experiment,
        "environment": _redact_environment(environment),
        "baseline": baseline,
        "variables": [],
        "methods": methods,
        "reports": {"report.md": report_rel_path} if report_rel_path else {},
        "execution_manifest": execution_manifest,
        "runtime_snapshot": runtime_snapshot,
        "compatibility_level": "current",
        "declared_model": declared_model,
        "effective_model": effective_model,
        "configuration_mismatch": configuration_mismatch,
    }
    if started_at is not None:
        envelope["started_at"] = started_at
    if status in {"complete", "failed"}:
        envelope["finished_at"] = finished_at or now
    if extra:
        envelope.update(extra)
    if status == "failed":
        failure = envelope.get("failure") or _existing_failure(output_dir)
        if not failure:
            failure = {
                "phase": "unknown",
                "error_type": "UnknownError",
                "summary": "failure details were not recorded by this runner",
                "retryable": None,
                "recommendation": "inspect events.jsonl and run.log before retrying",
                "log_offset": 0,
            }
        envelope["failure"] = failure
    envelope["events_path"] = "events.jsonl"
    path = output_dir / "run.json"
    path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _invalidate_scan_index(runs_root)
    return path


def write_progress(
    output_dir: Path,
    *,
    status: str,
    done: int,
    total: int,
    phase: str = "",
    message: str = "",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "phase": phase,
        "done": done,
        "total": total,
        "message": message,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (output_dir / "progress.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _invalidate_scan_index(runs_root: Path | None = None) -> None:
    """Drop the console's persisted scan index after a run file changes.

    The index lives at the runs root (env ``MEMORY_EVAL_RUNS_ROOT`` or the repo
    default) and is rebuilt lazily by ``lightrag.api.eval_index``.
    """
    if runs_root is None:
        configured = os.getenv("MEMORY_EVAL_RUNS_ROOT")
        runs_root = (
            Path(configured)
            if configured
            else Path(__file__).resolve().parents[3] / "memory_eval_tests" / "runs"
        )
    try:
        (runs_root / _SCAN_INDEX_NAME).unlink()
    except FileNotFoundError:
        pass


def read_progress(output_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((output_dir / "progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
