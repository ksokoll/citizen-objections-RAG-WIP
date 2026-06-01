"""JSON Lines file-backed implementation of AuditEventPublisherProtocol."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.core.events import AuditEvent, AuditEventType
from app.core.failures import AuditLogError


class JsonLinesAuditStore:
    """Append-only audit event store backed by a JSON Lines file.

    One JSON object per line, opened in append mode. The OS-level append
    mode makes accidental overwrites impossible. Each line is a complete
    AuditEvent serialized via Pydantic's model_dump_json().

    Limitations (by design, post-skeleton):
    - No file locking: concurrent publish() calls are not safe
    - No atomic write: a crash mid-write produces a corrupt line
    - No concurrent-publish protection
    - O(n) duplicate check on every publish(): acceptable for skeleton,
      replace with in-memory set post-skeleton
    """

    def __init__(self, path: Path) -> None:
        """Initialize the store and ensure the backing file exists.

        Args:
            path: Path to the JSON Lines file. Created (including parent
                directories) if it does not exist. Existing files are not
                truncated.
        """
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()

    def publish(self, event: AuditEvent) -> None:
        """Append an event to the audit log.

        Args:
            event: The audit event to record.

        Raises:
            AuditLogError: If event_id already exists (duplicate prevention).
        """
        # TODO(post-skeleton): replace O(n) duplicate check with in-memory set
        existing = self._read_all()
        for existing_event in existing:
            if existing_event.event_id == event.event_id:
                raise AuditLogError(f"Duplicate event_id: {event.event_id}")

        with self._path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")

    def query(
        self,
        einwendungs_id: str | None = None,
        event_type: AuditEventType | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
    ) -> list[AuditEvent]:
        """Query audit events with optional filters (AND semantics).

        Args:
            einwendungs_id: Filter by objection ID.
            event_type: Filter by event type.
            after: Return events with timestamp >= this value.
            before: Return events with timestamp <= this value.

        Returns:
            List of matching events, or empty list if no match or file missing.
        """
        if not self._path.exists():
            return []

        results = []
        for event in self._read_all():
            if einwendungs_id is not None and event.einwendungs_id != einwendungs_id:
                continue
            if event_type is not None and event.event_type != event_type:
                continue
            if after is not None and event.timestamp < after:
                continue
            if before is not None and event.timestamp > before:
                continue
            results.append(event)
        return results

    def _read_all(self) -> list[AuditEvent]:
        if not self._path.exists():
            return []
        events = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(AuditEvent.model_validate_json(line))
        return events
