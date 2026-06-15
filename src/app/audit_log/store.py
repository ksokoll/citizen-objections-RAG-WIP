"""JSON Lines file-backed implementation of AuditEventPublisherProtocol."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import structlog
from filelock import FileLock, Timeout

from app.audit_log.events import AUDIT_RECOVERED
from app.audit_log.serialization import GENESIS_PREV_HASH, compute_event_hash
from app.core.events import AuditEvent, AuditEventType
from app.core.failures import AuditLogError

#: Module logger for the store's recovery event. Routes through the same
#: governed chain as every other event (ADR-026).
_log = structlog.get_logger()

#: einwendungs_id carried by the system recovery event, which is not tied to a
#: citizen objection. A fixed non-objection sentinel so the recovery custody
#: record satisfies the required non-empty id without claiming an Einwendung
#: (ADR-030).
RECOVERY_EINWENDUNGS_ID: Final[str] = "SYSTEM"

#: How many trailing events are verified at store open: the last K, not the
#: whole file, so startup stays fast as the trail grows (ADR-031). The full walk
#: is the auditor's CLI command (verify_chain_file); the window only diagnoses a
#: break near the tail, where a crash-or-tamper is most likely to have landed.
OPEN_VERIFY_TAIL_WINDOW: Final[int] = 256


@dataclass(frozen=True)
class ChainBreak:
    """The first point at which verification found the chain inconsistent.

    Reported instead of a bare False so an auditor learns where and why the
    chain broke, not merely that it did (ADR-031). index is the 0-based position
    in the walked sequence (a line index for an on-disk walk); expected and
    found are stringified so a sequence break and a hash break report uniformly.
    """

    index: int
    sequence_number: int | None
    reason: str
    expected: str
    found: str

    def describe(self) -> str:
        """One-line, content-free description of the break for a report."""
        return (
            f"chain break at line index {self.index} "
            f"(sequence_number {self.sequence_number}): {self.reason}; "
            f"expected {self.expected!r}, found {self.found!r}"
        )


@dataclass(frozen=True)
class VerificationResult:
    """The outcome of a chain verification: ok, or the first break with detail.

    An empty chain is vacuously ok. first_break is None exactly when ok is True.
    """

    ok: bool
    first_break: ChainBreak | None = None


def _verify_one(
    event: AuditEvent,
    index: int,
    prev_hash: str,
    expected_sequence: int,
    is_genesis: bool,
) -> ChainBreak | None:
    """Verify one event against its expected position and predecessor.

    Checks, in order: the monotonic sequence number, that a chain hash is
    present, and that the recorded hash equals the recomputation over the
    event's canonical bytes plus prev_hash. Returns the break or None.

    Args:
        event: The event to check.
        index: Its 0-based position in the walked chain.
        prev_hash: The predecessor's hash (GENESIS_PREV_HASH for genesis).
        expected_sequence: The sequence number this position must carry.
        is_genesis: Whether this is the genesis position of a full walk, which
            only changes the reason text so a wrong genesis anchor reads as such.
    """
    if event.sequence_number != expected_sequence:
        return ChainBreak(
            index=index,
            sequence_number=event.sequence_number,
            reason="sequence number is not monotonic",
            expected=str(expected_sequence),
            found=str(event.sequence_number),
        )
    if event.event_hash is None:
        return ChainBreak(
            index=index,
            sequence_number=event.sequence_number,
            reason="event carries no chain hash",
            expected="a SHA-256 hex digest",
            found="None",
        )
    recomputed = compute_event_hash(event, prev_hash)
    if event.event_hash != recomputed:
        reason = (
            "genesis event does not chain from the all-zero sentinel"
            if is_genesis
            else "event hash does not match the recomputed hash"
        )
        return ChainBreak(
            index=index,
            sequence_number=event.sequence_number,
            reason=reason,
            expected=recomputed,
            found=event.event_hash,
        )
    return None


def verify_chain(
    events: list[AuditEvent], *, tail: int | None = None
) -> VerificationResult:
    """Walk the hash chain and report the first break, or ok (ADR-031).

    Recomputes each event's hash with the SAME canonical serializer the write
    path used (compute_event_hash, which dispatches canonical_bytes per
    serialization_version, serialization.py). A chain written under
    canonical_bytes verifies only against that same byte form, so a second,
    divergent serialization introduced into the verify path would make a
    freshly written chain fail here: the proof has exactly one definition.

    Per event it checks the monotonic sequence number, the chained hash (content
    plus predecessor hash), and, on a full walk, the genesis sentinel (the first
    event is sequence 0 and chains from GENESIS_PREV_HASH).

    Args:
        events: The chain in append order, as read from the store.
        tail: If given, verify only the last `tail` events, seeding the
            predecessor hash and the expected sequence from the event just
            before the window. This is the fast startup check; by construction
            it cannot see a break before the window or the genesis sentinel,
            which is the full walk's job. None (default) walks from genesis.

    Returns:
        VerificationResult(ok=True) for an intact (or empty) chain, or
        VerificationResult(ok=False, first_break=...) at the first break.
    """
    if not events:
        return VerificationResult(ok=True)

    if tail is None or tail >= len(events):
        start_index = 0
        prev_hash = GENESIS_PREV_HASH
        expected_sequence = 0
        verifies_genesis = True
    else:
        start_index = len(events) - tail
        predecessor = events[start_index - 1]
        prev_hash = predecessor.event_hash or GENESIS_PREV_HASH
        expected_sequence = (predecessor.sequence_number or -1) + 1
        verifies_genesis = False

    for index in range(start_index, len(events)):
        event = events[index]
        first_break = _verify_one(
            event,
            index,
            prev_hash,
            expected_sequence,
            is_genesis=verifies_genesis and index == 0,
        )
        if first_break is not None:
            return VerificationResult(ok=False, first_break=first_break)
        prev_hash = event.event_hash  # type: ignore[assignment]
        expected_sequence += 1
    return VerificationResult(ok=True)


def verify_chain_file(path: Path) -> VerificationResult:
    """Read the on-disk chain and verify it fully, reporting the first break.

    Non-mutating by design: it reads and parses the file directly rather than
    opening the store, so an auditor's verification never triggers recovery or
    appends a recovery event (the auditor reads the chain as it stands). A line
    that does not parse is itself reported as a break rather than crashing the
    command, so a corrupt line is diagnosed like any other.

    Args:
        path: Path to the JSON Lines audit file. A missing or empty file is a
            vacuously intact chain (nothing has been written to break).

    Returns:
        The full-walk VerificationResult for the chain on disk.
    """
    if not path.exists():
        return VerificationResult(ok=True)
    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    events: list[AuditEvent] = []
    for index, line in enumerate(lines):
        try:
            events.append(AuditEvent.model_validate_json(line))
        except ValueError:
            return VerificationResult(
                ok=False,
                first_break=ChainBreak(
                    index=index,
                    sequence_number=None,
                    reason="line is not a valid serialized AuditEvent",
                    expected="parseable JSON",
                    found="unparseable line",
                ),
            )
    return verify_chain(events)


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

    Durability, locking, and the chain head (ADR-030):
    - Durable append: each line is flushed and fsynced to stable storage before
      the in-memory head advances, so a crash can never leave the head asserting
      an event the disk lacks.
    - Single-writer advisory lock: an advisory filelock covers open+recovery and
      each append, so two starts cannot recover the same file concurrently and
      two writers cannot interleave an append. It guards against accidental
      concurrency (a retry thread, a cron replay), not a deliberate second
      writer (ADR-027 threat model). The single-process assumption (15.2) holds:
      no second process holds the file on the Windows host.
    - The in-memory head (last hash, last sequence) is the sole duplicate
      mechanism: publish() does not scan the file. The file is read only at
      open, to seed the head, by the recovery path below (not a second reader).
    - Recovery at open: a damaged or partial last line (invalid JSON at EOF, or
      a line whose hash does not chain) is moved to a quarantine file, its bytes'
      hash is recorded, and a recovery event is written into the chain. Only the
      last line is healed: a mid-file break is not silently fixed here, it is for
      verify_chain (18c) to surface, because a partial last line is directly
      observable while lost interior events are not without an external anchor
      (ADR-030).
    """

    def __init__(
        self,
        path: Path,
        lock_timeout: float = 10.0,
        tail_window: int = OPEN_VERIFY_TAIL_WINDOW,
    ) -> None:
        """Initialize the store, ensure the backing file exists, seed the head.

        Seeding reads the file under the single-writer lock, so open+recovery is
        one critical section (ADR-030). After seeding, the last `tail_window`
        events are verified for startup speed: a tampered or non-chaining line
        near the tail is surfaced loudly at open, while the full walk is the
        auditor's CLI command (ADR-031).

        Args:
            path: Path to the JSON Lines file. Created (including parent
                directories) if it does not exist. Existing files are not
                truncated.
            lock_timeout: Seconds to wait for the single-writer lock before
                failing loudly. A finite default surfaces accidental contention
                as an AuditLogError rather than hanging; tests pass a small value.
            tail_window: How many trailing events to verify at open. The default
                bounds startup cost as the trail grows; tests pass a small value
                to exercise the window boundary.

        Raises:
            AuditLogError: If another writer holds the lock past lock_timeout
                (open cannot recover concurrently with another start), or if the
                tail-window verification finds a break (ADR-031).
        """
        self._path = path
        self._lock_timeout = lock_timeout
        self._tail_window = tail_window
        self._lock = FileLock(f"{path}.lock", timeout=lock_timeout)
        self._head_hash: str = GENESIS_PREV_HASH
        self._head_sequence: int | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
        with self._single_writer():
            self._recover_and_seed()

    @contextmanager
    def _single_writer(self) -> Iterator[None]:
        """Hold the advisory single-writer lock for one critical section.

        The lock covers open+recovery and each append (ADR-030), so two starts
        cannot recover the same file concurrently and two writers cannot
        interleave an append. It is re-entered per critical section rather than
        held for the store's lifetime, because two store instances on one path
        are a supported pattern (reopen to continue the chain); a lifetime hold
        would deadlock the second instance. The residual (a writer slipping
        between two of this store's locked sections leaves the in-memory head
        stale) is the accidental-concurrency limit the lock accepts, not an
        interleaved-append race: the lock guards accident, not a deliberate
        second writer (ADR-027 threat model, ADR-030).

        Raises:
            AuditLogError: If the lock cannot be acquired within lock_timeout,
                translating filelock.Timeout so a contended store fails loudly on
                the documented failure type rather than hanging or leaking a
                third-party exception.
        """
        try:
            with self._lock:
                yield
        except Timeout as exc:
            raise AuditLogError(
                f"could not acquire the audit store lock at {self._lock.lock_file}: "
                "another writer holds it"
            ) from exc

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
                on stdlib exception types, or if another writer holds the
                single-writer lock past the timeout.
        """
        with self._single_writer():
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

    def _recover_and_seed(self) -> None:
        """Seed the in-memory head from disk, recovering a damaged tail (ADR-030).

        This is the single open-time read path: it both seeds the head and
        recovers, so seeding is never a second reader divergent from recovery.
        It reads the chain to its last valid line and seeds the head from it. If
        the last line is damaged (invalid JSON at EOF, or a hash that does not
        chain from its predecessor), the damaged line is quarantined, a recovery
        event is appended to the chain, and the recovery is logged. Only the last
        line is healed: an interior break is left for verify_chain (18c) to
        surface, because a partial last line is directly observable while a lost
        interior event is not without an external anchor.

        Called under the single-writer lock from __init__, so open, recovery, and
        the recovery event's append are one critical section. After seeding (and
        any tail recovery), the kept events' tail window is verified so a
        tampered or non-chaining interior line near the end is surfaced at open
        with detail, not left for the next CLI run (ADR-031).
        """
        valid_events, valid_lines, damaged_line = self._read_chain_with_tail_check()
        self._seed_head_from(valid_events)
        if damaged_line is not None:
            self._quarantine_and_record(damaged_line, valid_lines)
        self._verify_tail_window(valid_events)

    def _verify_tail_window(self, events: list[AuditEvent]) -> None:
        """Verify the last tail_window events, raising loudly on a break.

        The fast startup check (ADR-031): recovery heals only a damaged last
        line, so a parseable-but-non-chaining interior line (a naive in-place
        edit) would otherwise pass open silently and surface only at the next
        full verify. Verifying the tail window catches such a break near the end
        and raises it with location, so a damaged tail is diagnosed at open, not
        merely the next audit. A break before the window is the full walk's job
        (verify_chain_file), deliberately not done here for startup speed.

        Args:
            events: The kept (valid) events, the damaged tail already excluded.

        Raises:
            AuditLogError: If the tail window does not verify, carrying the
                first break's content-free description.
        """
        result = verify_chain(events, tail=self._tail_window)
        if not result.ok and result.first_break is not None:
            raise AuditLogError(
                f"audit store {self._path} failed tail-window verification at "
                f"open: {result.first_break.describe()}"
            )

    def _read_chain_with_tail_check(
        self,
    ) -> tuple[list[AuditEvent], list[str], str | None]:
        """Read the on-disk chain, isolating a damaged last line if present.

        Returns the valid events, their source lines (so the live file can be
        rewritten byte-for-byte without the damaged tail), and the damaged last
        line or None. The damage cases handled here are the EOF cases: a last
        line that fails to parse, or one that parses but whose hash does not
        chain from its predecessor. An interior line that fails to parse is a
        mid-file break, which recovery does not heal: it raises so the corruption
        surfaces loudly rather than being silently truncated (ADR-030).

        Returns:
            (valid_events, valid_lines, damaged_line). damaged_line is None when
            the chain is intact to its end.

        Raises:
            AuditLogError: If an interior (non-last) line fails to parse.
        """
        if not self._path.exists():
            return [], [], None
        lines = [
            line
            for line in self._path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not lines:
            return [], [], None

        events: list[AuditEvent] = []
        for index, line in enumerate(lines):
            try:
                events.append(AuditEvent.model_validate_json(line))
            except ValueError as exc:
                if index == len(lines) - 1:
                    # Truncated or partial last line: the EOF case recovery heals.
                    return events, lines[:index], line
                raise AuditLogError(
                    f"audit store {self._path} has a damaged interior line at "
                    f"position {index}; recovery heals only a damaged last line, "
                    "an interior break is for verify_chain to surface (18c)"
                ) from exc

        if self._last_event_chains(events):
            return events, lines, None
        # The last line parsed but its hash does not chain: quarantine it too.
        return events[:-1], lines[:-1], lines[-1]

    def _last_event_chains(self, events: list[AuditEvent]) -> bool:
        """Whether the last event's hash chains from its predecessor.

        A healthy last event always passes, because the store wrote its hash
        over the same canonical bytes the read reproduces (ADR-029 roundtrip
        stability), so this is a false-positive-free damaged-tail check. A
        pre-chain tail (no sequence_number or event_hash) is treated as intact:
        there is no hash to validate and it is not a partial write.

        Args:
            events: The parsed events, last element checked against its
                predecessor (the genesis sentinel when it is the only event).
        """
        last = events[-1]
        if last.sequence_number is None or last.event_hash is None:
            return True
        prev_hash = GENESIS_PREV_HASH
        if len(events) >= 2 and events[-2].event_hash is not None:
            prev_hash = events[-2].event_hash
        return last.event_hash == compute_event_hash(last, prev_hash)

    def _seed_head_from(self, events: list[AuditEvent]) -> None:
        """Seed the head from the last sequenced event (genesis if none).

        The head is the predecessor the next appended event chains from: the
        last event's hash and sequence number. An empty or pre-chain-only file
        seeds the genesis sentinel, so the next event becomes sequence 0.

        Args:
            events: The valid events to seed from (the damaged tail excluded).
        """
        chained = [event for event in events if event.sequence_number is not None]
        if not chained:
            self._head_hash = GENESIS_PREV_HASH
            self._head_sequence = None
            return
        last = max(chained, key=lambda event: event.sequence_number or 0)
        self._head_hash = (
            last.event_hash if last.event_hash is not None else GENESIS_PREV_HASH
        )
        self._head_sequence = last.sequence_number

    def _quarantine_and_record(self, damaged_line: str, valid_lines: list[str]) -> None:
        """Quarantine the damaged tail, heal the file, record a recovery event.

        The damaged line's bytes are written to a timestamped quarantine file,
        the live file is rewritten durably without the damaged tail, a recovery
        event carrying the quarantined bytes' hash and a line count (never the
        raw content) is appended to the chain, and the recovery is logged. The
        quarantine-not-truncate choice keeps the damaged bytes for later
        inspection rather than discarding them silently (ADR-030).

        Args:
            damaged_line: The damaged last line, removed from the live file.
            valid_lines: The source lines of the valid events, rewritten to the
                live file byte-for-byte.
        """
        quarantined_hash = hashlib.sha256(damaged_line.encode("utf-8")).hexdigest()
        self._quarantine_path().write_text(damaged_line + "\n", encoding="utf-8")
        self._rewrite_valid_prefix(valid_lines)
        self._append_durably(self._recovery_event(quarantined_hash))
        _log.warning(
            AUDIT_RECOVERED,
            quarantined_hash=quarantined_hash,
            quarantined_lines=1,
        )

    def _quarantine_path(self) -> Path:
        """Return a timestamped sibling quarantine path for the damaged bytes."""
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return self._path.with_name(f"{self._path.name}.corrupt.{timestamp}")

    def _rewrite_valid_prefix(self, valid_lines: list[str]) -> None:
        """Durably rewrite the live file with only the valid lines.

        Recovery is the one operation that rewrites the file rather than
        appending; it drops the damaged tail and fsyncs, so the heal is itself
        durable before the recovery event is appended (ADR-030).

        Args:
            valid_lines: The lines to keep, in order, written byte-for-byte.

        Raises:
            AuditLogError: If rewriting the healed file fails.
        """
        try:
            with self._path.open("w", encoding="utf-8") as f:
                for line in valid_lines:
                    f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            raise AuditLogError(
                f"failed to rewrite the audit store at {self._path} during recovery"
            ) from exc

    def _recovery_event(self, quarantined_hash: str) -> AuditEvent:
        """Build the recovery custody event for the chain.

        Carries the quarantined bytes' hash and a line count in its payload, so
        what was removed is attributable without the raw content ever entering
        the chain. It is a WIEDERHERSTELLUNG event with the system sentinel id,
        not an objection event (ADR-030).

        Args:
            quarantined_hash: SHA-256 hex of the quarantined bytes.
        """
        return AuditEvent(
            event_id=str(uuid.uuid4()),
            event_type=AuditEventType.WIEDERHERSTELLUNG,
            einwendungs_id=RECOVERY_EINWENDUNGS_ID,
            payload={"quarantined_hash": quarantined_hash, "quarantined_lines": 1},
        )

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
