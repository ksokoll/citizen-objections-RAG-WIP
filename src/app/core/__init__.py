"""Core domain models and protocols."""

from .entities import (
    Einwendung,
    ExtrahiertesArgument,
)
from .events import AuditEvent, AuditEventType
from .failures import (
    AuditLogError,
    IngestionError,
    RetrievalError,
    TriageError,
)

# from .prompts import PromptTemplate
from .protocols import (
    AuditEventPublisherProtocol,
    LLMClientProtocol,
    Retriever,
)
from .results import IngestionResult, TriageResult
from .statuses import AbwaegungsStatus, EinwendungsTyp, WuerdigungsStatus

__all__ = [
    # Statuses
    "AbwaegungsStatus",
    "EinwendungsTyp",
    "WuerdigungsStatus",
    # Entities
    "Einwendung",
    "ExtrahiertesArgument",
    # Results
    "IngestionResult",
    "TriageResult",
    # Failures
    "AuditLogError",
    "IngestionError",
    "RetrievalError",
    "TriageError",
    # Events
    "AuditEvent",
    "AuditEventType",
    # Protocols
    "AuditEventPublisherProtocol",
    "LLMClientProtocol",
    "Retriever",
]
