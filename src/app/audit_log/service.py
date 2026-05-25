"""Application-layer service for audit event publishing."""

from __future__ import annotations

from datetime import datetime

from app.core.events import AuditEvent, AuditEventType
from app.core.protocols import AuditEventPublisherProtocol


class AuditLogService:
    """Application-layer service implementing AuditEventPublisherProtocol.

    The Coordinator depends on this service, not on JsonLinesAuditStore
    directly. Current methods are 1:1 delegation. Future enrichment
    (metrics, structured logging, batch-write) happens here without
    touching the Coordinator's API or the store's infrastructure logic.
    """

    def __init__(self, store: AuditEventPublisherProtocol) -> None:
        """Initialize the service with a backing store.

        Args:
            store: Any implementation of AuditEventPublisherProtocol.
        """
        self._store = store

    def publish(self, event: AuditEvent) -> None:
        """Delegate publish to the backing store.

        Args:
            event: The audit event to record.
        """
        self._store.publish(event)

    def query(
        self,
        einwendungs_id: str | None = None,
        event_type: AuditEventType | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
    ) -> list[AuditEvent]:
        """Delegate query to the backing store.

        Args:
            einwendungs_id: Filter by objection ID.
            event_type: Filter by event type.
            after: Return events with timestamp >= this value.
            before: Return events with timestamp <= this value.

        Returns:
            List of matching events, or empty list if no match.
        """
        return self._store.query(
            einwendungs_id=einwendungs_id,
            event_type=event_type,
            after=after,
            before=before,
        )
