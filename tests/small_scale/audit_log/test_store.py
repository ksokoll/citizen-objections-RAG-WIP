"""Unit tests for JsonLinesAuditStore."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.audit_log.serialization import GENESIS_PREV_HASH, compute_event_hash
from app.audit_log.store import JsonLinesAuditStore
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


def test_fsync_precedes_the_head_advance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a publish, when its fsync runs, then the in-memory head still points
    at the previous event; only after the publish returns has it advanced.

    fsync-before-head-advance is the durability invariant (ADR-030): the head is
    the claim that an event exists, so it must not advance until the bytes are on
    stable storage. Observing the head at fsync time is the seam that proves the
    order.
    """
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    store.publish(_make_event(einwendungs_id="EW-001"))  # head is now at seq 0

    captured: dict[str, int | None] = {}
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        # The head must not yet reflect the event whose bytes we are fsyncing.
        captured["head_sequence_at_fsync"] = store._head_sequence
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    store.publish(_make_event(einwendungs_id="EW-002"))  # should advance to seq 1

    assert captured["head_sequence_at_fsync"] == 0
    assert store._head_sequence == 1


def test_a_write_failing_before_fsync_leaves_the_head_unadvanced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a publish whose fsync fails, when it raises, then the head has not
    advanced: a failed durable append leaves the in-memory chain level with disk,
    never ahead of it (ADR-030).
    """
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    store.publish(_make_event(einwendungs_id="EW-001"))
    head_sequence_before = store._head_sequence
    head_hash_before = store._head_hash

    def failing_fsync(fd: int) -> None:
        raise OSError("simulated fsync failure before the head advances")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(AuditLogError):
        store.publish(_make_event(einwendungs_id="EW-002"))

    assert store._head_sequence == head_sequence_before
    assert store._head_hash == head_hash_before


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
        payload={"confidence": 0.95, "model": "claude-3"},
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
