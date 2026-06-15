"""Behaviour tests for the minimal AuditEvent model contract (Form B, ADR-032).

The content-free policy moved out of the kernel: the AuditEvent model carries a
payload without constraining its shape, and the audit context governs and
enforces that shape at write entry (see
tests/small_scale/audit_log/test_payload_schema.py). These tests pin what the
kernel still owns: a payload of any shape constructs, and the id invariant
holds. The read path's tolerance rests on this: parsing a historical line back
into an AuditEvent must never fail on its payload content.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.core.events import AuditEvent, AuditEventType


def _event(payload: dict) -> AuditEvent:
    return AuditEvent(
        event_id=str(uuid.uuid4()),
        event_type=AuditEventType.EINGANG,
        einwendungs_id="EW-001",
        payload=payload,
    )


def test_the_kernel_carries_any_payload_shape_without_validating_it() -> None:
    """Given a payload the audit context would reject (an undeclared key, a deep
    nesting, a long string), when the AuditEvent is constructed, then it is
    accepted: the kernel does not enforce the content-free policy, the audit
    context does, at write entry (Form B, ADR-032).

    This is what lets the read path stay tolerant: reconstructing a historical
    or non-conforming line into an AuditEvent must not fail on its payload.
    """
    event = _event(
        {
            "namen": ["Max Mustermann"],
            "deep": {"inner": {"deeper": 1}},
            "long": "x" * 500,
        }
    )

    assert event.payload["namen"] == ["Max Mustermann"]
    assert event.payload["deep"]["inner"]["deeper"] == 1


def test_an_empty_payload_is_the_default() -> None:
    """An event constructed without a payload carries an empty dict, the honest
    default for a custody event that records only its type and position."""
    event = AuditEvent(
        event_id=str(uuid.uuid4()),
        event_type=AuditEventType.FREIGABE,
        einwendungs_id="EW-001",
    )

    assert event.payload == {}


def test_an_empty_id_is_rejected_at_construction() -> None:
    """The one kernel invariant on the model: an event_id or einwendungs_id that
    is empty or whitespace is rejected, because a custody record must reference a
    real id (its objection or the system sentinel)."""
    with pytest.raises(ValidationError, match="must not be empty"):
        AuditEvent(
            event_id="   ",
            event_type=AuditEventType.EINGANG,
            einwendungs_id="EW-001",
        )
