"""Ollama chat wrapper with per-call decoding control and hard deadlines.

The HTTP client reads the response body incrementally with a per-read socket
timeout and an overall wall-clock deadline, so a stuck Ollama request surfaces
as a ``TimeoutError`` within a bounded window instead of hanging the experiment
for tens of minutes. A single retry covers transient model-load stalls.
"""

from __future__ import annotations

import http.client
import json
import socket
import time
from urllib.parse import urlsplit


class ChatTimeoutError(TimeoutError):
    """Raised when an Ollama request exceeds the hard deadline."""


def chat_ollama(
    *,
    host: str,
    model: str,
    system: str,
    user: str,
    num_predict: int,
    num_ctx: int = 16384,
    temperature: float = 0,
    timeout: int = 600,
    read_timeout: int = 60,
    retries: int = 1,
) -> str:
    """Call ``/api/chat`` with explicit context window and decoding options."""
    payload = {
        "model": model,
        "stream": False,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
        "think": False,
    }
    parsed = urlsplit(host if "://" in host else f"http://{host}")
    target_host = parsed.netloc or parsed.path
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        deadline = time.monotonic() + timeout
        try:
            conn = http.client.HTTPConnection(target_host, timeout=read_timeout)
            try:
                conn.request(
                    "POST",
                    "/api/chat",
                    body=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                chunks: list[bytes] = []
                while True:
                    if time.monotonic() > deadline:
                        raise ChatTimeoutError(
                            f"Ollama chat exceeded {timeout}s deadline (model={model}, num_ctx={num_ctx})"
                        )
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    chunks.append(chunk)
                body = json.loads(b"".join(chunks).decode("utf-8"))
            finally:
                conn.close()
            return str((body.get("message") or {}).get("content") or "")
        except (socket.timeout, TimeoutError, http.client.HTTPException, ConnectionError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2)
                continue
            raise
    raise last_error if last_error is not None else ChatTimeoutError("Ollama chat failed")


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for prompt preflight (mixed zh/en)."""
    if not text:
        return 0
    # CJK characters cost ~1 token; ASCII ~4 chars per token. Take the worst
    # of the two heuristics so the preflight is conservative.
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    ascii_tokens = (len(text) - cjk) // 3
    return cjk + ascii_tokens + 1


def context_check(prompt: str, num_ctx: int, arm: str) -> dict:
    """Preflight a prompt against the context window; never blocks execution."""
    estimated = estimate_tokens(prompt)
    return {
        "arm": arm,
        "estimated_tokens": estimated,
        "num_ctx": num_ctx,
        "overflow": estimated > num_ctx,
        "truncated": None,  # filled by the caller when the model result is known
    }
