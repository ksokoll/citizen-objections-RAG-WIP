"""Application-layer service for audit event publishing."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.audit_log.protocols import AuditEventPublisherProtocol
from app.core.events import (
    SYSTEM_EINWENDUNGS_ID,
    AuditEvent,
    AuditEventType,
)
from app.observability.tracing import traced


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

    @traced(stage="audit_log")
    def publish(self, event: AuditEvent) -> None:
        """Delegate publish to the backing store.

        Traced per invocation: a run emits one audit_log timing event and,
        when tracing is enabled, one span per published custody event.

        Args:
            event: The audit event to record.
        """
        self._store.publish(event)

    def record_startup_config(self, provenance: dict[str, object]) -> None:
        """Construct and publish the STARTKONFIGURATION custody event (ADR-031).

        The audit context owns the custody event's shape: the STARTKONFIGURATION
        event type, the process-wide SYSTEM sentinel id, and a fresh event id.
        The CLI supplies only the content-free provenance values (git sha,
        package versions, allowlist size, and so on); the audit-schema knowledge
        stays here, not in the wiring layer (A3). The store assigns the chain
        fields and enforces the payload schema at write entry, so this is a plain
        publish like any other custody event, not a second writer reaching past
        the service.

        Args:
            provenance: The content-free provenance of the active controls, as
                assembled by the composition root. Copied into the event payload
                so the caller's dict is not retained.
        """
        self._store.publish(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type=AuditEventType.STARTKONFIGURATION,
                einwendungs_id=SYSTEM_EINWENDUNGS_ID,
                payload=dict(provenance),
            )
        )

    def record_raw_document_read(self, document_id: str) -> None:
        """Construct and publish the ROHDOKUMENT_ZUGRIFF custody event (ADR-033).

        The audit context owns the read-access event's shape: the
        ROHDOKUMENT_ZUGRIFF type, the einwendungs_id, and a fresh event id. The
        show-document path supplies only the document_id being read; the
        audit-schema knowledge stays here, like record_startup_config, not in the
        CLI. The store assigns the chain fields and enforces the content-free
        payload schema at write entry, so this is a plain publish, and it
        propagates AuditLogError on a write failure so the caller can abort the
        read fail-closed.

        The einwendungs_id is the document_id, not the SYSTEM sentinel: a
        raw-document read is access to one specific objection's PII, so the event
        is tied to that objection (its natural correlation), unlike the
        process-wide startup and recovery events (ADR-033, core/events.py). The
        payload carries the document_id only; the "when" is the event's top-level
        timestamp, already in the canonical hash, never content.

        Args:
            document_id: The id of the raw document being read. Used both as the
                event's correlation id and as the single content-free payload key.
        """
        self._store.publish(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type=AuditEventType.ROHDOKUMENT_ZUGRIFF,
                einwendungs_id=document_id,
                payload={"document_id": document_id},
            )
        )

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
