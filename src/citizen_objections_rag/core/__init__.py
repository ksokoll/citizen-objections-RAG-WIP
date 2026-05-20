"""Core domain models and protocols."""

from .data_structures import (
    AbwaegungsStatus,
    Abwaegungsstellungnahme,
    CatalogMatch,
    EinwendungsTyp,
    Freigabe,
    IngestionResult,
    Rechtsgrundlage,
    RetrievalMetadata,
    RetrievedChunk,
    TriageResult,
    WuerdigungsStatus,
)
from .events import AuditEvent, AuditEventType
from .protocols import (
    AuditEventPublisherProtocol,
    EmbedderProtocol,
    KatalogMatcherProtocol,
    LLMClientProtocol,
    RetrieverProtocol,
)

__all__ = [
    # Data structures
    "AbwaegungsStatus",
    "Abwaegungsstellungnahme",
    "CatalogMatch",
    "EinwendungsTyp",
    "Freigabe",
    "IngestionResult",
    "RetrievalMetadata",
    "RetrievedChunk",
    "Rechtsgrundlage",
    "TriageResult",
    "WuerdigungsStatus",
    # Events
    "AuditEvent",
    "AuditEventType",
    # Protocols
    "AuditEventPublisherProtocol",
    "EmbedderProtocol",
    "KatalogMatcherProtocol",
    "LLMClientProtocol",
    "RetrieverProtocol",
]
