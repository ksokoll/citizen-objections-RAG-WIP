"""Core domain models and protocols."""

from .entities import (
    Abwaegungsstellungnahme,
    Einwendung,
    ExtrahiertesArgument,
    Freigabe,
    Rechtsgrundlage,
    RetrievalMetadata,
    RetrievedChunk,
)
from .events import AuditEvent, AuditEventType
from .failures import (
    AuditLogError,
    GenerationError,
    IngestionError,
    RetrievalError,
    TriageError,
)

# from .prompts import PromptTemplate
from .protocols import (
    AuditEventPublisherProtocol,
    EmbedderProtocol,
    LLMClientProtocol,
    RetrieverProtocol,
)
from .results import IngestionResult, TriageResult
from .statuses import AbwaegungsStatus, EinwendungsTyp, WuerdigungsStatus

__all__ = [
    # Statuses
    "AbwaegungsStatus",
    "EinwendungsTyp",
    "WuerdigungsStatus",
    # Entities
    "Abwaegungsstellungnahme",
    "Einwendung",
    "ExtrahiertesArgument",
    "Freigabe",
    "Rechtsgrundlage",
    "RetrievalMetadata",
    "RetrievedChunk",
    # Results
    "IngestionResult",
    "TriageResult",
    # Failures
    "AuditLogError",
    "GenerationError",
    "IngestionError",
    "RetrievalError",
    "TriageError",
    # Events
    "AuditEvent",
    "AuditEventType",
    # Protocols
    "AuditEventPublisherProtocol",
    "EmbedderProtocol",
    "LLMClientProtocol",
    "RetrieverProtocol",
]
