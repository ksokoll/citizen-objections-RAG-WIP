"""JSON Lines file-backed implementation of AuditEventPublisherProtocol."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.audit_log.serialization import GENESIS_PREV_HASH, compute_event_hash
from app.core.events import AuditEvent, AuditEventType
from app.core.failures import AuditLogError


class JsonLinesAuditStore:
    """Append-only, hash-chained audit event store backed by a JSON Lines file.

    One JSON object per line, opened in append mode. The OS-level append
    mode makes accidental overwrites impossible. Each line is a complete
    AuditEvent serialized via Pydantic's model_dump_json().

    The store owns the tamper-evident chain (ADR-024): on append it stamps the
    next sequence number and the SHA-256 hash (over the event's canonical bytes
    plus the predecessor's hash) onto the event, advancing an in-memory head.
    The hash is computed from the canonical serializer (serialization.py) that
    a later verify path will recompute with, so the two cannot diverge.

    Limitations (by design, resolved in later chain phases):
    - No file locking: concurrent publish() calls are not safe (18b).
    - No durable append: a crash mid-write produces a corrupt line, and the
      head advances only after a successful write but without fsync (18b).
    - The head is re-seeded from the file at open and the O(n) duplicate check
      re-reads the file on every publish(); the in-memory head becomes the sole
      mechanism, replacing both reads, in 18b.
    """

    def __init__(self, path: Path) -> None:
        """Initialize the store, ensure the backing file exists, seed the head.

        Args:
            path: Path to the JSON Lines file. Created (including parent
                directories) if it does not exist. Existing files are not
                truncated.
        """
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
        self._head_hash, self._head_sequence = self._initialize_head()

    def publish(self, event: AuditEvent) -> None:
        """Append an event to the audit log, stamping its chain position and hash.

        The store owns the chain fields: it assigns the next sequence number and
        the chained hash from its in-memory head, overwriting any values the
        caller set on them, then writes the event and advances the head. The
        head advances only after a successful write, so a failed append does not
        leave the in-memory chain ahead of disk.

        Args:
            event: The audit event to record. Its sequence_number and event_hash
                are assigned here; the caller supplies content, not chain fields.

        Raises:
            AuditLogError: If event_id already exists (duplicate prevention) or
                if an I/O failure prevents reading or appending. Raw OSErrors are
                wrapped so callers (Pipeline._emit, ADR-027) can route a store
                failure as the recoverable class without depending on stdlib
                exception types.
        """
        # TODO(18b): replace the O(n) duplicate check with the in-memory head
        try:
            existing = self._read_all()
        except OSError as exc:
            raise AuditLogError(
                f"failed to read the audit store at {self._path}"
            ) from exc
        for existing_event in existing:
            if existing_event.event_id == event.event_id:
                raise AuditLogError(f"Duplicate event_id: {event.event_id}")

        chained = self._chain(event)

        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(chained.model_dump_json() + "\n")
        except OSError as exc:
            raise AuditLogError(
                f"failed to append audit event {event.event_id}"
            ) from exc

        self._head_hash = chained.event_hash
        self._head_sequence = chained.sequence_number

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

    def _initialize_head(self) -> tuple[str, int | None]:
        """Seed the in-memory chain head from the events already on disk.

        The head is the predecessor the next appended event chains from: the
        last event's hash and its sequence number. An empty chain (no event yet
        carries a sequence number) seeds the genesis sentinel, so the next event
        becomes sequence 0.

        Reading the file at open is this round's seeding mechanism; 18b makes the
        in-memory head the sole record and adds durable append. Correctness, not
        cost, is the concern here.

        Returns:
            A (head_hash, head_sequence) pair: the hash the next event chains
            from (GENESIS_PREV_HASH when the chain is empty) and the last
            assigned sequence number (None when the chain is empty).
        """
        chained = [
            event for event in self._read_all() if event.sequence_number is not None
        ]
        if not chained:
            return GENESIS_PREV_HASH, None
        last = max(chained, key=lambda event: event.sequence_number or 0)
        head_hash = (
            last.event_hash if last.event_hash is not None else GENESIS_PREV_HASH
        )
        return head_hash, last.sequence_number

    def _chain(self, event: AuditEvent) -> AuditEvent:
        """Stamp the next sequence number and chained hash onto an event.

        The next sequence is one past the head, or 0 for the genesis event. The
        hash covers the event's canonical bytes (sequence number included) plus
        the head's hash, which is the genesis sentinel for the first event, so
        the event binds both its position and its predecessor.

        Args:
            event: The caller's event, before chain fields are assigned.

        Returns:
            A copy carrying the assigned sequence_number and event_hash. The
            input is frozen and left unchanged.
        """
        next_sequence = 0 if self._head_sequence is None else self._head_sequence + 1
        sequenced = event.model_copy(update={"sequence_number": next_sequence})
        event_hash = compute_event_hash(sequenced, self._head_hash)
        return sequenced.model_copy(update={"event_hash": event_hash})

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
