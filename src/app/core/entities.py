"""Domain entities (frozen dataclasses) for the objection workflow."""

from __future__ import annotations

from dataclasses import dataclass, field

from .statuses import EinwendungsTyp


@dataclass(frozen=True)
class ExtrahiertesArgument:
    """A single discrete legal argument extracted from an Einwendung.

    All fields are set by the Triage context. argument_verified reflects
    ADR-006 Layer 1: whether original_zitat is a substring of the masked
    source document. zitierte_normen carries the canonical norm citations
    that the Retrieval context later resolves to source Gesetzestext.
    """

    argument_id: str
    argument_text: str  # normalized for vector search
    original_zitat: str  # verbatim quote for ADR-006 Layer 1 check
    catalog_id: str | None  # predefined domain enum; None = NoMatchEvent
    einwendungs_typ: EinwendungsTyp
    zitierte_normen: list[str] = field(default_factory=list)
    argument_verified: bool = False


@dataclass(frozen=True)
class RetrievedChunk:
    """A single document chunk returned by the RAG retriever.

    paragraph_id is the canonical form used for §-reference verification
    (e.g. baugb_§3_abs1). Score is the relevance score from the retriever,
    normalised to [0, 1] via L2-normalised inner product (ADR-003).
    """

    chunk_id: str
    paragraph_id: str
    gesetz: str
    text: str
    score: float


@dataclass(frozen=True)
class Einwendung:
    """Raw citizen objection as received at the system boundary.

    Carries the original input text and document identity. Referenced
    by einwendungs_id throughout the pipeline. Immutable after ingestion.
    PII is present here; downstream contexts receive only masked text.
    """

    einwendungs_id: str
    document_id: str
    raw_text: str
