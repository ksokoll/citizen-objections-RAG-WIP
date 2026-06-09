"""Core domain models and protocols."""

from .entities import (
    Einwendung,
    EinwendungsTyp,
    ExtrahiertesArgument,
)
from .events import AuditEvent, AuditEventType
from .failures import (
    AuditLogError,
    IngestionError,
    RetrievalError,
    TriageError,
)
from .protocols import (
    AuditEventPublisherProtocol,
    LLMClientProtocol,
    Retriever,
)
from .results import (
    IngestionResult,
    TriageResult,
)

__all__ = [
    # Entities
    "Einwendung",
    "EinwendungsTyp",
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
