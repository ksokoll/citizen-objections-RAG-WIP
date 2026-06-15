"""Core domain models and protocols."""

from .entities import (
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
from .results import (
    IngestionResult,
    TriageResult,
)

# Single-context protocols no longer live here: LLMClientProtocol moved to
# triage/protocols.py and AuditEventPublisherProtocol to audit_log/protocols.py
# (H1, Round 20). Each had exactly one consuming context, so it belongs with
# that context, not the shared kernel. core now exports only cross-context
# contracts: the payload entities, the boundary-crossing result DTOs, the
# shared failures, and the audit event vocabulary.
__all__ = [
    # Entities
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
]
