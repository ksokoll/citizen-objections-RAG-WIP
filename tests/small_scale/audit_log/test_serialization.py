"""Unit tests for the canonical AuditEvent serializer and its version dispatch.

Canonical bytes are the precondition for the hash chain being a proof: the
verify path must reproduce the exact bytes the write path hashed. These tests
guard determinism, the frozen v1 byte form, and the version dispatch (ADR-024).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.audit_log.serialization import (
    UnknownSerializationVersionError,
    _canonical_content_v1,
    canonical_bytes,
)
from app.core.events import AuditEvent, AuditEventType


def _event(**overrides: object) -> AuditEvent:
    """Build a fully-pinned v1 event, overriding only the fields a test varies."""
    fields: dict[str, object] = {
        "event_id": "11111111-1111-1111-1111-111111111111",
        "event_type": AuditEventType.EINGANG,
        "einwendungs_id": "EW-001",
        "timestamp": datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
        "payload": {"model": "mistral", "confidence": 0.95},
        "sequence_number": 0,
    }
    fields.update(overrides)
    return AuditEvent(**fields)


def test_serializing_the_same_event_twice_is_byte_identical() -> None:
    """Given one event, when serialized twice, then the bytes are identical.

    Determinism is the foundation of the chain: the verify path recomputes the
    bytes the write path hashed, so a serializer that varied between calls would
    report a break that never happened.
    """
    event = _event()

    assert canonical_bytes(event) == canonical_bytes(event)


def test_same_content_in_different_payload_order_is_byte_identical() -> None:
    """Given two events whose payloads differ only in key insertion order, when
    each is serialized, then the bytes are identical.

    The payload is a free-form dict; insertion order is not part of the event's
    logical identity. Canonical bytes must depend on content alone, or two
    recordings of the same event would hash differently.
    """
    ordered = _event(payload={"alpha": 1, "beta": 2, "gamma": 3})
    shuffled = _event(payload={"gamma": 3, "alpha": 1, "beta": 2})

    assert canonical_bytes(ordered) == canonical_bytes(shuffled)


def test_v1_serializer_reproduces_frozen_golden_bytes() -> None:
    """Given a fixed v1 event, when serialized, then the bytes match the frozen
    golden form exactly.

    The golden bytes are the regression guard. An accidental format change (key
    order, separator whitespace, unicode escaping, a field rename, the timestamp
    rendering) would silently break every hash computed before the change while
    every other test stayed green. Freezing the exact bytes turns such a drift
    into a failure here instead of an undetectable broken proof.
    """
    event = _event()

    golden = (
        b'{"einwendungs_id":"EW-001",'
        b'"event_id":"11111111-1111-1111-1111-111111111111",'
        b'"event_type":"eingang",'
        b'"payload":{"confidence":0.95,"model":"mistral"},'
        b'"sequence_number":0,'
        b'"serialization_version":1,'
        b'"timestamp":"2024-06-15T12:00:00Z"}'
    )

    assert canonical_bytes(event) == golden


def test_event_hash_is_excluded_from_canonical_bytes() -> None:
    """Given two events identical but for event_hash, when serialized, then the
    bytes are identical.

    event_hash is the output of hashing the canonical bytes; feeding it back in
    would be circular. The serializer must ignore it so the write path can hash
    the content and the verify path can recompute the same hash from it.
    """
    without_hash = _event(event_hash=None)
    with_hash = _event(event_hash="deadbeef" * 8)

    assert canonical_bytes(without_hash) == canonical_bytes(with_hash)


def test_sequence_number_is_part_of_the_canonical_bytes() -> None:
    """Given two events identical but for sequence_number, when serialized, then
    the bytes differ.

    The sequence number fixes an event's position in the chain. If it were not
    hashed, two events could be reordered without changing their bytes and the
    chain would still verify, which would defeat the ordering guarantee.
    """
    first = _event(sequence_number=0)
    second = _event(sequence_number=1)

    assert canonical_bytes(first) != canonical_bytes(second)


def test_version_one_dispatches_to_the_v1_serializer() -> None:
    """Given a v1 event, when serialized via the dispatcher, then the result
    matches the v1 serializer called directly.

    Dispatch by serialization_version is what lets a future v2 still verify v1
    events: the version field, not the current code, decides the byte form. This
    pins that v1 routes to the v1 serializer today.
    """
    event = _event()

    assert canonical_bytes(event) == _canonical_content_v1(event)


def test_unknown_serialization_version_raises_clearly() -> None:
    """Given an event whose serialization_version has no serializer, when
    canonical_bytes is called, then it raises UnknownSerializationVersionError.

    A missing serializer must fail loud, not fall back to some default byte
    form: a silent fallback would hash a different shape than the version
    promises and surface later as a phantom chain break.
    """
    event = _event(serialization_version=999)

    with pytest.raises(UnknownSerializationVersionError, match="999"):
        canonical_bytes(event)
