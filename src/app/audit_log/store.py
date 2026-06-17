"""JSON Lines file-backed implementation of AuditEventPublisherProtocol."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import structlog

from app.audit_log.anchor import ChainHead
from app.audit_log.events import AUDIT_RECOVERED
from app.audit_log.payload_schema import validate_payload
from app.audit_log.serialization import GENESIS_PREV_HASH, compute_event_hash
from app.audit_log.verification import ChainBreak, VerificationResult, verify_chain
from app.core.events import SYSTEM_EINWENDUNGS_ID, AuditEvent, AuditEventType
from app.core.failures import AuditLogError

#: Module logger for the store's recovery event. Routes through the same
#: governed chain as every other event (ADR-026).
_log = structlog.get_logger()

#: How many trailing events are verified at store open: the last K, not the
#: whole file, so startup stays fast as the trail grows (ADR-031). The full walk
#: is the auditor's CLI command (verify_chain_file); the window only diagnoses a
#: break near the tail, where a crash-or-tamper is most likely to have landed.
OPEN_VERIFY_TAIL_WINDOW: Final[int] = 256


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

    Opening is cheap and side-effect-free (A5): the constructor reads, writes,
    and verifies nothing, and is the marked read path (query, an auditor). A
    writing store is composed through the open_for_writing() factory, which runs
    the explicit recover() and verify_open() steps in order after construction,
    so the open ceremony lives in one place and a writing store cannot be
    assembled with verification skipped or the steps reordered. A read-only
    consumer uses the bare constructor and so never triggers a recovery write or
    a tail-verify abort just by opening the store.

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
    - Recovery (recover()): a damaged or partial last line (invalid JSON at EOF,
      or a line whose hash does not chain) is moved to a quarantine file, its
      bytes' hash is recorded, and a recovery event is written into the chain.
      Only the last line is healed: a mid-file break is not silently fixed here,
      it is for verify_chain (18c) to surface, because a partial last line is
      directly observable while lost interior events are not without an external
      anchor (ADR-030).
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
        """Seed the head from disk, healing a damaged tail. A writing-path step.

        The explicit recovery step a writing composition path calls after
        opening (A5): the slim constructor does no read and no write, so a
        read-only consumer never pays for recovery. recover() reads the chain to
        its last valid line and seeds the head from it, and if the last line is
        damaged (invalid JSON at EOF, or a hash that does not chain from its
        predecessor) quarantines it and appends a recovery event. Only the last
        line is healed: an interior break is left for verify_chain (18c) to
        surface, because a partial last line is directly observable while a lost
        interior event is not without an external anchor (ADR-030). Idempotent on
        a clean chain: it re-seeds the same head and writes nothing.

        Raises:
            AuditLogError: If an interior (non-last) line fails to parse (ADR-030).
        """
        self._recover_and_seed()

    def verify_open(self) -> None:
        """Verify the last tail_window events, raising on a break. A writing step.

        The fast startup check (ADR-031), an explicit step the writing path calls
        after recover(): a read-only consumer skips it, so opening a tampered
        file for a query never aborts. Recovery heals only a damaged last line,
        so a parseable-but-non-chaining interior line near the tail (a naive
        in-place edit) would otherwise pass open silently and surface only at the
        next full verify; verify_open catches such a break near the end and
        raises it with location. A break before the window is the full walk's job
        (verify_chain_file), deliberately not done here for startup speed.

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

    def _recover_and_seed(self) -> None:
        """Seed the in-memory head from disk, recovering a damaged tail (ADR-030).

        The body of recover(), under the single-writer lock. The clean path is
        O(K): it reads only the last tail_window+1 lines from the file end
        (Sec-2, ADR-032), seeds the head from the last valid event, and returns
        without parsing the whole trail. A damaged last line (invalid JSON at
        EOF, or a hash that does not chain) falls back to the full read its
        rewrite needs: recovery quarantines the damaged tail, appends a recovery
        event, and logs it. Only the last line is healed; an interior break is
        left for verify_chain (18c) to surface, because a partial last line is
        directly observable while a lost interior event is not without an
        external anchor.

        The tail-window verification is no longer part of seeding: it is the
        separate verify_open() step the writing path runs after recover(), so a
        read-only consumer that never calls it does not abort on a tampered tail
        (A5, ADR-031).
        """
        lines, _ = self._read_last_lines(self._tail_window + 1)
        events, damaged = self._parse_tail_for_recovery(lines)
        if not damaged:
            self._seed_head_from(events)
            return
        # A damaged last line: the rewrite needs the full valid prefix, so the
        # rare damaged path falls back to the full read. Only the clean seeding
        # read is O(K) (Sec-2: recovery's tail handling stays).
        self._recover_full()

    def _recover_full(self) -> None:
        """Full-file recovery for a damaged last line: quarantine and seed.

        The fallback _recover_and_seed takes only when the O(K) tail read found a
        damaged last line. It re-reads the whole file (the rewrite of the healed
        prefix needs every valid line), seeds the head, and quarantines the
        damaged tail with a recovery event. An interior unparseable line raises
        here, as it did before the O(K) optimization.
        """
        valid_events, valid_lines, damaged_line = self._read_chain_with_tail_check()
        self._seed_head_from(valid_events)
        if damaged_line is not None:
            self._quarantine_and_record(damaged_line, valid_lines)

    def _parse_tail_for_recovery(
        self, lines: list[str]
    ) -> tuple[list[AuditEvent], bool]:
        """Parse the tail lines, flagging a damaged last line for the full path.

        The windowed counterpart of _read_chain_with_tail_check used by the O(K)
        clean path: lines are the last tail_window+1 file lines (read from the
        end), so the last line here is the file's last line. An unparseable last
        line, or a last line that parses but does not chain, is the EOF damage
        recovery heals, reported as damaged=True so the caller falls back to the
        full read. An unparseable non-last line is an interior break that raises,
        as in the full read.

        Returns:
            (events, damaged). damaged is True when the last line is the EOF
            damage case; events then excludes nothing (the caller re-reads).

        Raises:
            AuditLogError: If a non-last line in the window fails to parse.
        """
        events: list[AuditEvent] = []
        for index, line in enumerate(lines):
            try:
                events.append(AuditEvent.model_validate_json(line))
            except ValueError as exc:
                if index == len(lines) - 1:
                    return events, True
                raise AuditLogError(
                    f"audit store {self._path} has a damaged interior line near "
                    f"the tail (window position {index}); recovery heals only a "
                    "damaged last line, an interior break is for verify_chain "
                    "to surface (18c)"
                ) from exc
        if not events:
            return events, False
        return events, not self._last_event_chains(events)

    def _tail_events(self) -> list[AuditEvent]:
        """Read the events verify_open checks: the window plus its predecessor.

        Reads only the last tail_window+1 lines from the file end (Sec-2,
        ADR-032), so verify_open is O(K) and the tail-window promise that open
        does not parse the whole trail holds. Returns those events (the window
        and the predecessor verify_chain seeds the tail walk from), or the whole
        chain when it is no longer than the window. The break verify_open reports
        is therefore indexed within this returned sequence, the documented
        meaning of a ChainBreak index for a windowed walk. Called after
        recover(), so the tail it reads is the healed tail.
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

        The fast startup check (ADR-031): recovery heals only a damaged last
        line, so a parseable-but-non-chaining interior line (a naive in-place
        edit) would otherwise pass open silently and surface only at the next
        full verify. Verifying the tail window catches such a break near the end
        and raises it with location, so a damaged tail is diagnosed at open, not
        merely the next audit. A break before the window is the full walk's job
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
        the chain. It is a WIEDERHERSTELLUNG event with the shared system
        sentinel id (SYSTEM_EINWENDUNGS_ID, core/events.py), not an objection
        event (ADR-030).

        Args:
            quarantined_hash: SHA-256 hex of the quarantined bytes.
        """
        return AuditEvent(
            event_id=str(uuid.uuid4()),
            event_type=AuditEventType.WIEDERHERSTELLUNG,
            einwendungs_id=SYSTEM_EINWENDUNGS_ID,
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
