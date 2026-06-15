"""Behaviour tests for the AuditEvent payload allowlist (ADR-031).

The hash chain is undeletable, and the right to erasure reaches only the raw
store; that coexistence holds only if the chain carries nothing erasure-bound.
The payload allowlist enforces that at construction: a value must be a
content-free simple type (or a flat container of them), or the AuditEvent is
rejected loudly. These tests pin the boundary and confirm the events the system
already emits still construct.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.core.events import MAX_PAYLOAD_STR_LEN, AuditEvent, AuditEventType


def _event(payload: dict) -> AuditEvent:
    return AuditEvent(
        event_id=str(uuid.uuid4()),
        event_type=AuditEventType.EINGANG,
        einwendungs_id="EW-001",
        payload=payload,
    )


def test_a_simple_scalar_payload_is_accepted() -> None:
    """Given a payload of ints, floats, bools, and short strings, when the event
    is constructed, then it is accepted: counts, scores, flags, and ids are the
    content-free data the chain is meant to carry.
    """
    event = _event(
        {
            "argument_count": 3,
            "confidence": 0.95,
            "contradiction_detected": True,
            "model": "mistral-large-latest",
        }
    )

    assert event.payload["argument_count"] == 3
    assert event.payload["contradiction_detected"] is True


def test_a_flat_counts_map_is_accepted() -> None:
    """Given a payload carrying a flat dict of counts (the EINGANG event's
    masked_entity_counts shape), when the event is constructed, then it is
    accepted: a one-level map of simple leaves stays content-free.
    """
    event = _event({"masked_entity_counts": {"PERSON": 3, "EMAIL_ADDRESS": 1}})

    assert event.payload["masked_entity_counts"]["PERSON"] == 3


def test_a_long_string_is_rejected_at_construction() -> None:
    """Given a payload string longer than the bound (a text snippet), when the
    event is constructed, then it is rejected loudly: a fragment of objection
    text must not enter the tamper-evident chain (ADR-031).
    """
    snippet = "x" * (MAX_PAYLOAD_STR_LEN + 1)

    with pytest.raises(ValidationError, match="content-free"):
        _event({"original_zitat": snippet})


def test_a_non_simple_type_is_rejected_at_construction() -> None:
    """Given a payload value that is neither a simple leaf nor a flat container
    (here None), when the event is constructed, then it is rejected loudly.
    """
    with pytest.raises(ValidationError, match="content-free"):
        _event({"detail": None})


def test_a_nested_container_is_rejected_at_construction() -> None:
    """Given a payload with a container nested inside a container, when the event
    is constructed, then it is rejected: the allowlist permits one flat level
    only, so deep structure cannot smuggle content in.
    """
    with pytest.raises(ValidationError, match="content-free"):
        _event({"nested": {"inner": {"deep": 1}}})


def test_a_list_of_simple_leaves_is_accepted_but_a_list_of_lists_is_not() -> None:
    """A flat list of leaves is content-free and accepted; a list containing a
    list is rejected, matching the dict rule (one flat level only).
    """
    assert _event({"sequence_numbers": [0, 1, 2]}).payload["sequence_numbers"] == [
        0,
        1,
        2,
    ]

    with pytest.raises(ValidationError, match="content-free"):
        _event({"matrix": [[1, 2], [3, 4]]})


def test_the_recovery_event_payload_shape_still_constructs() -> None:
    """The store's recovery event payload (a hash plus a count) is content-free
    and still constructs under the allowlist, so the 18b recovery path is
    unaffected (ADR-030, ADR-031).
    """
    event = _event({"quarantined_hash": "a" * 64, "quarantined_lines": 1})

    assert len(event.payload["quarantined_hash"]) == 64
