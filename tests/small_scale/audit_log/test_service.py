"""Behaviour tests for AuditLogService.

The service is the audit context's application layer. record_startup_config is
the one method that constructs a custody event rather than delegating one: it
owns the STARTKONFIGURATION event's shape (type, system sentinel, fresh id) so
the wiring layer passes provenance only (A3, ADR-031).
"""

from __future__ import annotations

from pathlib import Path

from app.audit_log.service import AuditLogService
from app.audit_log.store import JsonLinesAuditStore
from app.core.events import SYSTEM_EINWENDUNGS_ID, AuditEventType


def _provenance() -> dict[str, object]:
    """A content-free provenance dict shaped like the CLI's startup_config."""
    return {
        "git_sha": "abc1234",
        "package_versions": {"structlog": "26.1.0"},
        "allowlist_size": 12,
        "tracing_enabled": False,
        "log_format": "json",
        "model_id": "mistral-large-latest",
    }


def test_record_startup_config_publishes_a_system_genesis_event(tmp_path: Path) -> None:
    """Given a fresh store, when the service records the startup config, then a
    single STARTKONFIGURATION event under the SYSTEM sentinel is the chain
    genesis (sequence 0), carrying exactly the provenance the CLI passed (A3).

    The service constructs the custody event; the caller supplies only the
    content-free provenance, so audit-schema knowledge stays in the audit
    context, not the composition root.
    """
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    service = AuditLogService(store=store)
    provenance = _provenance()

    service.record_startup_config(provenance)

    events = store.query(event_type=AuditEventType.STARTKONFIGURATION)
    assert len(events) == 1
    event = events[0]
    assert event.einwendungs_id == SYSTEM_EINWENDUNGS_ID
    assert event.sequence_number == 0
    assert event.payload == provenance


def test_record_startup_config_does_not_retain_the_callers_dict(
    tmp_path: Path,
) -> None:
    """Given a provenance dict, when the service records it and the caller then
    mutates its own dict, then the recorded event is unaffected: the service
    copies the payload rather than aliasing the caller's mapping.
    """
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    service = AuditLogService(store=store)
    provenance = _provenance()

    service.record_startup_config(provenance)
    provenance["git_sha"] = "mutated_after_the_fact"

    [event] = store.query(event_type=AuditEventType.STARTKONFIGURATION)
    assert event.payload["git_sha"] == "abc1234"
