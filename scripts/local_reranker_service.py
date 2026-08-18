#!/usr/bin/env python3
"""Serve a local multilingual reranker with a Cohere-compatible API.

The service intentionally listens only on the loopback interface when started
through ``start_local_reranker.sh``.  LightRAG's ``cohere`` rerank binding
expects ``POST /rerank`` with ``query`` and ``documents`` and consumes the
``results`` array returned here.

The model is public and runs locally; no API key is read or required.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Sequence
from typing import Any

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModelForSequenceClassification, AutoTokenizer


MODEL_NAME = os.getenv(
    "LOCAL_RERANK_MODEL", "jinaai/jina-reranker-v2-base-multilingual"
)
MODEL_REVISION = os.getenv(
    "LOCAL_RERANK_REVISION", "9cfeff2df7d40d1b78e75e5e9cebec92a99813c9"
)
MAX_LENGTH = int(os.getenv("LOCAL_RERANK_MAX_LENGTH", "1024"))
BATCH_SIZE = int(os.getenv("LOCAL_RERANK_BATCH_SIZE", "8"))
DEVICE = torch.device("mps" if torch.backends.mps.is_available() else "cpu")


class RerankRequest(BaseModel):
    """Subset of the Cohere-compatible rerank request used by LightRAG."""

    model: str | None = None
    query: str = Field(min_length=1)
    documents: list[str] = Field(min_length=1)
    top_n: int | None = Field(default=None, ge=1)


app = FastAPI(title="Local multilingual reranker", version="1.0")
_model: Any | None = None
_tokenizer: Any | None = None
_inference_lock = threading.Lock()


def _load_model() -> None:
    """Load once before opening the HTTP port, so health means ready."""

    global _model, _tokenizer
    _tokenizer = AutoTokenizer.from_pretrained(
        MODEL_NAME, revision=MODEL_REVISION, trust_remote_code=True
    )
    _model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME,
        revision=MODEL_REVISION,
        trust_remote_code=True,
    ).to(DEVICE).float()
    _model.eval()


@app.on_event("startup")
def startup() -> None:
    _load_model()


@app.get("/health")
def health() -> dict[str, str]:
    if _model is None or _tokenizer is None:
        raise HTTPException(status_code=503, detail="model is still loading")
    return {"status": "healthy", "model": MODEL_NAME, "device": str(DEVICE)}


def _batched(items: Sequence[str], size: int) -> list[tuple[int, list[str]]]:
    return [(start, list(items[start : start + size])) for start in range(0, len(items), size)]


@app.post("/rerank")
def rerank(request: RerankRequest) -> dict[str, Any]:
    if _model is None or _tokenizer is None:
        raise HTTPException(status_code=503, detail="model is still loading")

    scores: list[float] = []
    # MPS inference is kept serial: the LightRAG client has its own concurrency
    # limit, while overlapping cross-encoder batches causes memory spikes on a Mac.
    with _inference_lock, torch.inference_mode():
        for _, documents in _batched(request.documents, BATCH_SIZE):
            encoded = _tokenizer(
                [request.query] * len(documents),
                documents,
                padding=True,
                truncation=True,
                max_length=MAX_LENGTH,
                return_tensors="pt",
            )
            encoded = {name: value.to(DEVICE) for name, value in encoded.items()}
            logits = _model(**encoded).logits.reshape(-1)
            scores.extend(float(score) for score in logits.float().cpu())

    ordered = sorted(
        (
            {"index": index, "relevance_score": score}
            for index, score in enumerate(scores)
        ),
        key=lambda result: result["relevance_score"],
        reverse=True,
    )
    if request.top_n is not None:
        ordered = ordered[: request.top_n]

    return {"results": ordered, "meta": {"model": MODEL_NAME}}
