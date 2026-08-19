"""BGE-small chunker: client in the slim image, model in its own container.

`ChunkerClient` is imported lazily. The embed image installs torch and
sentence-transformers but deliberately not httpx — it *is* the server, so it has
no use for an HTTP client — and an eager `from embed.client import ...` here
made `python -m embed` die on ModuleNotFoundError before uvicorn ever started.
Everything the model container needs (`embed.service`, `embed.models`) stays
import-free of httpx; the slim image still gets `from embed import ChunkerClient`.
"""

from typing import TYPE_CHECKING

from embed.models import EMBED_DIM, Chunk
from embed.policy import EMBEDDABLE_NODE_TYPES, should_embed, worth_embedding

if TYPE_CHECKING:
    from embed.client import ChunkerClient, ChunkerError

_LAZY = {"ChunkerClient", "ChunkerError"}

__all__ = [
    "EMBED_DIM",
    "EMBEDDABLE_NODE_TYPES",
    "Chunk",
    "ChunkerClient",
    "ChunkerError",
    "should_embed",
    "worth_embedding",
]


def __getattr__(name: str) -> object:
    if name in _LAZY:
        from embed import client

        return getattr(client, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
