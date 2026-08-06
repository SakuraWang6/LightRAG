from __future__ import annotations

import argparse
import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def run_api_preflight(
    *,
    rag_api_url: str = "http://127.0.0.1:9621",
    ollama_url: str = "http://127.0.0.1:11434",
) -> dict[str, Any]:
    _load_project_env()
    env = {
        "OPENAI_API_KEY": _is_set("OPENAI_API_KEY"),
        "LLM_BINDING": os.getenv("LLM_BINDING", ""),
        "EMBEDDING_BINDING": os.getenv("EMBEDDING_BINDING", ""),
        "LLM_BINDING_API_KEY": _is_set("LLM_BINDING_API_KEY"),
        "EMBEDDING_BINDING_API_KEY": _is_set("EMBEDDING_BINDING_API_KEY"),
    }
    ollama = _probe_json(f"{ollama_url.rstrip('/')}/api/tags")
    api_health = _probe_url(f"{rag_api_url.rstrip('/')}/health")
    api_docs = _probe_url(f"{rag_api_url.rstrip('/')}/docs")
    api_query_data = _probe_url(f"{rag_api_url.rstrip('/')}/query/data", method="POST")

    llm_ready = bool(env["OPENAI_API_KEY"]) or bool(env["LLM_BINDING_API_KEY"]) or ollama["reachable"]
    embedding_ready = (
        bool(env["OPENAI_API_KEY"])
        or bool(env["EMBEDDING_BINDING_API_KEY"])
        or ollama["reachable"]
    )
    api_ready = api_health["reachable"] or api_docs["reachable"] or api_query_data["reachable"]
    blockers = []
    if not llm_ready:
        blockers.append("no configured LLM backend detected")
    if not embedding_ready:
        blockers.append("no configured embedding backend detected")
    if not api_ready:
        blockers.append("LightRAG API server is not reachable")
    return {
        "rag_api_url": rag_api_url,
        "ollama_url": ollama_url,
        "environment": env,
        "ollama": ollama,
        "api": {
            "health": api_health,
            "docs": api_docs,
            "query_data": api_query_data,
        },
        "llm_ready": llm_ready,
        "embedding_ready": embedding_ready,
        "api_ready": api_ready,
        "ready_for_online_eval": not blockers,
        "blockers": blockers,
    }


def _load_project_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _is_set(name: str) -> bool:
    return bool(os.getenv(name))


def _probe_json(url: str) -> dict[str, Any]:
    result = _probe_url(url)
    if not result["reachable"]:
        return result
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        result["json"] = payload
    except Exception as exc:
        result["json_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _probe_url(url: str, *, method: str = "GET") -> dict[str, Any]:
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return {
                "url": url,
                "method": method,
                "reachable": True,
                "status": response.status,
            }
    except urllib.error.HTTPError as exc:
        return {
            "url": url,
            "method": method,
            "reachable": True,
            "status": exc.code,
            "http_error": exc.reason,
        }
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        return {
            "url": url,
            "method": method,
            "reachable": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check prerequisites for online LightRAG evaluation.")
    parser.add_argument("--rag-api-url", default="http://127.0.0.1:9621")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-blocker", action="store_true")
    args = parser.parse_args(argv)

    report = run_api_preflight(rag_api_url=args.rag_api_url, ollama_url=args.ollama_url)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 1 if args.fail_on_blocker and not report["ready_for_online_eval"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
