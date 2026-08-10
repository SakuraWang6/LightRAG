"""Durable, versioned environment profiles for isolated evaluation runs.

The profile store deliberately contains *references* to credentials, never a
credential value.  A version is append-only: publishing it makes that exact
configuration eligible for runs, while any edit creates the next draft.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_STORE_NAME = "environment_profiles.json"
_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_PARSER_ENGINE_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ROLE_NAMES = {"extraction", "query", "embedding", "vlm", "reranker"}
_STORAGE_KEYS = {"kv", "vector", "graph", "doc_status"}
_RETRIEVAL_KEYS = {"mode", "top_k", "chunk_top_k", "max_total_tokens", "max_cases"}
_CONCURRENCY_KEYS = {"max_async_llm", "max_parallel_insert"}


def _non_empty(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def validate_profile_configuration(configuration: dict[str, Any]) -> None:
    """Reject profile fields the isolated runner cannot apply faithfully.

    Environment profiles are evidence for a run's execution conditions.  A
    stored field that is silently ignored is worse than an unavailable field:
    it makes two results look comparable when they are not.  Keep this
    validation next to the append-only store so both draft creation and
    publication reject unsupported or unsafe configurations.
    """
    if not isinstance(configuration, dict):
        raise ValueError("environment profile configuration must be an object")
    mode = str(configuration.get("execution_mode") or "managed_local")
    if mode not in {"managed_local", "assigned"}:
        raise ValueError("execution_mode must be managed_local or assigned")
    endpoint = configuration.get("runtime_endpoint")
    if mode == "assigned" and not isinstance(endpoint, str):
        raise ValueError("assigned environment profile requires runtime_endpoint")
    if mode == "managed_local" and _non_empty(endpoint):
        raise ValueError("managed_local environment profiles cannot set runtime_endpoint")
    for field in ("lightrag_version", "startup_template", "answer"):
        if _non_empty(configuration.get(field)):
            raise ValueError(f"environment profile field {field!r} is not supported by isolated runs")

    parser_engine = configuration.get("parser_engine")
    if not isinstance(parser_engine, str) or not _PARSER_ENGINE_RE.fullmatch(parser_engine):
        raise ValueError("parser_engine must be a simple installed parser engine name")
    for role_name in _ROLE_NAMES:
        role = configuration.get(role_name)
        if role is None:
            continue
        if not isinstance(role, dict):
            raise ValueError(f"{role_name} must be an object")
        if not isinstance(role.get("provider"), str) or not role["provider"].strip():
            raise ValueError(f"{role_name}.provider is required")
        if not isinstance(role.get("model"), str) or not role["model"].strip():
            raise ValueError(f"{role_name}.model is required")
        # There is no secret resolver in the runner.  Allowing an arbitrary
        # endpoint while inherited process credentials remain available would
        # also permit credential exfiltration.
        if _non_empty(role.get("endpoint")) or _non_empty(role.get("secret_ref")):
            raise ValueError(
                f"{role_name}.endpoint and {role_name}.secret_ref are not supported by isolated runs"
            )
    embedding = configuration.get("embedding")
    if not isinstance(embedding, dict):
        raise ValueError("embedding is required")
    primary_llm = configuration.get("query") or configuration.get("extraction")
    if not isinstance(primary_llm, dict):
        raise ValueError("query or extraction is required")
    primary_provider = str(primary_llm["provider"]).strip().lower()
    for role_name in ("extraction", "query", "vlm"):
        role = configuration.get(role_name)
        if isinstance(role, dict) and str(role["provider"]).strip().lower() != primary_provider:
            raise ValueError(
                f"{role_name}.provider must match the primary query/extraction provider; "
                "cross-provider role credentials are not supported by isolated runs"
            )

    storage = configuration.get("storage_backends") or {}
    if not isinstance(storage, dict) or set(storage) - _STORAGE_KEYS:
        raise ValueError("storage_backends only supports: kv, vector, graph, doc_status")
    if any(not isinstance(value, str) or not value.strip() for value in storage.values()):
        raise ValueError("storage backend values must be non-empty strings")
    retrieval = configuration.get("retrieval_defaults") or {}
    if not isinstance(retrieval, dict) or set(retrieval) - _RETRIEVAL_KEYS:
        raise ValueError(
            "retrieval_defaults only supports: mode, top_k, chunk_top_k, max_total_tokens, max_cases"
        )
    if "mode" in retrieval and str(retrieval["mode"]) not in {"naive", "local", "global", "hybrid", "mix"}:
        raise ValueError("retrieval_defaults.mode is invalid")
    for key in _RETRIEVAL_KEYS - {"mode"}:
        if key in retrieval and (not isinstance(retrieval[key], int) or isinstance(retrieval[key], bool) or retrieval[key] < 0):
            raise ValueError(f"retrieval_defaults.{key} must be a non-negative integer")
    concurrency = configuration.get("concurrency") or {}
    if not isinstance(concurrency, dict) or set(concurrency) - _CONCURRENCY_KEYS:
        raise ValueError("concurrency only supports: max_async_llm, max_parallel_insert")
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in concurrency.values()):
        raise ValueError("concurrency values must be positive integers")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(runs_root: Path) -> Path:
    return runs_root / _STORE_NAME


def _read(runs_root: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_path(runs_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    profiles = payload.get("profiles") if isinstance(payload, dict) else None
    return {"schema_version": "1.0", "profiles": profiles if isinstance(profiles, list) else []}


def _write(runs_root: Path, payload: dict[str, Any]) -> None:
    path = _path(runs_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _profile_id(value: str | None, name: str) -> str:
    if value:
        if not _PROFILE_ID_RE.fullmatch(value) or value in {".", ".."}:
            raise ValueError("invalid environment profile id")
        return value
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name.lower()).strip("-")[:40] or "profile"
    return f"{slug}-{uuid.uuid4().hex[:8]}"


def list_profiles(runs_root: Path) -> list[dict[str, Any]]:
    """Return profile summaries; the immutable version configs stay addressable."""
    result: list[dict[str, Any]] = []
    for profile in _read(runs_root)["profiles"]:
        if not isinstance(profile, dict):
            continue
        versions = profile.get("versions") if isinstance(profile.get("versions"), list) else []
        result.append(
            {
                "id": profile.get("id"),
                "name": profile.get("name"),
                "created_at": profile.get("created_at"),
                "versions": [
                    {
                        "version": version.get("version"),
                        "status": version.get("status"),
                        "created_at": version.get("created_at"),
                        "published_at": version.get("published_at"),
                    }
                    for version in versions
                    if isinstance(version, dict)
                ],
            }
        )
    return result


def get_profile_version(
    runs_root: Path, profile_id: str, version: int
) -> dict[str, Any] | None:
    for profile in _read(runs_root)["profiles"]:
        if not isinstance(profile, dict) or profile.get("id") != profile_id:
            continue
        for item in profile.get("versions") or []:
            if isinstance(item, dict) and item.get("version") == version:
                return {"id": profile_id, "name": profile.get("name"), **item}
    return None


def create_draft_version(
    *,
    runs_root: Path,
    name: str,
    configuration: dict[str, Any],
    profile_id: str | None = None,
) -> dict[str, Any]:
    """Append a draft; existing versions are never edited in place."""
    validate_profile_configuration(configuration)
    payload = _read(runs_root)
    identifier = _profile_id(profile_id, name)
    profile = next(
        (item for item in payload["profiles"] if item.get("id") == identifier), None
    )
    if profile is None:
        profile = {"id": identifier, "name": name, "created_at": _now(), "versions": []}
        payload["profiles"].append(profile)
    elif name and name != profile.get("name"):
        profile["name"] = name
    versions = profile["versions"]
    next_version = max((int(item.get("version", 0)) for item in versions), default=0) + 1
    draft = {
        "version": next_version,
        "status": "draft",
        "created_at": _now(),
        "configuration": configuration,
    }
    versions.append(draft)
    _write(runs_root, payload)
    return {"id": identifier, "name": profile["name"], **draft}


def publish_version(*, runs_root: Path, profile_id: str, version: int) -> dict[str, Any] | None:
    """Publish one draft exactly once; a published version is immutable."""
    payload = _read(runs_root)
    for profile in payload["profiles"]:
        if not isinstance(profile, dict) or profile.get("id") != profile_id:
            continue
        for item in profile.get("versions") or []:
            if not isinstance(item, dict) or item.get("version") != version:
                continue
            if item.get("status") == "published":
                return {"id": profile_id, "name": profile.get("name"), **item}
            if item.get("status") != "draft":
                raise ValueError("only draft environment profile versions can be published")
            validate_profile_configuration(item.get("configuration") or {})
            item["status"] = "published"
            item["published_at"] = _now()
            _write(runs_root, payload)
            return {"id": profile_id, "name": profile.get("name"), **item}
    return None
