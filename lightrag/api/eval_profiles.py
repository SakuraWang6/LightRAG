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
            item["status"] = "published"
            item["published_at"] = _now()
            _write(runs_root, payload)
            return {"id": profile_id, "name": profile.get("name"), **item}
    return None
