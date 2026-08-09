"""Ollama chat wrapper with per-call decoding control and hard deadlines.

The request uses ``stream=True``: a healthy generation continuously produces
token deltas (so per-read socket timeouts never fire for slow but legitimate
32K-context generations), while a stuck request stops producing bytes and is
surfaced as a ``TimeoutError`` within ``read_timeout`` seconds. An overall
wall-clock deadline bounds the whole call, and one retry covers transient
model-load stalls.
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
        "stream": True,
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
                content_parts: list[str] = []
                buffer = b""
                done = False
                while True:
                    if time.monotonic() > deadline:
                        raise ChatTimeoutError(
                            f"Ollama chat exceeded {timeout}s deadline (model={model}, num_ctx={num_ctx})"
                        )
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line.decode("utf-8"))
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        if obj.get("error"):
                            raise RuntimeError(str(obj["error"]))
                        content_parts.append(str((obj.get("message") or {}).get("content") or ""))
                        if obj.get("done"):
                            done = True
                            break
                    if done:
                        break
            finally:
                conn.close()
            text = "".join(content_parts).strip()
            if not text:
                raise RuntimeError("Empty Ollama response")
            return text
        except (socket.timeout, TimeoutError, http.client.HTTPException, ConnectionError, RuntimeError) as exc:
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
