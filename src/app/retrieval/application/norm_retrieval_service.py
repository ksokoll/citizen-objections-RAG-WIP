"""Norm retrieval service for the Retrieval bounded context.

Application-layer orchestrator implementing the Retriever Protocol.
Resolves canonical norm citations to their source Gesetzestext using a
fixed hybrid strategy: exact-match on the paragraph-level key first,
vector-similarity fallback with Gesetz-suffix filtering on a miss.

The service depends on the Embedder and FaissNormIndex via constructor
injection; it does not import concrete infrastructure types beyond what
is needed for type hints, and all external behaviour sits behind those
collaborators.
"""

from __future__ import annotations

import re

from app.core.failures import RetrievalError
from app.retrieval.domain.entities import (
    Embedder,
    GesetzParagraph,
    NormWithSource,
)
from app.retrieval.infrastructure.faiss_norm_index import FaissNormIndex

# Parses a canonical citation into its paragraph and Gesetz components.
# Captures the section (with optional letter suffix) and the trailing
# Gesetz abbreviation, ignoring any Abs./S./Nr. specifics in between.
_CITATION_PATTERN = re.compile(
    r"^§\s*(?P<section>\d+[a-z]?)\b.*?\s(?P<gesetz>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß]+)$"
)

# Minimum cosine similarity for a vector-fallback hit to count as
# resolved. Below this, the citation is reported unresolved rather than
# returning a weak, likely-wrong match.
_VECTOR_CONFIDENCE_FLOOR = 0.80


class NormRetrievalService:
    """Hybrid resolver: exact-match dictionary then vector fallback.

    Attributes:
        _index: The FAISS index over the statute corpus.
        _embedder: The query embedder for the vector fallback.
        _exact: Map from paragraph-level canonical key to its paragraph.
    """

    def __init__(
        self,
        index: FaissNormIndex,
        embedder: Embedder,
        paragraphs: list[GesetzParagraph],
    ) -> None:
        """Wire the service to its corpus, index, and embedder.

        Args:
            index: The built FAISS index over the same paragraphs.
            embedder: The query-side embedder for the vector fallback.
            paragraphs: The corpus paragraphs, used to build the
                exact-match lookup dictionary.

        Raises:
            RetrievalError: If the index is empty.
        """
        if index.size() == 0:
            raise RetrievalError("Cannot construct service with an empty index.")
        self._index = index
        self._embedder = embedder
        self._exact = {p.canonical_key: p for p in paragraphs}

    def resolve(self, citations: list[str]) -> list[NormWithSource]:
        """Resolve each citation to its source text via the hybrid strategy.

        Args:
            citations: Canonical citation strings from Triage.

        Returns:
            One NormWithSource per input citation, in input order.
        """
        return [self._resolve_one(c) for c in citations]

    def _resolve_one(self, citation: str) -> NormWithSource:
        """Resolve a single citation: exact-match, then vector fallback."""
        parsed = self._parse_citation(citation)
        if parsed is None:
            return self._unresolved(citation)

        section, gesetz = parsed
        paragraph_key = f"§ {section} {gesetz}"

        exact = self._exact.get(paragraph_key)
        if exact is not None:
            return NormWithSource(
                canonical_citation=citation,
                paragraph_key=exact.canonical_key,
                source_text=exact.text,
                method="exact",
                confidence=None,
                resolved=True,
            )

        return self._vector_fallback(citation, gesetz)

    def _vector_fallback(self, citation: str, gesetz: str) -> NormWithSource:
        """Resolve via Gesetz-filtered vector search on an exact-match miss."""
        query_vec = self._embedder.embed_query(citation)
        try:
            hits = self._index.search(query_vec, gesetz=gesetz, top_k=1)
        except Exception as exc:
            raise RetrievalError(f"Vector search failed: {exc}") from exc

        if not hits:
            return self._unresolved(citation)

        paragraph, score = hits[0]
        if score < _VECTOR_CONFIDENCE_FLOOR:
            return self._unresolved(citation)

        return NormWithSource(
            canonical_citation=citation,
            paragraph_key=paragraph.canonical_key,
            source_text=paragraph.text,
            method="vector",
            confidence=score,
            resolved=True,
        )

    @staticmethod
    def _parse_citation(citation: str) -> tuple[str, str] | None:
        """Split a canonical citation into (section, gesetz).

        Args:
            citation: A canonical string such as "§ 9 Abs. 1 Nr. 1 WHG".

        Returns:
            A (section, gesetz) tuple such as ("9", "WHG"), or None if
            the citation does not parse into the expected shape.
        """
        collapsed = " ".join(citation.split())
        match = _CITATION_PATTERN.match(collapsed)
        if match is None:
            return None
        return match.group("section"), match.group("gesetz")

    @staticmethod
    def _unresolved(citation: str) -> NormWithSource:
        """Build the unresolved result for a citation."""
        return NormWithSource(
            canonical_citation=citation,
            paragraph_key="",
            source_text="",
            method="none",
            confidence=None,
            resolved=False,
        )
