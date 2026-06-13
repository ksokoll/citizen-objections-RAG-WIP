"""JSON Lines file-backed implementation of AuditEventPublisherProtocol."""

from __future__ import annotations

import os
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

    Durability and the chain head (ADR-030):
    - Durable append: each line is flushed and fsynced to stable storage before
      the in-memory head advances, so a crash can never leave the head asserting
      an event the disk lacks.
    - The in-memory head (last hash, last sequence) is the sole duplicate
      mechanism: publish() does not scan the file. The file is read only at
      open, to seed the head.

    Limitations (resolved later in this round / a later phase):
    - No file locking yet: concurrent publish() calls are not serialized (the
      next commit adds the single-writer advisory lock).
    - A damaged last line is not yet recovered (the recovery commit adds
      quarantine plus a recovery event).
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
        caller set on them, then durably appends the event and advances the
        head. The write is flushed and fsynced to disk before the head advances
        (ADR-030), so the head never claims an event the disk lacks; and the head
        advances only on success, so a failed append leaves the in-memory chain
        level with disk rather than ahead of it.

        The in-memory head is the sole duplicate mechanism (ADR-030): publish no
        longer scans the file, so a run of n events is no longer O(n^2). The file
        is read only at open, to seed the head. A re-published event_id is
        therefore not detected here; the pipeline keys each event with a fresh
        id, so this is a deliberate trade, not a gap (ADR-030).

        Args:
            event: The audit event to record. Its sequence_number and event_hash
                are assigned here; the caller supplies content, not chain fields.

        Raises:
            AuditLogError: If an I/O failure prevents the durable append. Raw
                OSErrors are wrapped so callers (Pipeline._emit, ADR-027) can
                route a store failure as the recoverable class without depending
                on stdlib exception types.
        """
        self._append_durably(event)

    def _append_durably(self, event: AuditEvent) -> None:
        """Chain, durably write, then advance the head: the append invariant.

        The ordering is the durability guarantee (ADR-030): write the line,
        flush the Python buffer, os.fsync the file descriptor so the bytes reach
        stable storage, and only then advance the in-memory head. The head is
        the claim that an event exists, so advancing it strictly after the fsync
        means the claim never precedes its evidence on disk. Any failure before
        the head-advance raises and leaves the head where it was, so a failed
        append never puts the in-memory chain ahead of the file.

        Args:
            event: The caller's event, before its chain fields are assigned.

        Raises:
            AuditLogError: If writing or fsyncing the line fails. The OSError is
                wrapped and chained so the boundary contract holds (ADR-027).
        """
        chained = self._chain(event)

        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(chained.model_dump_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
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
