"""Behaviour tests for the per-event payload schema (Form B, ADR-032).

The chain stays content-free by positive declaration, not a length bound: each
event type declares its payload keys and types, and the store enforces them at
write entry. These tests pin the declaration boundary directly (validate_payload)
and through the store (publish), and confirm the events the system emits still
write. {"namen": [...]} is the canonical fragment a length heuristic let through
and a key allowlist does not.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.audit_log.payload_schema import (
    PAYLOAD_SCHEMAS,
    PayloadSchemaError,
    validate_payload,
)
from app.audit_log.store import JsonLinesAuditStore
from app.core.events import AuditEvent, AuditEventType


def _event(event_type: AuditEventType, payload: dict) -> AuditEvent:
    return AuditEvent(
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        einwendungs_id="EW-001",
        payload=payload,
    )


def test_every_event_type_declares_a_schema() -> None:
    """Given the event-type enum, when the schema table is checked, then every
    member has a declared schema: a new event type fails closed (no payload may
    pass) rather than silently admitting an undeclared payload.
    """
    assert set(PAYLOAD_SCHEMAS) == set(AuditEventType)


def test_a_declared_key_with_its_declared_type_passes() -> None:
    """The EINGANG event's declared keys (a document id string and a flat counts
    map) pass: these are the content-free fields the chain is meant to carry.
    """
    validate_payload(
        AuditEventType.EINGANG,
        {"document_id": "doc-123", "masked_entity_counts": {"PERSON": 3}},
    )


def test_a_flat_counts_map_passes_as_a_declared_dict_str_int() -> None:
    """masked_entity_counts is declared as a flat dict[str, int], so a counts map
    of string labels to integer counts passes; an empty map passes too (the
    pass-through masker's result).
    """
    validate_payload(AuditEventType.EINGANG, {"masked_entity_counts": {}})
    validate_payload(
        AuditEventType.EINGANG,
        {"masked_entity_counts": {"PERSON": 3, "EMAIL_ADDRESS": 1}},
    )


def test_an_undeclared_key_is_rejected() -> None:
    """Given a payload key declared on no event ("namen"), when validated against
    EINGANG, then it is rejected: the canonical text fragment a length heuristic
    let through cannot enter, because the gate is a key allowlist (ADR-032).
    """
    with pytest.raises(PayloadSchemaError, match="namen"):
        validate_payload(AuditEventType.EINGANG, {"namen": ["Max Mustermann"]})


def test_a_declared_key_with_a_wrong_type_is_rejected() -> None:
    """Given a declared key carrying the wrong type (a count map with a string
    value where int is declared), when validated, then it is rejected: the
    declaration binds the type, not only the key.
    """
    with pytest.raises(PayloadSchemaError, match="masked_entity_counts"):
        validate_payload(
            AuditEventType.EINGANG, {"masked_entity_counts": {"PERSON": "three"}}
        )


def test_a_nested_container_under_a_flat_dict_key_is_rejected() -> None:
    """A FlatDict admits one level only: a dict whose value is itself a dict is
    rejected, so depth cannot smuggle structure (and content) into the chain.
    """
    with pytest.raises(PayloadSchemaError, match="masked_entity_counts"):
        validate_payload(
            AuditEventType.EINGANG, {"masked_entity_counts": {"PERSON": {"x": 1}}}
        )


def test_an_int_key_rejects_a_bool_and_a_bool_key_rejects_an_int() -> None:
    """bool is an int subclass, but a count and a flag are distinct: a declared
    int rejects a bool (RETRIEVAL's resolved_norm_count) and a declared bool
    rejects an int (TRIAGE's contradiction_detected), so neither quietly
    substitutes for the other in the proof.
    """
    with pytest.raises(PayloadSchemaError, match="resolved_norm_count"):
        validate_payload(AuditEventType.RETRIEVAL, {"resolved_norm_count": True})
    with pytest.raises(PayloadSchemaError, match="contradiction_detected"):
        validate_payload(AuditEventType.TRIAGE, {"contradiction_detected": 1})


def test_the_emitted_events_payloads_all_validate() -> None:
    """The payloads the pipeline and store emit all pass their schemas, so write
    entry enforcement does not break a single event the system already writes.
    """
    validate_payload(
        AuditEventType.TRIAGE,
        {
            "argument_count": 2,
            "contradiction_detected": True,
            "substance_threshold_exceeded": False,
        },
    )
    validate_payload(AuditEventType.RETRIEVAL, {"resolved_norm_count": 4})
    validate_payload(AuditEventType.BRIEFING_ERSTELLT, {"entry_count": 1})
    validate_payload(AuditEventType.KEIN_TREFFER, {"entry_count": 0})
    validate_payload(AuditEventType.PIPELINE_FEHLER, {"reason": "pipeline error"})
    validate_payload(
        AuditEventType.WIEDERHERSTELLUNG,
        {"quarantined_hash": "a" * 64, "quarantined_lines": 1},
    )
    validate_payload(
        AuditEventType.ROHDOKUMENT_ZUGRIFF,
        {"document_id": "doc-1"},
    )


def test_read_access_event_carries_only_the_content_free_document_id() -> None:
    """The read-access event declares document_id and nothing that could carry
    content (ADR-033): the document_id string passes, and a content key like the
    document text is rejected at the declaration boundary, so the read-access
    record stays content-free like every other chain event.
    """
    validate_payload(AuditEventType.ROHDOKUMENT_ZUGRIFF, {"document_id": "doc-1"})

    with pytest.raises(PayloadSchemaError, match="content"):
        validate_payload(
            AuditEventType.ROHDOKUMENT_ZUGRIFF,
            {"document_id": "doc-1", "content": "Originaltext der Einwendung"},
        )


def test_publish_rejects_an_undeclared_payload_key_at_write_entry(
    tmp_path: Path,
) -> None:
    """Given an event whose payload carries an undeclared key, when it is
    published, then the store rejects it at write entry before anything reaches
    disk: the chain stays content-free by construction, not by a read-time check
    (ADR-032).
    """
    path = tmp_path / "audit.jsonl"
    store = JsonLinesAuditStore(path)

    with pytest.raises(PayloadSchemaError, match="namen"):
        store.publish(_event(AuditEventType.EINGANG, {"namen": ["Max Mustermann"]}))

    # Nothing was written: the rejection precedes the durable append.
    assert path.read_text(encoding="utf-8") == ""


def test_publish_accepts_a_declared_payload_at_write_entry(tmp_path: Path) -> None:
    """Given an event whose payload is fully declared, when it is published, then
    it is appended normally: write entry enforcement gates undeclared content,
    not the events the system actually emits.
    """
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")

    store.publish(
        _event(
            AuditEventType.EINGANG,
            {"document_id": "doc-1", "masked_entity_counts": {"PERSON": 2}},
        )
    )

    [stored] = store.query()
    assert stored.payload["masked_entity_counts"]["PERSON"] == 2
