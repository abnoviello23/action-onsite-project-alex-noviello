"""Wire types for the chunker service and its client.

Kept free of torch so the ingest workers can import `Chunk` / `ChunkerClient`
from the slim Python image.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Declared in 001_core.sql as vector(384). Startup refuses a model that
# disagrees rather than writing the wrong width into pgvector.
EMBED_DIM = 384


class Chunk(BaseModel):
    """One passage plus its BGE vector, ready to write to `node_chunk`."""

    model_config = ConfigDict(frozen=True)

    ord: int
    text: str
    embedding: list[float] = Field(min_length=EMBED_DIM, max_length=EMBED_DIM)


class ChunkRequest(BaseModel):
    content: str
    title: str | None = None
    # The node type this content came from, so the service can apply the right
    # policy rather than guessing from shape. Optional, and omitting it keeps
    # the old behaviour: judge the text on its own.
    kind: str | None = None


class ChunkResponse(BaseModel):
    chunks: list[Chunk]


class EmbedQueryRequest(BaseModel):
    text: str


class EmbedQueryResponse(BaseModel):
    embedding: list[float] = Field(min_length=EMBED_DIM, max_length=EMBED_DIM)
    dim: int = EMBED_DIM
