"""Sentence-transformers embedder: evaluated, rejected, kept as reference.

Reference implementation of the vector-search path the Retrieval context
evaluated and rejected (ADR-021). Production resolves norm citations by exact
dict lookup (retrieval/service.py); the vector fallback resolved zero real
citations on the Phase A ground truth and produced a confident-wrong match on
an out-of-corpus probe, so it was removed from the production path. This module
lived in src/app/retrieval through Round 17; Round 20 (M2) moved it here so the
production package no longer carries torch/faiss/sentence-transformers for code
that never runs, and the directory name states what is true. The decision is
reversible: if production data later shows drift that exact-match misses, the
hybrid path is reinstated from here with a calibrated confidence floor.

It wraps the multilingual-e5-large model. The e5 family is trained for
asymmetric retrieval and requires distinct prefixes:

    "passage: " for texts that are stored in the index (the corpus)
    "query: "   for texts used as search queries

Omitting these prefixes silently degrades retrieval quality, so they are
applied unconditionally inside this wrapper. Callers pass plain text; the
wrapper owns the prefixing.

Embeddings are L2-normalised so that an inner-product search over the
vectors is equivalent to cosine similarity. The FaissNormIndex relies on
this: it uses an inner-product index and assumes normalised inputs.

Requires the optional vector-experiments extra (pyproject.toml):
``pip install -e .[vector-experiments]``.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
from sentence_transformers import SentenceTransformer

# The retrieval-tuned multilingual model. Local inference, no external API,
# which keeps the embedding step inside the EU-sovereignty boundary.
_MODEL_NAME = "intfloat/multilingual-e5-large"

_PASSAGE_PREFIX = "passage: "
_QUERY_PREFIX = "query: "


class Embedder(Protocol):
    """Abstract interface for turning text into dense vectors.

    Moved here with its only implementation in Round 20 (M2): no production
    code referenced it once the vector path left src/app/retrieval, so the
    interface travels with E5Embedder rather than remaining a dead protocol in
    the context's entities module. The asymmetric query/passage distinction is
    part of the contract because retrieval-tuned models such as the e5 family
    require different handling for indexed passages versus search queries.
    """

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """Embed texts that will be stored in the index (the corpus side)."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single text that will be used as a search query."""
        ...


class E5Embedder:
    """multilingual-e5-large wrapper implementing the Embedder Protocol.

    Loads the model once at construction. Applies the e5 query/passage
    prefixes and L2-normalises outputs for cosine-equivalent inner-product
    search.

    Attributes:
        _model: The loaded SentenceTransformer instance.
    """

    def __init__(self, model_name: str = _MODEL_NAME) -> None:
        """Load the embedding model.

        Args:
            model_name: HuggingFace model identifier. Defaults to
                multilingual-e5-large. Overridable for tests that want a
                smaller, faster model.
        """
        self._model = SentenceTransformer(model_name)

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """Embed corpus texts with the passage prefix.

        Args:
            texts: Plain paragraph texts to index. The passage prefix is
                applied internally.

        Returns:
            A list of L2-normalised embedding vectors, one per input text,
            in the same order. Empty list for empty input.
        """
        if not texts:
            return []
        prefixed = [f"{_PASSAGE_PREFIX}{t}" for t in texts]
        vectors = self._model.encode(
            prefixed,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return vectors.astype(np.float32).tolist()

    def embed_query(self, text: str) -> list[float]:
        """Embed a single search query with the query prefix.

        Args:
            text: The plain query text. The query prefix is applied
                internally.

        Returns:
            A single L2-normalised embedding vector.
        """
        prefixed = f"{_QUERY_PREFIX}{text}"
        vector = self._model.encode(
            prefixed,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return vector.astype(np.float32).tolist()
