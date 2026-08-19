"""HTTP wrapper around BGE-small: chunk a document, embed a query.

The model stays in this process. Everyone else uses `ChunkerClient`. Channel
names and other labels are rejected here (`worth_embedding`) even if a caller
sends them — they are not documents.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from embed.chunk import MAX_CHUNKS, chunk_document
from embed.model import DEFAULT_MODEL_ID, Embedder
from embed.models import (
    EMBED_DIM,
    Chunk,
    ChunkRequest,
    ChunkResponse,
    EmbedQueryRequest,
    EmbedQueryResponse,
)
from embed.policy import should_embed

log = logging.getLogger("embed")

# Drive already clamps bodies; this is a memory bound for a bad caller.
_MAX_CONTENT_CHARS = 200_000


@asynccontextmanager
async def lifespan(app: FastAPI):
    model_id = os.environ.get("EMBED_MODEL", DEFAULT_MODEL_ID)
    app.state.embedder = Embedder(model_id)
    app.state.lock = asyncio.Lock()
    log.info("model loaded")
    try:
        yield
    finally:
        app.state.embedder = None


app = FastAPI(title="BGE chunker", lifespan=lifespan)


def _embedder(request: Request) -> Embedder:
    embedder = getattr(request.app.state, "embedder", None)
    if embedder is None:
        raise HTTPException(status_code=503, detail="model loading")
    return embedder


@app.get("/health")
async def health(request: Request) -> dict:
    embedder = getattr(request.app.state, "embedder", None)
    if embedder is None:
        raise HTTPException(status_code=503, detail="loading")
    return {
        "status": "ok",
        "model": embedder.model_id,
        "dim": embedder.dim,
    }


@app.post("/chunk", response_model=ChunkResponse)
async def chunk(payload: ChunkRequest, request: Request) -> ChunkResponse:
    """Split `content` and embed each passage. Empty list if it is a label."""
    embedder = _embedder(request)
    content = payload.content or ""
    if len(content) > _MAX_CONTENT_CHARS:
        content = content[: _MAX_CONTENT_CHARS - 1] + "…"
    if not should_embed(payload.kind, content):
        return ChunkResponse(chunks=[])

    passages = chunk_document(
        content,
        embedder.token_count,
        title=payload.title,
    )
    if len(passages) > MAX_CHUNKS:
        log.warning("truncating %d passages to %d", len(passages), MAX_CHUNKS)
        passages = passages[:MAX_CHUNKS]
    if not passages:
        return ChunkResponse(chunks=[])

    vectors = await _encode_passages(request, embedder, passages)
    chunks = [
        Chunk(ord=i, text=text, embedding=vec)
        for i, (text, vec) in enumerate(zip(passages, vectors, strict=True))
    ]
    return ChunkResponse(chunks=chunks)


@app.post("/embed_query", response_model=EmbedQueryResponse)
async def embed_query(
    payload: EmbedQueryRequest, request: Request
) -> EmbedQueryResponse:
    """One query vector. Applies BGE's retrieval instruction prefix."""
    embedder = _embedder(request)
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty query")
    vector = await _encode_query(request, embedder, text)
    if len(vector) != EMBED_DIM:
        raise HTTPException(status_code=500, detail="embedding width mismatch")
    return EmbedQueryResponse(embedding=vector, dim=EMBED_DIM)


async def _encode_passages(
    request: Request, embedder: Embedder, texts: list[str]
) -> list[list[float]]:
    async with request.app.state.lock:
        return await asyncio.to_thread(embedder.encode_passages, texts)


async def _encode_query(
    request: Request, embedder: Embedder, text: str
) -> list[float]:
    async with request.app.state.lock:
        return await asyncio.to_thread(embedder.encode_query, text)
