"""Norm retrieval service for the Retrieval bounded context.

Service implementing the Retriever Protocol. Resolves canonical norm
citations to their source Gesetzestext by exact-match lookup on the
paragraph-level key (ADR-021). A citation more specific than a paragraph
(carrying Abs./Satz/Nr.) is normalised to its paragraph-level key before
lookup. A citation that does not resolve is reported as unresolved rather
than guessed.

The vector-similarity fallback was removed from the production path
(ADR-021): it resolved zero real citations on the Phase A ground truth and
produced a confident-wrong match on an out-of-corpus probe. The E5Embedder
and FaissNormIndex remain in this context as reversible experimental
reference and are deliberately not imported here.
"""

from __future__ import annotations

import re

from app.observability.tracing import traced
from app.retrieval.entities import LoadedCorpus, NormWithSource

# Parses a canonical citation into its paragraph and Gesetz components.
# Captures the section (with optional letter suffix) and the trailing
# Gesetz abbreviation, ignoring any Abs./S./Nr. specifics in between.
_CITATION_PATTERN = re.compile(
    r"^§\s*(?P<section>\d+[a-z]?)\b.*?\s(?P<gesetz>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß]+)$"
)


class NormRetrievalService:
    """Exact-match resolver from canonical citation to source Gesetzestext.

    Attributes:
        _exact: Map from paragraph-level canonical key to its paragraph.
        _corpus_id: Content identifier of the corpus behind _exact.
    """

    def __init__(self, corpus: LoadedCorpus) -> None:
        """Build the exact-match lookup from a loaded corpus.

        The service is built from the LoadedCorpus value type, never from a
        bare paragraph list plus a separate id string, so the corpus_id it
        exposes is the one computed over exactly the paragraphs it resolves
        against (ADR-028, provenance).

        Args:
            corpus: The loaded corpus whose paragraphs are keyed by their
                canonical_key (e.g. "§ 9 BauGB") for exact-match lookup.
        """
        self._exact = {p.canonical_key: p for p in corpus.paragraphs}
        self._corpus_id = corpus.corpus_id

    @property
    def corpus_id(self) -> str:
        """Content identifier of the statute corpus this service resolves against."""
        return self._corpus_id

    @traced(stage="retrieval")
    def resolve(self, citations: list[str]) -> list[NormWithSource]:
        """Resolve each citation to its source text via exact match.

        Args:
            citations: Canonical citation strings from Triage.

        Returns:
            One NormWithSource per input citation, in input order.
        """
        return [self._resolve_one(c) for c in citations]

    def _resolve_one(self, citation: str) -> NormWithSource:
        """Resolve a single citation by paragraph-level exact match."""
        parsed = self._parse_citation(citation)
        if parsed is None:
            return self._unresolved(citation)

        section, gesetz = parsed
        paragraph_key = f"§ {section} {gesetz}"

        exact = self._exact.get(paragraph_key)
        if exact is None:
            return self._unresolved(citation)

        return NormWithSource(
            canonical_citation=citation,
            paragraph_key=exact.canonical_key,
            source_text=exact.text,
            method="exact",
            confidence=None,
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
