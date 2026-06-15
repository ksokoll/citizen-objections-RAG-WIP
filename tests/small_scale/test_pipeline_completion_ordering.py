"""Behaviour tests for the end-to-end completion ordering (ADR-033).

The return value of run() is the system's claim that an objection was processed
and recorded. Fail-closed plus the durable-append guarantee (fsync before
head-advance, ADR-030) make that claim never precede its evidence: the
completion custody event is durably appended before the briefing is returned,
and a completion-write failure returns no briefing at all. These tests pin both
halves through a store that records its appends and can fail one event type.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.audit_log.service import AuditLogService
from app.audit_log.store import JsonLinesAuditStore
from app.briefing.service import BriefingService
from app.core.events import AuditEvent, AuditEventType
from app.core.failures import AuditLogError
from app.document_ingestion.service import DocumentIngestionService
from app.pipeline import Pipeline
from app.triage.service import TriageService
from tests.conftest import (
    _DEFAULT_TRIAGE_OUTPUT,
    FakeLLMClient,
    FakePiiMasker,
    FakeRetriever,
)

_SAMPLE_EINWENDUNG = (
    "Ein vorhabenbezogener Bebauungsplan, der von dieser "
    "Darstellung des Flächennutzungsplans abweicht. "
    "Die Öffentlichkeit wurde über grundlegende "
    "Planänderungen nicht frühzeitig unterrichtet."
)


class _RecordingAuditStore:
    """Wraps a real store, recording durable appends; can fail one event type.

    Records each event type that reaches disk, in append order, so a test can
    assert the completion event was the last durable append before run()
    returned. With fail_on set, publish raises AuditLogError for that one event
    type before it reaches the backing store, so nothing is written for it: the
    completion-write-failure path, where run() must return no briefing.
    """

    def __init__(
        self,
        backing: JsonLinesAuditStore,
        fail_on: AuditEventType | None = None,
    ) -> None:
        self._backing = backing
        self._fail_on = fail_on
        self.appended: list[AuditEventType] = []

    def publish(self, event: AuditEvent) -> None:
        if self._fail_on is not None and event.event_type == self._fail_on:
            raise AuditLogError("simulated completion write failure")
        self._backing.publish(event)
        self.appended.append(event.event_type)

    def query(self, **kwargs: object) -> list[AuditEvent]:
        return self._backing.query(**kwargs)


def _pipeline_with(tmp_path: Path, store: _RecordingAuditStore) -> Pipeline:
    """Wire a pipeline whose audit publisher is the recording store under test."""
    return Pipeline(
        ingestion=DocumentIngestionService(
            raw_store_path=tmp_path / "raw",
            masker=FakePiiMasker(),
        ),
        triage=TriageService(llm=FakeLLMClient(parse_response=_DEFAULT_TRIAGE_OUTPUT)),
        retrieval=FakeRetriever(),
        briefing=BriefingService(),
        audit=AuditLogService(store=store),
    )


def test_completion_event_is_durable_before_the_briefing_is_returned(
    tmp_path: Path,
) -> None:
    """The completion event is the last durable append, on disk before return.

    Given a run with a healthy audit store, when run() returns the briefing,
    then the completion event (BRIEFING_ERSTELLT) was the last event appended to
    the chain and is already queryable on disk for the returned document: the
    return never precedes the completion proof (ADR-033).
    """
    backing = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    recording = _RecordingAuditStore(backing)

    briefing = _pipeline_with(tmp_path, recording).run(_SAMPLE_EINWENDUNG)

    assert briefing is not None
    assert recording.appended[-1] == AuditEventType.BRIEFING_ERSTELLT
    completion = backing.query(event_type=AuditEventType.BRIEFING_ERSTELLT)
    assert len(completion) == 1
    assert completion[0].einwendungs_id == briefing.document_id


def test_a_completion_write_failure_returns_no_briefing(
    tmp_path: Path,
) -> None:
    """A failed completion write aborts run(): no briefing, no completion on disk.

    Given a store that fails exactly on the completion event, when run()
    reaches it, then the AuditLogError propagates (no briefing is returned) and
    the chain holds the earlier custody events but not the completion event:
    there is no output whose completion proof the disk lacks (ADR-033).
    """
    backing = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    recording = _RecordingAuditStore(backing, fail_on=AuditEventType.BRIEFING_ERSTELLT)

    with pytest.raises(AuditLogError):
        _pipeline_with(tmp_path, recording).run(_SAMPLE_EINWENDUNG)

    assert backing.query(event_type=AuditEventType.RETRIEVAL)
    assert backing.query(event_type=AuditEventType.BRIEFING_ERSTELLT) == []
