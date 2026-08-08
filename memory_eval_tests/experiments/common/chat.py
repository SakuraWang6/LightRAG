"""Ollama chat wrapper with per-call decoding control (num_ctx, num_predict)."""

from __future__ import annotations

import json
import urllib.request


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
) -> str:
    """Call ``/api/chat`` with explicit context window and decoding options."""
    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=json.dumps(
            {
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
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    return str((body.get("message") or {}).get("content") or "")


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
