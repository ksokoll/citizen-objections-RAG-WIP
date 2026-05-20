"""AuditLog bounded context.

Append-only event persistence for all pipeline decisions and
Sachbearbeiter actions. Implements AuditEventPublisherProtocol.
"""

from citizen_objections_rag.audit_log.service import AuditLogService

__all__ = ["AuditLogService"]
