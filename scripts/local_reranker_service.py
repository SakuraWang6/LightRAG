#!/usr/bin/env python3
"""Cohere-compatible HTTP wrapper for a local Jina CrossEncoder reranker.

LightRAG's ``cohere`` reranker binding only requires a JSON endpoint that
accepts ``model``, ``query``, ``documents`` and optional ``top_n`` and returns
``{"results": [{"index": ..., "relevance_score": ...}]}``.  This service
keeps the model entirely local while preserving that stable contract.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DEFAULT_MODEL = "jinaai/jina-reranker-v2-base-multilingual"


class RerankRequest(BaseModel):
    model: str | None = None
    query: str = Field(min_length=1)
    documents: list[str] = Field(min_length=1)
    top_n: int | None = Field(default=None, ge=1)


class LocalReranker:
    def __init__(self, model_name: str, device: str, max_length: int, batch_size: int):
        self.model_name = model_name
        self.device = device
        self.max_length = max_length
        self.batch_size = batch_size
        self.model: Any | None = None

    def load(self) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # pragma: no cover - startup diagnostic
            raise RuntimeError(
                "Missing local reranker dependencies. Install torch and sentence-transformers."
            ) from exc

        self.model = CrossEncoder(
            self.model_name,
            trust_remote_code=True,
            max_length=self.max_length,
            device=self.device,
        )

    def score(self, query: str, documents: list[str]) -> list[float]:
        if self.model is None:
            raise RuntimeError("Reranker model has not completed startup.")
        pairs = [(query, document) for document in documents]
        scores = self.model.predict(
            pairs,
            batch_size=self.batch_size,
            show_progress_bar=False,
        )
        return [float(score) for score in scores]


def default_device() -> str:
    requested = os.getenv("LIGHTRAG_RERANK_DEVICE")
    if requested:
        return requested
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def make_app(model_name: str, device: str, max_length: int, batch_size: int) -> FastAPI:
    reranker = LocalReranker(model_name, device, max_length, batch_size)
    score_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await asyncio.to_thread(reranker.load)
        yield

    app = FastAPI(title="Local LightRAG Reranker", version="1.0")
    app.router.lifespan_context = lifespan

    @app.get("/health")
    async def health() -> dict[str, str | int]:
        return {
            "status": "healthy" if reranker.model is not None else "starting",
            "model": reranker.model_name,
            "device": reranker.device,
            "max_length": reranker.max_length,
        }

    @app.post("/rerank")
    async def rerank(request: RerankRequest) -> dict[str, list[dict[str, float | int]]]:
        try:
            async with score_lock:
                scores = await asyncio.to_thread(
                    reranker.score, request.query, request.documents
                )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Reranker unavailable: {exc}") from exc

        ranked = sorted(
            (
                {"index": index, "relevance_score": score}
                for index, score in enumerate(scores)
            ),
            key=lambda item: float(item["relevance_score"]),
            reverse=True,
        )
        if request.top_n is not None:
            ranked = ranked[: request.top_n]
        return {"results": ranked}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--model", default=os.getenv("LIGHTRAG_RERANK_MODEL", DEFAULT_MODEL))
    parser.add_argument("--device", default=default_device())
    parser.add_argument("--max-length", default=1024, type=int)
    parser.add_argument("--batch-size", default=8, type=int)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        make_app(args.model, args.device, args.max_length, args.batch_size),
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
