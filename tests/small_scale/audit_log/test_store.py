"""Unit tests for JsonLinesAuditStore."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from filelock import FileLock

from app.audit_log.anchor import head_anchor, results_with_anchor
from app.audit_log.serialization import GENESIS_PREV_HASH, compute_event_hash
from app.audit_log.store import JsonLinesAuditStore, verify_chain_file
from app.audit_log.verification import verify_chain
from app.core.events import AuditEvent, AuditEventType
from app.core.failures import AuditLogError


def _make_event(
    einwendungs_id: str = "EW-001",
    event_type: AuditEventType = AuditEventType.EINGANG,
    timestamp: datetime | None = None,
    payload: dict | None = None,
) -> AuditEvent:
    kwargs: dict = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "einwendungs_id": einwendungs_id,
    }
    if timestamp is not None:
        kwargs["timestamp"] = timestamp
    if payload is not None:
        kwargs["payload"] = payload
    return AuditEvent(**kwargs)


def test_publish_single_event(tmp_path: Path) -> None:
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    event = _make_event()
    store.publish(event)

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    retrieved = AuditEvent.model_validate_json(lines[0])
    assert retrieved.event_id == event.event_id


def test_publish_appends(tmp_path: Path) -> None:
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    event_a = _make_event(einwendungs_id="EW-001")
    event_b = _make_event(einwendungs_id="EW-002")
    store.publish(event_a)
    store.publish(event_b)

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    ids = {AuditEvent.model_validate_json(line).event_id for line in lines}
    assert event_a.event_id in ids
    assert event_b.event_id in ids


def test_publish_translates_io_failure_into_audit_log_error(tmp_path: Path) -> None:
    """No raw OSError escapes publish (the publisher failure contract).

    Given a store whose backing path has become unreadable as a file (a
    directory sits at the path), when publish hits the resulting I/O failure,
    then the store raises AuditLogError with the OSError chained, never the
    raw OSError: callers route the recoverable store-failure class on exactly
    one exception type (core/protocols.py, ADR-027).
    """
    path = tmp_path / "audit.jsonl"
    store = JsonLinesAuditStore(path)
    path.unlink()
    path.mkdir()

    with pytest.raises(AuditLogError) as exc_info:
        store.publish(_make_event())

    assert isinstance(exc_info.value.__cause__, OSError)


def test_publish_does_not_read_the_whole_file_per_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a published event, when another is published, then publish does not
    scan the whole file: the in-memory head is the sole duplicate mechanism, so
    the O(n^2) per-append read of 18a is gone (ADR-030).
    """
    path = tmp_path / "audit.jsonl"
    store = JsonLinesAuditStore(path)
    store.publish(_make_event(einwendungs_id="EW-001"))

    def _boom(*args: object, **kwargs: object) -> list[AuditEvent]:
        raise AssertionError("publish must not read the whole file per append")

    monkeypatch.setattr(store, "_read_all", _boom)
    store.publish(_make_event(einwendungs_id="EW-002"))  # must not call _read_all

    # Read the file directly (query() legitimately reads; only publish must not).
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_a_second_writer_is_blocked_while_the_lock_is_held(tmp_path: Path) -> None:
    """Given the audit store lock is already held, when the store tries to
    publish, then it fails loudly with AuditLogError instead of interleaving;
    once the lock is released the publish proceeds and the chain stays valid.

    The single-writer advisory lock (ADR-030) serializes writers: two writers
    cannot interleave an append. A held lock is the stand-in for a concurrent
    writer; the contended store must fail loudly on the documented type, not hang
    or write a conflicting line.
    """
    path = tmp_path / "audit.jsonl"
    store = JsonLinesAuditStore(path, lock_timeout=0.1)

    contending_lock = FileLock(f"{path}.lock")
    with contending_lock:
        with pytest.raises(AuditLogError):
            store.publish(_make_event(einwendungs_id="EW-001"))

    # The blocked event never reached disk, so after release the chain is clean:
    # the next publish is sequence 0 and chains from the genesis sentinel.
    store.publish(_make_event(einwendungs_id="EW-002"))
    events = store.query()
    assert [event.sequence_number for event in events] == [0]
    assert events[0].event_hash == compute_event_hash(events[0], GENESIS_PREV_HASH)


def test_concurrent_recovery_is_blocked_while_the_lock_is_held(tmp_path: Path) -> None:
    """Given the audit store lock is held, when a store recovers on the same
    path, then its recover() fails loudly rather than recovering concurrently.

    recover() is inside the critical section (A5, ADR-030): two starts must not
    seed the head from the same file at once. The bare open is side-effect-free
    and takes no lock; recover() is where the writing path acquires it, so a held
    lock makes the second start's recover() fail on the documented type.
    """
    path = tmp_path / "audit.jsonl"
    JsonLinesAuditStore(path).publish(_make_event(einwendungs_id="EW-001"))

    contending_lock = FileLock(f"{path}.lock")
    with contending_lock:
        with pytest.raises(AuditLogError):
            JsonLinesAuditStore(path, lock_timeout=0.1).recover()


def test_query_by_einwendungs_id(tmp_path: Path) -> None:
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    e1 = _make_event(einwendungs_id="EW-001")
    e2 = _make_event(einwendungs_id="EW-001")
    e3 = _make_event(einwendungs_id="EW-999")
    store.publish(e1)
    store.publish(e2)
    store.publish(e3)

    results = store.query(einwendungs_id="EW-001")
    assert len(results) == 2
    assert all(r.einwendungs_id == "EW-001" for r in results)


def test_query_by_event_type(tmp_path: Path) -> None:
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    e1 = _make_event(event_type=AuditEventType.EINGANG)
    e2 = _make_event(event_type=AuditEventType.TRIAGE)
    e3 = _make_event(event_type=AuditEventType.EINGANG)
    store.publish(e1)
    store.publish(e2)
    store.publish(e3)

    results = store.query(event_type=AuditEventType.EINGANG)
    assert len(results) == 2
    assert all(r.event_type == AuditEventType.EINGANG for r in results)


def test_query_returns_empty_list_on_no_match(tmp_path: Path) -> None:
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    store.publish(_make_event(einwendungs_id="EW-001"))

    results = store.query(einwendungs_id="EW-DOES-NOT-EXIST")
    assert results == []


def test_query_returns_empty_list_if_file_missing(tmp_path: Path) -> None:
    path = tmp_path / "subdir" / "audit.jsonl"
    store = JsonLinesAuditStore(path)
    path.unlink()

    results = store.query(einwendungs_id="EW-001")
    assert results == []


def test_append_only_semantics(tmp_path: Path) -> None:
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    e1 = _make_event(einwendungs_id="EW-001")
    e2 = _make_event(einwendungs_id="EW-002")
    store.publish(e1)
    store.publish(e2)

    content = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.strip()]
    assert len(lines) == 2
    ids = {AuditEvent.model_validate_json(line).event_id for line in lines}
    assert e1.event_id in ids
    assert e2.event_id in ids


def test_event_survives_roundtrip(tmp_path: Path) -> None:
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    original = _make_event(
        einwendungs_id="EW-042",
        event_type=AuditEventType.BRIEFING_ERSTELLT,
        timestamp=ts,
        payload={"entry_count": 3},
    )
    store.publish(original)

    results = store.query()
    assert len(results) == 1
    retrieved = results[0]
    assert retrieved.event_id == original.event_id
    assert retrieved.einwendungs_id == original.einwendungs_id
    assert retrieved.event_type == original.event_type
    assert retrieved.timestamp == original.timestamp
    assert retrieved.timestamp.tzinfo is not None
    assert retrieved.payload == original.payload
    # The store now populates the chain fields on append (Round 18a, ADR-024):
    # the first event is sequence 0 and carries its hash, chained from genesis.
    assert retrieved.serialization_version == 1
    assert retrieved.sequence_number == 0
    assert retrieved.event_hash == compute_event_hash(retrieved, GENESIS_PREV_HASH)


def test_query_combined_filters(tmp_path: Path) -> None:
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    target = _make_event(einwendungs_id="EW-001", event_type=AuditEventType.TRIAGE)
    other_ew = _make_event(einwendungs_id="EW-002", event_type=AuditEventType.TRIAGE)
    other_type = _make_event(einwendungs_id="EW-001", event_type=AuditEventType.EINGANG)
    store.publish(target)
    store.publish(other_ew)
    store.publish(other_type)

    results = store.query(einwendungs_id="EW-001", event_type=AuditEventType.TRIAGE)
    assert len(results) == 1
    assert results[0].event_id == target.event_id


def test_query_time_filters(tmp_path: Path) -> None:
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    ref = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    past = _make_event(timestamp=ref - timedelta(hours=2))
    present = _make_event(timestamp=ref)
    future = _make_event(timestamp=ref + timedelta(hours=2))
    store.publish(past)
    store.publish(present)
    store.publish(future)

    results = store.query(
        after=ref - timedelta(hours=1),
        before=ref + timedelta(hours=1),
    )
    assert len(results) == 1
    assert results[0].event_id == present.event_id


def test_genesis_event_chains_from_the_all_zero_sentinel(tmp_path: Path) -> None:
    """Given a fresh store, when the first event is published, then it is
    sequence 0 and its hash is computed from the all-zero genesis sentinel.

    The first event has no predecessor, so its prev_hash is the documented
    sentinel rather than a real digest (ADR-024). Pinning this anchors the whole
    chain: every later link is only as sound as the genesis it descends from.
    """
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    store.publish(_make_event(einwendungs_id="EW-001"))

    [genesis] = store.query()
    assert genesis.sequence_number == 0
    assert genesis.event_hash == compute_event_hash(genesis, GENESIS_PREV_HASH)


def test_sequence_numbers_increase_monotonically_from_zero(tmp_path: Path) -> None:
    """Given three published events, when the log is read, then their sequence
    numbers are 0, 1, 2 in append order.

    The sequence number is the event's position in the chain and is part of its
    hashed content, so it must advance by one per append from genesis.
    """
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    store.publish(_make_event(einwendungs_id="EW-001"))
    store.publish(_make_event(einwendungs_id="EW-002"))
    store.publish(_make_event(einwendungs_id="EW-003"))

    events = store.query()
    assert [event.sequence_number for event in events] == [0, 1, 2]


def test_a_published_chain_recomputes_consistently_from_genesis(
    tmp_path: Path,
) -> None:
    """Given a chain of five events, when each hash is recomputed from its
    predecessor, then every stored hash matches.

    This is the verify property in miniature: walk from the genesis sentinel,
    recompute H(canonical_bytes + prev_hash) per event, and confirm it equals
    what was written. verify_chain() as a named function is 18c; the property it
    will rely on is proven here directly.
    """
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    for index in range(5):
        store.publish(_make_event(einwendungs_id=f"EW-{index:03d}"))

    events = store.query()
    prev_hash = GENESIS_PREV_HASH
    for event in events:
        assert event.event_hash == compute_event_hash(event, prev_hash)
        prev_hash = event.event_hash


def test_mutating_a_past_events_payload_breaks_the_link_to_its_successor(
    tmp_path: Path,
) -> None:
    """Given a recorded chain, when a past event's payload is altered, then the
    event's own hash changes and its successor no longer chains from it.

    This is the tamper-evidence property the chain exists for (ADR-024): a single
    edited event cannot pass an honest recomputation, and because each event
    binds its predecessor's hash, the break propagates to every successor.
    """
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    store.publish(_make_event(einwendungs_id="EW-001"))
    store.publish(_make_event(einwendungs_id="EW-002"))
    first, second = store.query()

    tampered_first = first.model_copy(update={"payload": {"tampered": True}})
    recomputed_first_hash = compute_event_hash(tampered_first, GENESIS_PREV_HASH)

    # The edited event no longer matches its own recorded hash.
    assert recomputed_first_hash != first.event_hash
    # The successor bound the original predecessor hash, so it does not chain
    # from the edited event: the tamper is detectable at the link.
    assert second.event_hash != compute_event_hash(second, recomputed_first_hash)
    # The successor still chains from the original predecessor hash, proving the
    # break is the tamper itself, not a serializer artifact.
    assert second.event_hash == compute_event_hash(second, first.event_hash)


def test_chain_continues_after_the_store_is_reopened(tmp_path: Path) -> None:
    """Given events written by one store instance, when a new instance opens the
    same file and appends, then it resumes the sequence and the hash links.

    The in-memory head is seeded from the file at open this round (durable head
    recovery is 18b), so a restart must continue the chain rather than restart at
    genesis and orphan the events already on disk.
    """
    path = tmp_path / "audit.jsonl"
    first_store = JsonLinesAuditStore(path)
    first_store.publish(_make_event(einwendungs_id="EW-001"))
    first_store.publish(_make_event(einwendungs_id="EW-002"))

    reopened = JsonLinesAuditStore(path)
    reopened.recover()  # writing path seeds the head from the events on disk
    reopened.publish(_make_event(einwendungs_id="EW-003"))

    events = reopened.query()
    assert [event.sequence_number for event in events] == [0, 1, 2]
    # The event appended after reopen chains from the last event on disk.
    assert events[2].event_hash == compute_event_hash(events[2], events[1].event_hash)
    # And the whole chain still recomputes consistently from genesis.
    prev_hash = GENESIS_PREV_HASH
    for event in events:
        assert event.event_hash == compute_event_hash(event, prev_hash)
        prev_hash = event.event_hash


def test_store_assigns_chain_fields_and_ignores_caller_supplied_ones(
    tmp_path: Path,
) -> None:
    """Given an event with a caller-set sequence_number and event_hash, when it
    is published, then the store overwrites both with the chain's own values.

    The chain fields are the store's to assign from its head; honoring
    caller-supplied ones would let a caller forge a position or a hash. The store
    stamps them regardless of what arrived.
    """
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    forged = _make_event(einwendungs_id="EW-001").model_copy(
        update={"sequence_number": 99, "event_hash": "f" * 64}
    )
    store.publish(forged)

    [stored] = store.query()
    assert stored.sequence_number == 0
    assert stored.event_hash == compute_event_hash(stored, GENESIS_PREV_HASH)
    assert stored.event_hash != "f" * 64


# A distinctive, invalid-JSON tail standing in for a crash mid-write: it cannot
# parse, and it shares no substring with a real event line, so absence checks
# against the healed live file are unambiguous.
_PARTIAL_LAST_LINE = '{"truncated_partial_write": "PARTIAL'


def test_a_truncated_last_line_is_quarantined_with_a_recovery_event(
    tmp_path: Path,
) -> None:
    """Given a chain whose last line was partially written (a crash mid-append),
    when the store reopens, then the partial line is quarantined (absent from the
    live file, present in audit.jsonl.corrupt.<timestamp>) and a recovery event
    carrying the quarantined bytes' hash and a count, with no raw content, is in
    the chain (ADR-030).
    """
    path = tmp_path / "audit.jsonl"
    store = JsonLinesAuditStore(path)
    store.publish(_make_event(einwendungs_id="EW-001"))
    store.publish(_make_event(einwendungs_id="EW-002"))
    with path.open("a", encoding="utf-8") as f:
        f.write(_PARTIAL_LAST_LINE + "\n")
    expected_hash = hashlib.sha256(_PARTIAL_LAST_LINE.encode("utf-8")).hexdigest()

    reopened = JsonLinesAuditStore(path)
    reopened.recover()  # the writing path heals the damaged tail

    # The partial line is gone from the live file and preserved in quarantine.
    assert _PARTIAL_LAST_LINE not in path.read_text(encoding="utf-8")
    corrupt_files = list(tmp_path.glob("audit.jsonl.corrupt.*"))
    assert len(corrupt_files) == 1
    assert corrupt_files[0].read_text(encoding="utf-8").strip() == _PARTIAL_LAST_LINE

    # A recovery event records the quarantine: the bytes' hash and a count, never
    # the raw content.
    [recovery] = reopened.query(event_type=AuditEventType.WIEDERHERSTELLUNG)
    assert recovery.payload["quarantined_hash"] == expected_hash
    assert recovery.payload["quarantined_lines"] == 1
    assert _PARTIAL_LAST_LINE not in json.dumps(recovery.payload)


def test_the_chain_continues_from_the_recovery_event_after_recovery(
    tmp_path: Path,
) -> None:
    """Given recovery quarantined a damaged tail, when a new event is published,
    then the head reflects the recovered chain and the new event chains onto the
    recovery event: the chain is continuous across the heal (ADR-030).
    """
    path = tmp_path / "audit.jsonl"
    store = JsonLinesAuditStore(path)
    store.publish(_make_event(einwendungs_id="EW-001"))
    with path.open("a", encoding="utf-8") as f:
        f.write(_PARTIAL_LAST_LINE + "\n")

    reopened = JsonLinesAuditStore(path)
    reopened.recover()  # recovery seeds the head and appends the recovery event
    reopened.publish(_make_event(einwendungs_id="EW-002"))

    events = reopened.query()
    # EW-001 (0), the recovery event (1), EW-002 (2): one continuous chain.
    assert [event.sequence_number for event in events] == [0, 1, 2]
    assert events[1].event_type == AuditEventType.WIEDERHERSTELLUNG
    prev_hash = GENESIS_PREV_HASH
    for event in events:
        assert event.event_hash == compute_event_hash(event, prev_hash)
        prev_hash = event.event_hash


def test_a_last_line_whose_hash_does_not_chain_is_quarantined(
    tmp_path: Path,
) -> None:
    """Given a last line that parses but whose hash does not chain from its
    predecessor, when the store reopens, then it is quarantined like a truncated
    line: a damaged tail is a damaged tail whether the damage is partial bytes or
    a broken link (ADR-030).
    """
    path = tmp_path / "audit.jsonl"
    store = JsonLinesAuditStore(path)
    store.publish(_make_event(einwendungs_id="EW-001"))
    [genuine] = store.query()
    # A well-formed event that does NOT chain (wrong event_hash for its position).
    forged = genuine.model_copy(update={"sequence_number": 1, "event_hash": "a" * 64})
    with path.open("a", encoding="utf-8") as f:
        f.write(forged.model_dump_json() + "\n")

    reopened = JsonLinesAuditStore(path)
    reopened.recover()  # the writing path quarantines the non-chaining tail

    # The forged line is quarantined and a recovery event replaces it in the chain.
    assert len(list(tmp_path.glob("audit.jsonl.corrupt.*"))) == 1
    sequences = [event.sequence_number for event in reopened.query()]
    assert sequences == [0, 1]  # EW-001 then the recovery event
    [recovery] = reopened.query(event_type=AuditEventType.WIEDERHERSTELLUNG)
    assert recovery.sequence_number == 1


def test_an_interior_damaged_line_is_not_healed_but_surfaces_loudly(
    tmp_path: Path,
) -> None:
    """Given a damaged line that is not the last (an interior break), when the
    writing path recovers, then it raises rather than silently truncating:
    recovery heals only the observable EOF case, interior breaks are for
    verify_chain (ADR-030).
    """
    path = tmp_path / "audit.jsonl"
    store = JsonLinesAuditStore(path)
    store.publish(_make_event(einwendungs_id="EW-001"))
    store.publish(_make_event(einwendungs_id="EW-002"))
    # Insert a damaged line between the two valid ones (an interior break).
    good_lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(
        good_lines[0] + "\n" + _PARTIAL_LAST_LINE + "\n" + good_lines[1] + "\n",
        encoding="utf-8",
    )

    with pytest.raises(AuditLogError):
        JsonLinesAuditStore(path).recover()


def _written_chain(tmp_path: Path, count: int = 5) -> tuple[Path, list[AuditEvent]]:
    """Write a real chain of `count` events and return the path and the events.

    The events carry hashes the store computed via the canonical serializer, so
    verifying them is the same-serializer property in practice: a divergent
    verify serializer would fail on this freshly written chain.
    """
    path = tmp_path / "audit.jsonl"
    store = JsonLinesAuditStore(path)
    for index in range(count):
        store.publish(_make_event(einwendungs_id=f"EW-{index:03d}"))
    return path, store.query()


def test_verify_chain_accepts_a_freshly_written_chain_same_serializer(
    tmp_path: Path,
) -> None:
    """Given a chain the store wrote, when it is verified, then it is ok.

    This is the same-serializer guard (ADR-031): the store stamped each hash via
    canonical_bytes, and verify_chain recomputes via the same compute_event_hash.
    If a second, divergent serialization were introduced into the verify path,
    this freshly written chain would fail to verify, so the test fails exactly
    when the two serializations drift.
    """
    _, events = _written_chain(tmp_path)

    result = verify_chain(events)

    assert result.ok
    assert result.first_break is None


def test_verify_chain_accepts_a_chain_anchored_on_the_18a_golden_event() -> None:
    """Given the 18a golden event chained from genesis, when verified, then ok.

    Ties verification to the exact canonical bytes 18a froze (test_serialization
    golden): verify_chain recomputes over those bytes, so the chain proof rests
    on the one pinned byte form, not a second copy.
    """
    golden = AuditEvent(
        event_id="11111111-1111-1111-1111-111111111111",
        event_type=AuditEventType.EINGANG,
        einwendungs_id="EW-001",
        timestamp=datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
        payload={"model": "mistral", "confidence": 0.95},
        sequence_number=0,
    )
    chained = golden.model_copy(
        update={"event_hash": compute_event_hash(golden, GENESIS_PREV_HASH)}
    )

    assert verify_chain([chained]).ok


def test_verify_chain_detects_a_mutated_past_payload_with_location(
    tmp_path: Path,
) -> None:
    """Given a chain with one event's payload altered (its hash left intact),
    when verified, then the break is reported at that event's index, not a bare
    False (ADR-031).
    """
    _, events = _written_chain(tmp_path, count=3)
    events[1] = events[1].model_copy(update={"payload": {"tampered": True}})

    result = verify_chain(events)

    assert not result.ok
    assert result.first_break is not None
    assert result.first_break.index == 1
    assert result.first_break.sequence_number == 1
    assert "hash" in result.first_break.reason


def test_verify_chain_detects_a_broken_link_with_location(tmp_path: Path) -> None:
    """Given a chain with one event's recorded hash overwritten (a broken link),
    when verified, then the break is reported at that event's index.
    """
    _, events = _written_chain(tmp_path, count=3)
    events[1] = events[1].model_copy(update={"event_hash": "a" * 64})

    result = verify_chain(events)

    assert not result.ok
    assert result.first_break is not None
    assert result.first_break.index == 1
    assert result.first_break.found == "a" * 64


def test_verify_chain_detects_a_sequence_gap_with_location(tmp_path: Path) -> None:
    """Given a chain with an interior event removed (a sequence gap), when
    verified, then the break is reported at the gap with the expected vs found
    sequence number.
    """
    _, events = _written_chain(tmp_path, count=3)
    del events[1]  # sequences are now 0, 2: a gap at the second position

    result = verify_chain(events)

    assert not result.ok
    assert result.first_break is not None
    assert result.first_break.index == 1
    assert "sequence" in result.first_break.reason
    assert result.first_break.expected == "1"
    assert result.first_break.found == "2"


def test_verify_chain_detects_a_wrong_genesis_sentinel_with_location(
    tmp_path: Path,
) -> None:
    """Given a genesis event whose hash was computed from a non-genesis
    predecessor, when verified, then the break is reported at index 0 as a
    genesis-anchor failure: the whole chain is only as sound as the genesis it
    descends from (ADR-031).
    """
    _, events = _written_chain(tmp_path, count=3)
    events[0] = events[0].model_copy(
        update={"event_hash": compute_event_hash(events[0], "f" * 64)}
    )

    result = verify_chain(events)

    assert not result.ok
    assert result.first_break is not None
    assert result.first_break.index == 0
    assert "genesis" in result.first_break.reason


def test_verify_chain_file_reports_an_unparseable_line(tmp_path: Path) -> None:
    """Given a chain file with a corrupt line, when verified for an auditor,
    then it is reported as a located break rather than crashing the command.
    """
    path, _ = _written_chain(tmp_path, count=3)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = '{"truncated": '
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = verify_chain_file(path)

    assert not result.ok
    assert result.first_break is not None
    assert result.first_break.index == 1


def test_verify_open_detects_a_break_within_the_window(
    tmp_path: Path,
) -> None:
    """Given a chain whose second-to-last event was edited in place (its hash
    kept, the last line left intact so recovery does not quarantine it), when
    the writing path verifies the tail window, then verify_open fails loudly with
    the break's location (ADR-031): a tampered tail is diagnosed at open.

    The break index is reported within the verified window (the window plus its
    predecessor), the documented meaning of a ChainBreak index for a windowed
    walk: the tampered event is the second of the three tail events checked.
    """
    path, _ = _written_chain(tmp_path, count=5)
    lines = path.read_text(encoding="utf-8").splitlines()
    second_to_last = AuditEvent.model_validate_json(lines[3])
    lines[3] = second_to_last.model_copy(
        update={"payload": {"tampered": True}}
    ).model_dump_json()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    store = JsonLinesAuditStore(path, tail_window=2)
    store.recover()  # the tampered line is interior, so the tail is intact

    with pytest.raises(AuditLogError, match="index 1"):
        store.verify_open()


def test_verify_open_does_not_see_a_break_before_the_window(
    tmp_path: Path,
) -> None:
    """Given a chain whose first event was edited in place, when the writing path
    verifies a small tail window, then verify_open succeeds (the break is before
    the window), while a full walk still catches it: the window is a fast startup
    check, not the full audit (ADR-031).
    """
    path, _ = _written_chain(tmp_path, count=5)
    lines = path.read_text(encoding="utf-8").splitlines()
    first = AuditEvent.model_validate_json(lines[0])
    lines[0] = first.model_copy(
        update={"payload": {"tampered": True}}
    ).model_dump_json()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    store = JsonLinesAuditStore(path, tail_window=2)
    store.recover()
    store.verify_open()  # the break is before the window, so the tail check passes

    full = verify_chain_file(path)
    assert not full.ok
    assert full.first_break is not None
    assert full.first_break.index == 0


def test_a_slim_open_performs_no_write_and_no_verify(tmp_path: Path) -> None:
    """Given a chain with a damaged tail, when a store is merely opened (no
    recover, no verify_open), then nothing is quarantined and no recovery event
    is written: opening is side-effect-free, the heal is the explicit recover()
    step (A5, ADR-031).
    """
    path = tmp_path / "audit.jsonl"
    seeding = JsonLinesAuditStore(path)
    seeding.publish(_make_event(einwendungs_id="EW-001"))
    with path.open("a", encoding="utf-8") as f:
        f.write(_PARTIAL_LAST_LINE + "\n")

    JsonLinesAuditStore(path)  # a bare open: no recover, no verify_open

    # The damaged tail is untouched: no quarantine file, the partial line stays.
    assert list(tmp_path.glob("audit.jsonl.corrupt.*")) == []
    assert _PARTIAL_LAST_LINE in path.read_text(encoding="utf-8")


def test_a_read_only_open_of_a_tampered_file_does_not_abort(tmp_path: Path) -> None:
    """Given a chain with an in-place edit near the tail, when a read-only
    consumer opens the store and queries (without verify_open), then the open
    does not abort: integrity checking is the explicit, opt-in verify_open step
    the writing path takes, never a cost a reader pays (A5, ADR-031).
    """
    path, _ = _written_chain(tmp_path, count=5)
    lines = path.read_text(encoding="utf-8").splitlines()
    second_to_last = AuditEvent.model_validate_json(lines[3])
    lines[3] = second_to_last.model_copy(
        update={"payload": {"tampered": True}}
    ).model_dump_json()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    store = JsonLinesAuditStore(path)  # does not raise
    results = store.query()

    assert len(results) == 5


def test_open_seeds_and_verifies_without_a_full_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a multi-event chain, when the writing path recovers and verifies the
    tail, then it parses only the last K lines, never the whole file: open is
    O(K), so the tail-window's documented promise that open does not scan the
    trail holds (Sec-2, ADR-032).

    The two full-file readers are made to fail; a clean recover()+verify_open()
    that still seeds the correct head proves neither was used.
    """
    path, _ = _written_chain(tmp_path, count=6)
    store = JsonLinesAuditStore(path, tail_window=2)

    def _boom(*args: object, **kwargs: object) -> list[AuditEvent]:
        raise AssertionError("open must not read the whole file (Sec-2)")

    monkeypatch.setattr(store, "_read_all", _boom)
    monkeypatch.setattr(store, "_read_chain_with_tail_check", _boom)

    store.recover()  # seeds from the last K+1 lines only
    store.verify_open()  # verifies the last K+1 lines only

    assert store.head.sequence_number == 5


def test_open_for_writing_seeds_the_head_so_the_chain_continues(
    tmp_path: Path,
) -> None:
    """Given a chain written by an earlier store, when a writing store is opened
    via the open_for_writing factory, then its head is seeded from the last event
    on disk: the factory ran recover(), so the next append continues the chain
    rather than re-seeding genesis (M3, ADR-031).
    """
    path, events = _written_chain(tmp_path, count=4)

    store = JsonLinesAuditStore.open_for_writing(path)

    assert store.head.sequence_number == 3
    assert store.head.event_hash == events[-1].event_hash


def test_open_for_writing_aborts_on_a_tampered_tail(tmp_path: Path) -> None:
    """Given a chain with an in-place edit near the tail, when a writing store is
    opened via the factory, then it raises: the factory ran verify_open(), so a
    writing store cannot be assembled onto a tail that does not verify (M3). The
    bare constructor would not have aborted (the read path,
    test_a_read_only_open_of_a_tampered_file_does_not_abort); routing the writing
    path through the factory is what guarantees the check.
    """
    path, _ = _written_chain(tmp_path, count=5)
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = AuditEvent.model_validate_json(lines[3])
    lines[3] = tampered.model_copy(
        update={"payload": {"tampered": True}}
    ).model_dump_json()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(AuditLogError):
        JsonLinesAuditStore.open_for_writing(path)


def test_open_for_writing_recovers_before_it_verifies(tmp_path: Path) -> None:
    """Given a chain whose last line was partially written, when a writing store
    is opened via the factory, then it returns without raising and the damaged
    tail is quarantined: recover() healed the tail before verify_open() checked
    it, which pins the two steps to that order (M3, ADR-030). Were the order
    reversed, verify_open would choke on the unparseable last line and raise.
    """
    path = tmp_path / "audit.jsonl"
    seeding = JsonLinesAuditStore(path)
    seeding.publish(_make_event(einwendungs_id="EW-001"))
    with path.open("a", encoding="utf-8") as f:
        f.write(_PARTIAL_LAST_LINE + "\n")

    store = JsonLinesAuditStore.open_for_writing(path)

    # The recovery event sits at sequence 1, after the healed EW-001 at 0: the
    # head advanced through recover(), and verify_open() passed on the healed tail.
    assert store.head.sequence_number == 1
    assert _PARTIAL_LAST_LINE not in path.read_text(encoding="utf-8")
    assert len(list(tmp_path.glob("audit.jsonl.corrupt.*"))) == 1


def _chained_lines(payloads: list[dict]) -> list[str]:
    """Build correctly hash-chained EINGANG lines for the given payloads.

    Bypasses the store's write-entry schema gate by computing hashes directly,
    so a payload the gate would reject can still be written as a valid,
    integrity-sound line for a read-path tolerance test.
    """
    prev_hash = GENESIS_PREV_HASH
    lines: list[str] = []
    for seq, payload in enumerate(payloads):
        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            event_type=AuditEventType.EINGANG,
            einwendungs_id=f"EW-{seq:03d}",
            payload=payload,
            sequence_number=seq,
        )
        event = event.model_copy(
            update={"event_hash": compute_event_hash(event, prev_hash)}
        )
        prev_hash = event.event_hash  # type: ignore[assignment]
        lines.append(event.model_dump_json())
    return lines


def test_a_non_conforming_inner_line_does_not_fail_open(tmp_path: Path) -> None:
    """Given a chain whose interior line carries a payload the write-entry schema
    would reject (but correctly hash-chained), when the store is opened and the
    writing path recovers and verifies, then it does not fail: the schema is a
    write-entry gate, the read path is tolerant, and the hash chain (not a
    content rule) checks integrity (Sec-3, ADR-032).
    """
    path = tmp_path / "audit.jsonl"
    lines = _chained_lines(
        [
            {"document_id": "d0"},
            {"namen": ["Max Mustermann"]},  # declared on no event: write would reject
            {"document_id": "d2"},
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    store = JsonLinesAuditStore(path)
    store.recover()  # does not raise on the non-conforming payload
    store.verify_open()  # the hash chain is intact, so the tail verifies

    read_back = store.query()
    assert len(read_back) == 3
    assert read_back[1].payload == {"namen": ["Max Mustermann"]}
    assert verify_chain_file(path).ok

    # Integrity is still the hash chain's job, not the content rule's: break the
    # non-conforming line's recorded hash and the full walk surfaces it.
    broken = AuditEvent.model_validate_json(lines[1]).model_copy(
        update={"event_hash": "a" * 64}
    )
    path.write_text(
        "\n".join([lines[0], broken.model_dump_json(), lines[2]]) + "\n",
        encoding="utf-8",
    )
    assert not verify_chain_file(path).ok


def test_verify_chain_is_vacuously_ok_for_an_empty_chain(tmp_path: Path) -> None:
    """Given a fresh store with no events, when its chain is verified, then it is
    ok: an empty chain has nothing to break."""
    path = tmp_path / "audit.jsonl"
    JsonLinesAuditStore(path)

    assert verify_chain([]).ok
    assert verify_chain_file(path).ok


def test_head_reflects_the_last_appended_event(tmp_path: Path) -> None:
    """Given a chain of events, when the head is read, then it carries the last
    event's hash and sequence: the head is the external anchor value (ADR-031).
    """
    path, events = _written_chain(tmp_path, count=3)
    store = JsonLinesAuditStore(path)
    store.recover()  # seed the head from the events on disk

    assert store.head.sequence_number == 2
    assert store.head.event_hash == events[-1].event_hash


def test_head_of_a_fresh_chain_is_the_genesis_sentinel(tmp_path: Path) -> None:
    """A fresh store has the genesis sentinel as head hash and None as sequence:
    there is no chain to anchor yet, recorded honestly."""
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")

    assert store.head.event_hash == GENESIS_PREV_HASH
    assert store.head.sequence_number is None


def test_head_anchor_serializes_the_head_for_results_json(tmp_path: Path) -> None:
    """head_anchor produces a JSON-serializable chain_anchor block carrying the
    head hash and sequence, the value an eval run commits (ADR-031)."""
    path, events = _written_chain(tmp_path, count=3)
    store = JsonLinesAuditStore(path)
    store.recover()  # seed the head from the events on disk

    anchor = head_anchor(store.head)

    assert anchor == {
        "chain_anchor": {
            "head_hash": events[-1].event_hash,
            "head_sequence": 2,
        }
    }
    # It must round-trip through JSON, since it is written into results.json.
    assert json.loads(json.dumps(anchor)) == anchor


def test_results_with_anchor_merges_the_head_into_eval_results(
    tmp_path: Path,
) -> None:
    """results_with_anchor merges the chain_anchor block into the eval's own
    results without colliding with its metrics, the load-bearing anchor logic now
    under src/app and static analysis (A4, ADR-032)."""
    path, events = _written_chain(tmp_path, count=3)
    store = JsonLinesAuditStore(path)
    store.recover()

    document = results_with_anchor({"recall": 0.9, "precision": 0.95}, store.head)

    assert document["recall"] == 0.9
    assert document["chain_anchor"] == {
        "head_hash": events[-1].event_hash,
        "head_sequence": 2,
    }
    assert json.loads(json.dumps(document)) == document
