"""Sentence-transformers embedder for the Retrieval bounded context.

Infrastructure-layer implementation of the Embedder Protocol, wrapping
the multilingual-e5-large model. The e5 family is trained for asymmetric
retrieval and requires distinct prefixes:

    "passage: " for texts that are stored in the index (the corpus)
    "query: "   for texts used as search queries

Omitting these prefixes silently degrades retrieval quality, so they are
applied unconditionally inside this wrapper. Callers pass plain text; the
wrapper owns the prefixing.

Embeddings are L2-normalised so that an inner-product search over the
vectors is equivalent to cosine similarity. The FaissNormIndex relies on
this: it uses an inner-product index and assumes normalised inputs.
"""

from __future__ import annotations

import numpy as np
from sentence_transformers import SentenceTransformer

# The retrieval-tuned multilingual model. Local inference, no external API,
# which keeps the embedding step inside the EU-sovereignty boundary.
_MODEL_NAME = "intfloat/multilingual-e5-large"

_PASSAGE_PREFIX = "passage: "
_QUERY_PREFIX = "query: "


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
