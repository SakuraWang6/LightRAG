"""Shared HTTP helpers for the online evaluators.

The WebUI sends ``X-API-Key`` / ``Authorization: Bearer`` on every eval request;
the CLI runners used to omit them, so any server with auth enabled rejected the
entire evaluation.  These helpers carry optional credentials through every
request so the CLI matches the WebUI client contract.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any


def auth_headers(
    *,
    api_key: str | None = None,
    access_token: str | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if api_key:
        headers["X-API-Key"] = api_key
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str | None = None,
    access_token: str | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        **auth_headers(api_key=api_key, access_token=access_token),
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(
    url: str,
    *,
    api_key: str | None = None,
    access_token: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers=auth_headers(api_key=api_key, access_token=access_token),
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def upload_file(
    path: Path,
    url: str,
    *,
    api_key: str | None = None,
    access_token: str | None = None,
    timeout: int = 600,
) -> dict[str, Any]:
    boundary = "----lightragMemoryEvalBoundary"
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        + path.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        **auth_headers(api_key=api_key, access_token=access_token),
    }
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
