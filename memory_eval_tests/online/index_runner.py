from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.common.http import get_json as _http_get_json
from memory_eval_tests.common.http import upload_file as _http_upload_file


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
) -> dict[str, Any]:
    client = DatasetClient(dataset_source)
    manifest = client.manifest()
    selected = set(formats or ["docx"])
    uploaded = []
    skipped = []
    started = time.perf_counter()
    for file_info in manifest.get("files", []):
        if file_info.get("status") != "created" or file_info.get("format") not in selected:
            skipped.append(file_info.get("name"))
            continue
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
            continue
        upload_result = _upload_file(
            path,
            rag_api_url,
            api_key=api_key,
            access_token=access_token,
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
) -> dict[str, Any]:
    return _http_upload_file(
        path,
        f"{rag_api_url.rstrip('/')}/documents/upload",
        api_key=api_key,
        access_token=access_token,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload generated files into LightRAG API.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--rag-api-url", default="http://127.0.0.1:9621")
    parser.add_argument("--formats", default="docx")
    parser.add_argument("--wait", action="store_true", help="Wait until uploaded tracks reach terminal status.")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--api-key", default=None, help="X-API-Key header for authenticated servers.")
    parser.add_argument(
        "--access-token",
        default=None,
        help="Bearer access token (Authorization header) for authenticated servers.",
    )
    args = parser.parse_args(argv)
    report = upload_dataset_files(
        dataset_source=args.dataset,
        rag_api_url=args.rag_api_url,
        formats=[f.strip() for f in args.formats.split(",") if f.strip()],
        wait=args.wait,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
        api_key=args.api_key,
        access_token=args.access_token,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
