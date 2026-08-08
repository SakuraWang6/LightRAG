from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from memory_eval_tests.common.dataset_client import DatasetClient


def upload_dataset_files(
    *,
    dataset_source: str,
    rag_api_url: str,
    formats: list[str] | None = None,
    wait: bool = False,
    timeout_seconds: int = 900,
    poll_seconds: float = 5.0,
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
        upload_result = _upload_file(path, rag_api_url)
        if wait and upload_result.get("track_id"):
            upload_result["track_status"] = _wait_track_status(
                str(upload_result["track_id"]),
                rag_api_url,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
            )
        uploaded.append(upload_result)
    elapsed = time.perf_counter() - started
    return {
        "uploaded": uploaded,
        "skipped": skipped,
        "elapsed_seconds": elapsed,
        "waited": wait,
        "passed": _uploads_passed(uploaded, wait=wait),
    }


def _upload_file(path: Path, rag_api_url: str) -> dict[str, Any]:
    boundary = "----lightragMemoryEvalBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = urllib.request.Request(
        f"{rag_api_url.rstrip('/')}/documents/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_track_status(
    track_id: str,
    rag_api_url: str,
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, Any] = {}
    terminal_statuses = {"processed", "failed"}
    while time.monotonic() < deadline:
        last_payload = _get_json(f"{rag_api_url.rstrip('/')}/documents/track_status/{track_id}")
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


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


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
    args = parser.parse_args(argv)
    report = upload_dataset_files(
        dataset_source=args.dataset,
        rag_api_url=args.rag_api_url,
        formats=[f.strip() for f in args.formats.split(",") if f.strip()],
        wait=args.wait,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
