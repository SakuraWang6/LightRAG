"""Local read-only web console for comparing recall-lab runs."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"


def _default_runs_root() -> Path:
    configured = os.getenv("MEMORY_RECALL_RUNS_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return (PACKAGE_DIR / "runs").resolve()


def _json_bytes(payload: Any, status: int = HTTPStatus.OK) -> tuple[int, bytes]:
    return status, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def _run_summary(run_dir: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": run_dir.name,
        "path": str(run_dir),
        "has_recall_report": (run_dir / "recall_report.json").exists(),
    }
    run_json = run_dir / "run.json"
    if run_json.exists():
        try:
            envelope = json.loads(run_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            envelope = {}
        payload["label"] = envelope.get("label") or run_dir.name
        payload["status"] = envelope.get("status")
        payload["dataset"] = envelope.get("dataset")
        payload["baseline"] = envelope.get("baseline") or {}
        payload["finished_at"] = envelope.get("finished_at")
    else:
        payload["label"] = run_dir.name
    return payload


class RecallLabHandler(BaseHTTPRequestHandler):
    runs_root: Path = _default_runs_root()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/static/"):
            relative = unquote(parsed.path[len("/static/") :])
            self._serve_static(relative)
            return
        if parsed.path == "/api/runs":
            self._send_json({"runs": self._list_runs()})
            return
        if parsed.path == "/api/run":
            self._serve_run(parse_qs(parsed.query))
            return
        if parsed.path == "/api/compare":
            self._serve_compare(parse_qs(parsed.query))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _list_runs(self) -> list[dict[str, Any]]:
        if not self.runs_root.exists():
            return []
        runs: list[dict[str, Any]] = []
        for candidate in self.runs_root.iterdir():
            if not candidate.is_dir():
                continue
            summary = _run_summary(candidate)
            if summary["has_recall_report"]:
                summary["modified_at"] = candidate.stat().st_mtime
                runs.append(summary)
        runs.sort(key=lambda item: item.get("modified_at") or 0, reverse=True)
        return runs

    def _resolve_run(self, raw_path: str) -> Path:
        path = Path(unquote(raw_path)).expanduser()
        if not path.is_absolute():
            path = self.runs_root / path
        resolved = path.resolve()
        try:
            resolved.relative_to(self.runs_root.resolve())
        except ValueError as exc:
            raise ValueError("run path is outside the configured runs root") from exc
        if not resolved.is_dir():
            raise ValueError("run path is not a directory")
        return resolved

    def _serve_run(self, query: dict[str, list[str]]) -> None:
        raw = (query.get("path") or [""])[0]
        try:
            run_dir = self._resolve_run(raw)
            payload = _run_summary(run_dir)
            report_path = run_dir / "recall_report.json"
            if report_path.exists():
                payload["recall"] = json.loads(report_path.read_text(encoding="utf-8"))
            self._send_json(payload)
        except (OSError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _serve_compare(self, query: dict[str, list[str]]) -> None:
        paths = query.get("path") or query.get("paths") or []
        if not paths:
            self._send_json({"runs": []})
            return
        if len(paths) == 1 and "," in paths[0]:
            paths = paths[0].split(",")
        runs = []
        for raw in paths:
            try:
                run_dir = self._resolve_run(raw)
            except (OSError, ValueError):
                continue
            summary = _run_summary(run_dir)
            report_path = run_dir / "recall_report.json"
            if report_path.exists():
                summary["recall"] = json.loads(report_path.read_text(encoding="utf-8"))
            runs.append(summary)
        self._send_json({"runs": runs})

    def _serve_static(self, relative: str) -> None:
        target = (STATIC_DIR / relative).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = (
            mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        )
        if target.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        self._serve_file(target, content_type)

    def _serve_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        code, body = _json_bytes(payload, status)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Recall-lab local comparison console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8710)
    parser.add_argument("--runs-root", type=Path, default=None)
    args = parser.parse_args(argv)
    RecallLabHandler.runs_root = (
        args.runs_root.expanduser().resolve()
        if args.runs_root
        else _default_runs_root()
    )
    RecallLabHandler.runs_root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), RecallLabHandler)
    print(
        f"Recall Lab: http://{args.host}:{args.port}  runs={RecallLabHandler.runs_root}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
