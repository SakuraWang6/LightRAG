from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_eval_tests.dataset import DatasetClient
from memory_eval_tests.http import get_json as _http_get_json
from memory_eval_tests.http import upload_file as _http_upload_file

_SOURCE_DOCUMENT_FORMATS = {"docx", "pdf"}


def source_document_names(manifest: dict[str, Any]) -> list[str]:
    """Return only the document files that an evaluation may index.

    ``oracle.json`` and the other generated JSON files are evaluation artefacts,
    not source material.  New manifests mark this directly with ``role``.  The
    format-based branch preserves imported 1.0/1.1 datasets, including ones
    without a top-level ``formats`` list.
    """
    files = [item for item in manifest.get("files") or [] if isinstance(item, dict)]
    has_roles = any(
        item.get("role") in {"source_document", "evaluation_artifact"}
        for item in files
    )
    names: list[str] = []
    for item in files:
        if item.get("status") != "created":
            continue
        name = item.get("name")
        file_format = str(item.get("format") or "").lower()
        if not isinstance(name, str) or not name:
            continue
        if has_roles:
            include = item.get("role") == "source_document"
        else:
            include = file_format in _SOURCE_DOCUMENT_FORMATS
        if include and file_format in _SOURCE_DOCUMENT_FORMATS and name not in names:
            names.append(name)
    return names


def upload_dataset_files(
    *,
    dataset_source: str,
    rag_api_url: str,
    formats: list[str] | None = None,
    wait: bool = False,
    timeout_seconds: int = 900,
    poll_seconds: float = 5.0,
    api_key: str | None = None,
    access_token: str | None = None,
    confirmed_hashes: set[str] | None = None,
    file_names: list[str] | None = None,
    process_options: str | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    client = DatasetClient(dataset_source)
    manifest = client.manifest()
    selected = set(formats or ["docx"])
    selected_names = set(file_names) if file_names is not None else None
    uploaded = []
    skipped = []
    started = time.perf_counter()
    all_files = [item for item in manifest.get("files", []) if isinstance(item, dict)]
    candidates = [
        item
        for item in all_files
        if item.get("status") == "created"
        and (
            item.get("name") in selected_names
            if selected_names is not None
            else item.get("format") in selected
        )
    ]
    candidate_names = {str(item.get("name") or "") for item in candidates}
    skipped.extend(
        item.get("name")
        for item in all_files
        if str(item.get("name") or "") not in candidate_names
    )
    for position, file_info in enumerate(candidates, start=1):
        path = client.local_file(file_info["name"])
        content_sha256 = _sha256(path)
        receipt = {
            "file_name": path.name,
            "format": file_info.get("format"),
            "content_sha256": content_sha256,
            "upload_started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if confirmed_hashes and content_sha256 in confirmed_hashes:
            uploaded.append(
                {
                    **receipt,
                    "status": "success",
                    "reused": True,
                    "track_status": {"terminal": True, "passed": True, "status": "processed"},
                }
            )
            if progress_callback:
                progress_callback(position, len(candidates))
            continue
        upload_result = _upload_file(
            path,
            rag_api_url,
            api_key=api_key,
            access_token=access_token,
            process_options=process_options,
        )
        if wait and upload_result.get("track_id"):
            upload_result["track_status"] = _wait_track_status(
                str(upload_result["track_id"]),
                rag_api_url,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
                api_key=api_key,
                access_token=access_token,
            )
        uploaded.append({**receipt, **upload_result})
        if progress_callback:
            progress_callback(position, len(candidates))
    elapsed = time.perf_counter() - started
    return {
        "uploaded": uploaded,
        "skipped": skipped,
        "elapsed_seconds": elapsed,
        "waited": wait,
        "passed": _uploads_passed(uploaded, wait=wait),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _upload_file(
    path: Path,
    rag_api_url: str,
    *,
    api_key: str | None = None,
    access_token: str | None = None,
    process_options: str | None = None,
) -> dict[str, Any]:
    return _http_upload_file(
        path,
        f"{rag_api_url.rstrip('/')}/documents/upload",
        api_key=api_key,
        access_token=access_token,
        process_options=process_options,
    )

def _wait_track_status(
    track_id: str,
    rag_api_url: str,
    *,
    timeout_seconds: int,
    poll_seconds: float,
    api_key: str | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, Any] = {}
    terminal_statuses = {"processed", "failed"}
    while time.monotonic() < deadline:
        last_payload = _get_json(
            f"{rag_api_url.rstrip('/')}/documents/track_status/{track_id}",
            api_key=api_key,
            access_token=access_token,
        )
        documents = last_payload.get("documents") or []
        statuses = {str(doc.get("status", "")).lower() for doc in documents}
        if documents and statuses <= terminal_statuses:
            last_payload["terminal"] = True
            last_payload["passed"] = statuses == {"processed"}
            return last_payload
        time.sleep(poll_seconds)
    last_payload["terminal"] = False
    last_payload["passed"] = False
    last_payload["timeout_seconds"] = timeout_seconds
    return last_payload


def _get_json(
    url: str,
    *,
    api_key: str | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    return _http_get_json(url, api_key=api_key, access_token=access_token)


def _uploads_passed(uploaded: list[dict[str, Any]], *, wait: bool) -> bool:
    if not uploaded:
        return False
    if not wait:
        return all(item.get("status") == "success" for item in uploaded)
    return all(
        item.get("status") == "success"
        and (item.get("track_status") or {}).get("passed") is True
        for item in uploaded
    )
