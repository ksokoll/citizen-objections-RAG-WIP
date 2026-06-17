"""JSON Lines file-backed implementation of AuditEventPublisherProtocol."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Final

from app.audit_log.anchor import ChainHead
from app.audit_log.payload_schema import validate_payload
from app.audit_log.serialization import GENESIS_PREV_HASH, compute_event_hash
from app.audit_log.verification import ChainBreak, VerificationResult, verify_chain
from app.core.events import AuditEvent, AuditEventType
from app.core.failures import AuditLogError

#: How many trailing events are verified at store open: the last K, not the
#: whole file, so startup stays fast as the trail grows (ADR-031). The full walk
#: is the auditor's CLI command (verify_chain_file); the window only diagnoses a
#: break near the tail, where a crash-or-tamper is most likely to have landed.
OPEN_VERIFY_TAIL_WINDOW: Final[int] = 256


def verify_chain_file(path: Path) -> VerificationResult:
    """Read the on-disk chain and verify it fully, reporting the first break.

    Non-mutating by design: it reads and parses the file directly rather than
    opening the store, so an auditor's verification never seeds a head or raises
    an open-time failure (the auditor reads the chain as it stands). A line that
    does not parse is itself reported as a break rather than crashing the
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

    Opening is cheap and side-effect-free (A5): the constructor reads, writes,
    and verifies nothing, and is the marked read path (query, an auditor). A
    writing store is composed through the open_for_writing() factory, which runs
    the explicit recover() and verify_open() steps in order after construction,
    so the open ceremony lives in one place and a writing store cannot be
    assembled with verification skipped or the steps reordered. A read-only
    consumer uses the bare constructor and so never seeds a head or hits a
    tail-verify abort just by opening the store.

    Durability, locking, and the chain head (ADR-030):
    - Append and head advance: each line is written and flushed, and the
      in-memory head advances only after a successful write, so a failed append
      never leaves the head ahead of the file. The fsync durability promise was
      rolled back as out of demo scope (Round 21); the append still raises on
      OSError (fail-closed), it just no longer forces bytes to stable storage.
    - No concurrency guard: the single-writer filelock was rolled back as out of
      demo scope (Round 21). The demo has one synchronous writer (15.2), so the
      lock was functionless; the chain is now not guarded against a concurrent
      writer, which a production deployment would restore (ADR-030 superseded).
    - The in-memory head (last hash, last sequence) is the sole duplicate
      mechanism: publish() does not scan the file. The file is read only by
      recover() (to seed the head) and verify_open() (to check the tail), the
      explicit writing-path steps, never on the bare open.
    - Loud failure at open (recover()): a damaged line (invalid JSON, or a last
      line whose hash does not chain) makes recover() raise AuditLogError naming
      the problem; the store does not open on a damaged chain. The quarantine
      recovery and the heal/recovery event of ADR-030 were rolled back as out of
      demo scope (Round 21): "if the chain is damaged, I do not open" is less
      code and a clearer demo statement than quarantine-and-continue. A break
      before the tail window is still the full walk's job (verify_chain_file).
    """

    def __init__(
        self,
        path: Path,
        tail_window: int = OPEN_VERIFY_TAIL_WINDOW,
    ) -> None:
        """Initialize the store: paths, the genesis head.

        Opening is cheap and side-effect-free (A5, ADR-031): the constructor
        reads nothing, writes nothing, and verifies nothing. It records the
        path, seeds the in-memory head to the genesis sentinel, and ensures the
        backing file exists. A read-only consumer (query, an auditor) therefore
        never triggers a recovery write or a tail-verify abort just by opening
        the store.

        A writing composition path is built through the open_for_writing()
        factory, which runs the explicit steps in order after construction:
        recover() seeds the head from disk, then verify_open() runs the fast
        tail-window check. They are opt-in so the cost and the side effects land
        only on the path that writes, and routing them through the one factory
        keeps a writing store from being assembled with a step skipped or
        reordered (ADR-030, ADR-031).

        Args:
            path: Path to the JSON Lines file. Created (including parent
                directories) if it does not exist. Existing files are not
                truncated.
            tail_window: How many trailing events verify_open checks. The default
                bounds startup cost as the trail grows; tests pass a small value
                to exercise the window boundary.
        """
        self._path = path
        self._tail_window = tail_window
        self._head_hash: str = GENESIS_PREV_HASH
        self._head_sequence: int | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()

    @classmethod
    def open_for_writing(
        cls,
        path: Path,
        tail_window: int = OPEN_VERIFY_TAIL_WINDOW,
    ) -> JsonLinesAuditStore:
        """Open a store on the writing path: construct, recover, then verify_open.

        The one composition point for a writing store, so the open ceremony lives
        in a single place rather than being hand-rolled at each call site. The
        bare constructor is deliberately side-effect-free (A5, ADR-031);
        continuing the chain takes two further steps in a fixed order: recover()
        seeds the in-memory head from disk (raising loudly on a damaged tail),
        then verify_open() runs the fast tail-window check before the first
        append. This factory runs exactly that sequence. A caller that needs a
        writing store calls this rather than assembling the steps itself, so a
        future third writing site cannot compose a store that skips verify_open()
        (the fail-open the design exists to prevent, 18d) or runs them out of
        order.

        The read path keeps the bare constructor by design: a query consumer or an
        auditor must not pay seeding's cost or abort on a tampered tail merely by
        opening the store (A5, ADR-031). The constructor is therefore the marked
        read path and this factory is the marked write path.

        Args:
            path: Path to the JSON Lines audit file; created if absent, never
                truncated.
            tail_window: How many trailing events verify_open checks, forwarded to
                the constructor.

        Returns:
            A store whose head is seeded from disk and whose tail has verified,
            ready for the first append.

        Raises:
            AuditLogError: If the chain at open is damaged (a line fails to parse
                or the last line does not chain), or the tail window does not
                verify.
        """
        store = cls(path, tail_window=tail_window)
        store.recover()
        store.verify_open()
        return store

    def recover(self) -> None:
        """Seed the head from disk, raising loudly on a damaged tail. A write step.

        The explicit head-seeding step a writing composition path calls after
        opening (A5): the slim constructor does no read and no write, so a
        read-only consumer never pays for it. recover() reads the last
        tail_window+1 lines from the file end (O(K), Sec-2), parses them, and
        seeds the head from the last valid event. A damaged line (invalid JSON,
        or a last line whose hash does not chain from its predecessor) makes it
        raise AuditLogError naming the problem: the store does not open on a
        damaged chain. The quarantine recovery and the heal/recovery event of
        ADR-030 were rolled back as out of demo scope (Round 21). Idempotent on a
        clean chain: it re-seeds the same head and writes nothing.

        Raises:
            AuditLogError: If a line in the tail window fails to parse, or the
                last line's hash does not chain from its predecessor.
        """
        lines, _ = self._read_last_lines(self._tail_window + 1)
        events = self._parse_tail_or_raise(lines)
        self._seed_head_from(events)

    def verify_open(self) -> None:
        """Verify the last tail_window events, raising on a break. A writing step.

        The fast startup check (ADR-031), an explicit step the writing path calls
        after recover(): a read-only consumer skips it, so opening a tampered
        file for a query never aborts. recover() raises only on a damaged last
        line (unparseable, or a last line whose hash does not chain), so a
        parseable-but-non-chaining line within the tail window (a naive in-place
        edit to an interior line) is not caught by that last-line check; it would
        pass the seeding step silently and surface only at the next full verify.
        verify_open walks the tail window and catches such a break near the end,
        raising it with location. A break before the window is the full walk's
        job (verify_chain_file), deliberately not done here for startup speed.

        Raises:
            AuditLogError: If the tail window does not verify, carrying the first
                break's content-free description.
        """
        self._verify_tail_window(self._tail_events())

    def publish(self, event: AuditEvent) -> None:
        """Append an event to the audit log, stamping its chain position and hash.

        The store owns the chain fields: it assigns the next sequence number and
        the chained hash from its in-memory head, overwriting any values the
        caller set on them, then appends the event and advances the head. The
        line is written and flushed, and the head advances only on success, so a
        failed append leaves the in-memory chain level with the file rather than
        ahead of it. (The fsync durability promise of ADR-030 was rolled back as
        out of demo scope in Round 21.)

        The in-memory head is the sole duplicate mechanism (ADR-030): publish no
        longer scans the file, so a run of n events is no longer O(n^2). The file
        is read only at open, to seed the head. A re-published event_id is
        therefore not detected here; the pipeline keys each event with a fresh
        id, so this is a deliberate trade, not a gap (ADR-030).

        Args:
            event: The audit event to record. Its sequence_number and event_hash
                are assigned here; the caller supplies content, not chain fields.

        Raises:
            AuditLogError: If an I/O failure prevents the append. Raw OSErrors
                are wrapped so callers (Pipeline._emit, ADR-027) can route a
                store failure as the recoverable class without depending on
                stdlib exception types.
        """
        self._append_durably(event)

    @property
    def head(self) -> ChainHead:
        """The chain's current head (last hash and sequence): the anchor value.

        Read from the in-memory head the append maintains, advanced only after a
        successful write. An eval run records this via head_anchor into its
        committed results.json (ADR-031).
        """
        return ChainHead(
            event_hash=self._head_hash, sequence_number=self._head_sequence
        )

    def _append_durably(self, event: AuditEvent) -> None:
        """Validate, chain, durably write, then advance the head: the append rule.

        The write entry is where the content-free gate runs (Form B, ADR-032):
        before anything touches disk, the payload is validated against the event
        type's declared schema, so an undeclared key or a wrong type is rejected
        loudly and never reaches the chain. This is the only place the schema is
        enforced; the read path stays tolerant so a historical line never fails
        an open (Sec-3).

        The ordering preserves the head invariant (ADR-024): write the line,
        flush the buffer, and only then advance the in-memory head. The head is
        the claim that an event exists, so advancing it strictly after a
        successful write means a failed append leaves the head where it was,
        never ahead of the file. The fsync-before-advance durability promise of
        ADR-030 was rolled back as out of demo scope (Round 21); the append
        still raises on OSError, which is what fail-closed depends on, but it no
        longer forces the bytes to stable storage before returning.

        Args:
            event: The caller's event, before its chain fields are assigned.

        Raises:
            PayloadSchemaError: If the payload carries a key or type the event's
                declared schema forbids (ADR-032). A programming error in the
                emitter, raised before any write.
            AuditLogError: If writing the line fails. The OSError is wrapped and
                chained so the boundary contract holds (ADR-027).
        """
        validate_payload(event.event_type, event.payload)
        chained = self._chain(event)

        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(chained.model_dump_json() + "\n")
                f.flush()
        except OSError as exc:
            raise AuditLogError(
                f"failed to append audit event {event.event_id}"
            ) from exc

        # _chain always stamps a hash, so event_hash is a str here; the field
        # type is str | None for the pre-chain case, narrowed by construction.
        assert chained.event_hash is not None
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

    def _parse_tail_or_raise(self, lines: list[str]) -> list[AuditEvent]:
        """Parse the tail-window lines, raising loudly on any damaged line.

        The seeding read's parse step (Round 21, replacing quarantine recovery).
        lines are the last tail_window+1 file lines (read from the end, O(K)), so
        the last line here is the file's last line. A line that fails to parse,
        at any window position, makes the open fail with AuditLogError: the chain
        does not open on a damaged file, instead of quarantining and continuing.
        A last line that parses but whose hash does not chain from its
        predecessor is the same damaged-tail case and raises too. A
        parseable-but-non-chaining interior line near the tail is left for
        verify_open() (the tail-window walk) to surface with location; a break
        before the window is the full walk's job (verify_chain_file).

        Returns:
            The parsed tail events, to seed the head from.

        Raises:
            AuditLogError: If a tail-window line fails to parse, or the last line
                does not chain from its predecessor.
        """
        events: list[AuditEvent] = []
        for index, line in enumerate(lines):
            try:
                events.append(AuditEvent.model_validate_json(line))
            except ValueError as exc:
                raise AuditLogError(
                    f"audit store {self._path} has a damaged line at tail-window "
                    f"position {index}; the chain does not open on a damaged file "
                    "(Round 21: the loud-failure-at-open rule replaced quarantine "
                    "recovery). Inspect the file or run verify-audit for the full "
                    "walk."
                ) from exc
        if events and not self._last_event_chains(events):
            raise AuditLogError(
                f"audit store {self._path} has a last line whose hash does not "
                "chain from its predecessor; the chain does not open on a damaged "
                "tail (Round 21: loud failure at open replaced quarantine "
                "recovery)."
            )
        return events

    def _tail_events(self) -> list[AuditEvent]:
        """Read the events verify_open checks: the window plus its predecessor.

        Reads only the last tail_window+1 lines from the file end (Sec-2,
        ADR-032), so verify_open is O(K) and the tail-window promise that open
        does not parse the whole trail holds. Returns those events (the window
        and the predecessor verify_chain seeds the tail walk from), or the whole
        chain when it is no longer than the window. The break verify_open reports
        is therefore indexed within this returned sequence, the documented
        meaning of a ChainBreak index for a windowed walk. Called after
        recover(), so the tail it reads is the tail recover() seeded from.
        """
        lines, _ = self._read_last_lines(self._tail_window + 1)
        return [AuditEvent.model_validate_json(line) for line in lines]

    def _read_last_lines(self, max_lines: int) -> tuple[list[str], bool]:
        """Read up to the last max_lines non-empty lines, seeking from the end.

        Reads the file backwards in blocks until it has more than max_lines line
        terminators or reaches the start, so a clean open is O(max_lines), not
        O(file): the head is seeded and the tail window verified from the last
        lines without parsing the whole trail (Sec-2, ADR-032). Splitting on raw
        b"\\n" is safe for utf-8, since 0x0A never occurs inside a multibyte
        sequence.

        Args:
            max_lines: How many trailing lines to return at most.

        Returns:
            (lines, reached_start). lines are the last complete non-empty lines,
            at most max_lines, in file order. reached_start is True exactly when
            the returned lines are the whole chain (the file has at most max_lines
            lines, so the first is genesis), False when lines is a proper suffix.
            A partial leading line from the backward read is never returned.
        """
        if not self._path.exists():
            return [], True
        block_size = 8192
        with self._path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            position = f.tell()
            buffer = b""
            while position > 0:
                read_size = min(block_size, position)
                position -= read_size
                f.seek(position)
                buffer = f.read(read_size) + buffer
                if buffer.count(b"\n") > max_lines:
                    break
            read_whole_file = position == 0
        lines = [line for line in buffer.decode("utf-8").splitlines() if line.strip()]
        # reached_start is the genesis-included claim, not merely "the read hit
        # byte 0": a small file read whole in one block still yields only a
        # suffix once trimmed to max_lines. Trimming also drops the partial
        # leading line a mid-file backward read leaves (there are then more than
        # max_lines complete lines after it, so the slice excludes it).
        reached_start = read_whole_file and len(lines) <= max_lines
        return lines[-max_lines:], reached_start

    def _verify_tail_window(self, events: list[AuditEvent]) -> None:
        """Verify the last tail_window events, raising loudly on a break.

        The fast startup check (ADR-031): recover() raises only on a damaged last
        line (unparseable, or a hash that does not chain), so a parseable-but-
        non-chaining interior line near the tail (a naive in-place edit) would
        otherwise pass open silently and surface only at the next full verify.
        Verifying the tail window catches such a break near the end and raises it
        with location, so a damaged tail is diagnosed at open, not merely the
        next audit. A break before the window is the full walk's job
        (verify_chain_file), deliberately not done here for startup speed.

        Args:
            events: The tail events to verify, the window plus its predecessor,
                as returned by _tail_events.

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
            events: The valid events to seed from (recover() has already raised
                if the tail was damaged).
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
