"""Unit tests for JsonLinesAuditStore."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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


def test_publish_duplicate_event_id_raises(tmp_path: Path) -> None:
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    event = _make_event()
    store.publish(event)

    with pytest.raises(AuditLogError, match="Duplicate event_id"):
        store.publish(event)


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
        event_type=AuditEventType.ENTWURF_GENERIERT,
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
