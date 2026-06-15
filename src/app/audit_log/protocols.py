# audit_log.protocols.py - Protocols for the AuditLog bounded context.
"""Protocols for the AuditLog bounded context.

Holds the append-only publisher interface the AuditLogService implements and
the JsonLinesAuditStore satisfies. The protocol lives with its only consumer,
the audit context, rather than in the shared kernel: the pipeline injects the
concrete AuditLogService, not this protocol, so no other context depends on it
(H1, Round 20). This mirrors the Retriever move of Round 17.1; the two
remaining single-context protocols are now retro-fitted to that precedent so
the repo carries one rule, not two.

It references AuditEvent and AuditEventType from the shared kernel, the
cross-context payload the audit chain persists; that dependency points into
core, the allowed direction, not into another bounded context.
"""

from datetime import datetime
from typing import Protocol

from app.core.events import AuditEvent, AuditEventType


class AuditEventPublisherProtocol(Protocol):
    """Append-only audit event store.

    All state changes must be audited via `publish`, which appends; no event is
    ever rewritten or deleted. `query` is for retrieving historical events; it
    returns empty list on no match, never raises.

    Duplicate detection is deliberately not part of this contract. The durable
    store keys the chain off an in-memory head and does not scan the file per
    append (ADR-030), so a re-published event_id is not rejected here; the
    pipeline mints a fresh id per event, making that a deliberate trade, not a
    gap. Append-only and failure-translation are the guarantees this protocol
    makes.

    Failure contract: implementations translate every I/O failure on the
    publish path into AuditLogError. No raw OSError may escape publish, so
    callers (Pipeline._emit, ADR-027) can route the recoverable store-failure
    class on exactly one exception type without depending on stdlib exception
    types. A contract test against each store implementation proves the
    translation.
    """

    def publish(self, event: AuditEvent) -> None:
        """Append an event to the audit log.

        Args:
            event: The audit event to record.

        Raises:
            AuditLogError: If an I/O failure prevents the append. Raw OSErrors
                are translated, never propagated.
        """
        ...

    def query(
        self,
        einwendungs_id: str | None = None,
        event_type: AuditEventType | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
    ) -> list[AuditEvent]:
        """Query historical audit events.

        Args:
            einwendungs_id: Filter by objection ID.
            event_type: Filter by event type.
            after: Return events with timestamp >= this value.
            before: Return events with timestamp <= this value.

        Returns:
            List of matching events, or empty list if no match.
        """
        ...
