"""Domain entities and protocols for the Retrieval bounded context.

Holds the value objects and the abstract interfaces. Pure domain: no
I/O, no external dependencies beyond the standard library and typing,
no imports from infrastructure or application layers. Concrete
implementations of the protocols live in the infrastructure layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GesetzParagraph:
    """A single paragraph (§) of a German statute with its full text.

    Represents one indexable unit of legal text. Produced by the XML
    loader in the infrastructure layer and consumed by the resolver in
    the application layer. The canonical_key is the join point against
    the canonical citation strings produced by the Triage norm_extractor.

    Attributes:
        gesetz: Official abbreviation of the statute (e.g. "BauGB").
            Derived from the amtabk element, or the jurabk element when
            no amtabk is present.
        paragraph: The paragraph designation as it appears in the
            source (e.g. "§ 9", "§ 9a", "§ 135a"). Whitespace is
            normalised to a single space between the section sign and
            the number.
        canonical_key: Normalised lookup key combining paragraph and
            gesetz (e.g. "§ 9 BauGB"). This is the exact-match key used
            by the resolver against the citation's paragraph-level form.
        title: The paragraph heading (e.g. "Inhalt des Bebauungsplans"),
            empty string if none could be extracted. Some statutes (for
            example VwGO) carry no paragraph headings in the source.
        text: The full plain text of the paragraph, with all Absätze,
            Sätze, and Nummern flattened into a single string. Empty
            string is never stored; paragraphs with no content (for
            example "(weggefallen)") are filtered out by the loader.
    """

    gesetz: str
    paragraph: str
    canonical_key: str
    title: str
    text: str


@dataclass(frozen=True)
class NormWithSource:
    """A canonical citation paired with its resolved source text.

    The output unit of the Retrieval context, one per input citation.
    Carries the resolution method and confidence so downstream contexts
    can reason about how the text was obtained.

    Attributes:
        canonical_citation: The citation as it came from Triage
            (e.g. "§ 9 Abs. 1 Nr. 1 WHG").
        paragraph_key: The paragraph-level key the citation resolved to
            (e.g. "§ 9 WHG"). Empty string when unresolved.
        source_text: The full Gesetzestext of the resolved paragraph.
            Empty string when unresolved.
        method: How the resolution was obtained: "exact", "vector", or
            "none" when unresolved.
        confidence: Cosine similarity score for a vector resolution,
            None for an exact match or an unresolved citation.
        resolved: True when a source text was found, False otherwise.
    """

    canonical_citation: str
    paragraph_key: str
    source_text: str
    method: str
    confidence: float | None
    resolved: bool


class Embedder(Protocol):
    """Abstract interface for turning text into dense vectors.

    Concrete implementations (for example a sentence-transformers wrapper)
    live in the infrastructure layer. The asymmetric query/passage
    distinction is part of the contract because retrieval-tuned models
    such as the e5 family require different handling for indexed passages
    versus search queries.
    """

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """Embed texts that will be stored in the index (the corpus side)."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single text that will be used as a search query."""
        ...


class Retriever(Protocol):
    """Abstract interface for resolving citations to source text.

    The application-layer NormRetrievalService implements this. Triage
    and ResponseDrafting depend on this Protocol, not on the concrete
    service, so their unit tests can substitute a fake.
    """

    def resolve(self, citations: list[str]) -> list[NormWithSource]:
        """Resolve canonical norm citations to their source Gesetzestext."""
        ...
