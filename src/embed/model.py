"""BAAI/bge-small-en-v1.5 loaded once per process.

Lives only in the embed container. Ingest workers talk to it over HTTP; they
do not import this module, and torch is not in the slim service image.
"""

from __future__ import annotations

import logging

from sentence_transformers import SentenceTransformer

from embed.models import EMBED_DIM

log = logging.getLogger("embed.model")

DEFAULT_MODEL_ID = "BAAI/bge-small-en-v1.5"

# BGE's retrieval instruction. Passages (chunks) are stored without it; queries
# are prefixed so the ANN space matches what the model card specifies.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder:
    """Tokenizer + encoder. Encode is CPU-bound; callers serialize with a lock."""

    def __init__(self, model_id: str = DEFAULT_MODEL_ID) -> None:
        log.info("loading %s", model_id)
        self.model_id = model_id
        self._model = SentenceTransformer(model_id)
        probe = self._model.encode(
            ["dim check"],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        dim = int(probe.shape[1])
        if dim != EMBED_DIM:
            raise RuntimeError(
                f"{model_id} produced dim {dim}, schema expects {EMBED_DIM}"
            )
        self.dim = dim
        log.info("ready; dim=%d max_seq=%s", dim, self._model.max_seq_length)

    def token_count(self, text: str) -> int:
        # Specials are added at encode time; budget content only so a 384-token
        # chunk still fits in 512 with [CLS]/[SEP].
        return len(self._model.tokenizer.encode(text, add_special_tokens=False))

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts)

    def encode_query(self, text: str) -> list[float]:
        return self._encode([QUERY_PREFIX + text])[0]

    def _encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=32,
        )
        return [row.tolist() for row in vectors]
