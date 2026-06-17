"""FAISS vector index: evaluated, rejected, kept as reference (ADR-021).

The index half of the vector-search path the Retrieval context evaluated and
rejected; production resolves by exact dict lookup (retrieval/service.py). Moved
out of src/app/retrieval in Round 20 (M2) so the production package no longer
ships faiss for code that never runs. It still imports GesetzParagraph from the
production context (app.retrieval.entities), the one type it shares with the
live path. Requires the optional vector-experiments extra.

Infrastructure-layer component holding paragraph embeddings and the
parallel metadata needed to map a search hit back to its GesetzParagraph.
Uses an inner-product index (IndexFlatIP); combined with L2-normalised
embeddings from the E5Embedder, inner product equals cosine similarity.

The index supports Gesetz-filtered search: a query for a WHG citation
restricts candidates to WHG paragraphs, so a § 9 from another statute can
never be returned for a § 9 WHG query. The filter is applied by querying
a wider top-k from FAISS and then keeping only the hits whose paragraph
belongs to the requested Gesetz.
"""

from __future__ import annotations

import faiss
import numpy as np

from app.retrieval.entities import GesetzParagraph


class FaissNormIndex:
    """In-memory FAISS index over statute paragraph embeddings.

    Holds the embedding matrix, the parallel list of GesetzParagraph
    entities, and a Gesetz-to-row-indices map for filtered search. Built
    once from a corpus; queried many times.

    Attributes:
        _index: The FAISS inner-product index.
        _paragraphs: Parallel list of paragraphs, aligned to index rows.
        _rows_by_gesetz: Map from Gesetz abbreviation to the row indices
            of its paragraphs, used for the Gesetz filter.
        _dim: Embedding dimensionality.
    """

    def __init__(
        self,
        paragraphs: list[GesetzParagraph],
        embeddings: list[list[float]],
    ) -> None:
        """Build the index from paragraphs and their embeddings.

        Args:
            paragraphs: The corpus paragraphs, in the same order as
                embeddings.
            embeddings: L2-normalised embedding vectors, one per
                paragraph, in the same order.

        Raises:
            ValueError: If the counts differ or the inputs are empty.
        """
        if len(paragraphs) != len(embeddings):
            raise ValueError(
                f"paragraph count {len(paragraphs)} does not match "
                f"embedding count {len(embeddings)}"
            )
        if not paragraphs:
            raise ValueError("Cannot build an index from an empty corpus.")

        matrix = np.asarray(embeddings, dtype=np.float32)
        self._dim = matrix.shape[1]
        self._index = faiss.IndexFlatIP(self._dim)
        self._index.add(matrix)

        self._paragraphs = paragraphs
        self._rows_by_gesetz: dict[str, list[int]] = {}
        for row, paragraph in enumerate(paragraphs):
            self._rows_by_gesetz.setdefault(paragraph.gesetz, []).append(row)

    def search(
        self,
        query_embedding: list[float],
        gesetz: str,
        top_k: int = 3,
    ) -> list[tuple[GesetzParagraph, float]]:
        """Return the top-k paragraphs of one Gesetz for a query embedding.

        Queries a wider candidate pool from FAISS, then keeps only hits
        belonging to the requested Gesetz, preserving similarity order.
        Restricting after the FAISS query (rather than maintaining a
        separate index per Gesetz) keeps the index simple; the widening
        factor compensates for candidates filtered out.

        Args:
            query_embedding: The L2-normalised query vector.
            gesetz: The statute abbreviation to restrict results to.
            top_k: Number of within-Gesetz results to return.

        Returns:
            A list of (paragraph, score) tuples for the requested Gesetz,
            highest score first, at most top_k entries. Empty list if the
            Gesetz is absent from the corpus.
        """
        gesetz_rows = self._rows_by_gesetz.get(gesetz)
        if not gesetz_rows:
            return []

        # Widen the FAISS query so that, after filtering to the requested
        # Gesetz, enough within-Gesetz candidates remain. Capped at the
        # corpus size.
        widened = min(len(self._paragraphs), top_k * 20)
        query = np.asarray([query_embedding], dtype=np.float32)
        scores, indices = self._index.search(query, widened)

        gesetz_row_set = set(gesetz_rows)
        results: list[tuple[GesetzParagraph, float]] = []
        for score, row in zip(scores[0], indices[0]):
            if row == -1:
                continue
            if int(row) in gesetz_row_set:
                results.append((self._paragraphs[int(row)], float(score)))
            if len(results) >= top_k:
                break
        return results

    def size(self) -> int:
        """Return the number of indexed paragraphs."""
        return len(self._paragraphs)
