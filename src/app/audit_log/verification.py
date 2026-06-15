"""Hash-chain verification: walk a chain and report the first break (ADR-031).

The verification semantics and the value objects that carry their result live
here, beside the hashing half they depend on (compute_event_hash,
GENESIS_PREV_HASH in serialization.py), not in the file store. verify_chain
recomputes each event's hash with the SAME canonical serializer the write path
used, so the proof has exactly one definition (ADR-029); the file-bound entry
(verify_chain_file, the open-time tail window) stays in the store, which composes
these pieces with the storage mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.audit_log.serialization import GENESIS_PREV_HASH, compute_event_hash
from app.core.events import AuditEvent


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
