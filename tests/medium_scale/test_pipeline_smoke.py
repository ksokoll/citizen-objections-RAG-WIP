"""End-to-end smoke test for the wired pipeline."""

import re
from pathlib import Path

import pytest

from app.audit_log.store import JsonLinesAuditStore
from app.briefing.entities import BriefingStatus
from app.core import EinwendungsTyp
from app.core.events import AuditEventType
from app.core.failures import IngestionError
from app.core.results import TriageResult
from app.document_ingestion.service import DocumentIngestionService
from app.pipeline import Pipeline

SAMPLE_EINWENDUNG = (
    "Ein vorhabenbezogener Bebauungsplan, der von dieser "
    "Darstellung des Flächennutzungsplans abweicht. "
    "Die Öffentlichkeit wurde über grundlegende "
    "Planänderungen nicht frühzeitig unterrichtet."
)


class TestPipelineSmoke:
    def test_should_return_briefing_with_one_ready_entry(
        self,
        pipeline_and_audit: tuple[Pipeline, JsonLinesAuditStore],
    ) -> None:
        # Given a fully wired pipeline
        pipeline, _ = pipeline_and_audit

        # When the pipeline runs
        result = pipeline.run(SAMPLE_EINWENDUNG)

        # Then a briefing is returned with one BRIEFING_READY entry
        assert re.match(r"^[0-9a-f-]{36}$", result.document_id)
        assert result.einwendungs_typ == EinwendungsTyp.TYP_2.value
        assert len(result.entries) == 1
        assert result.entries[0].status == BriefingStatus.BRIEFING_READY
        assert result.entries[0].requires_case_context is True

    def test_should_emit_four_audit_events_in_order(
        self,
        pipeline_and_audit: tuple[Pipeline, JsonLinesAuditStore],
    ) -> None:
        # Given a fully wired pipeline
        pipeline, audit_store = pipeline_and_audit

        # When the pipeline runs
        pipeline.run(SAMPLE_EINWENDUNG)

        # Then the four pipeline-stage events are emitted in order
        events = audit_store.query()
        event_types = [e.event_type for e in events]
        assert event_types == [
            AuditEventType.EINGANG,
            AuditEventType.TRIAGE,
            AuditEventType.RETRIEVAL,
            AuditEventType.BRIEFING_ERSTELLT,
        ]

    def test_should_emit_kein_treffer_when_triage_returns_no_arguments(
        self,
        tmp_path: Path,
    ) -> None:
        # Given a pipeline where triage produces no arguments
        from tests.conftest import FakeLLMClient, FakePiiMasker, FakeRetriever

        from app.audit_log.service import AuditLogService
        from app.briefing.service import BriefingService
        from app.triage.service import TriageService

        class EmptyTriageService(TriageService):
            def triage(self, clean_text: str) -> TriageResult:
                return TriageResult(
                    einwendungs_typ=EinwendungsTyp.TYP_1,
                    extracted_arguments=[],
                )

        audit_store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
        pipeline = Pipeline(
            ingestion=DocumentIngestionService(
                raw_store_path=tmp_path / "raw",
                masker=FakePiiMasker(),
            ),
            triage=EmptyTriageService(llm=FakeLLMClient()),
            retrieval=FakeRetriever(source_revision="corpus-id-kein-treffer-test"),
            briefing=BriefingService(),
            audit=AuditLogService(store=audit_store),
        )

        # When the pipeline runs with text that yields no arguments
        result = pipeline.run("Kurzer Text.")

        # Then the briefing has no entries and KEIN_TREFFER is emitted
        assert result.entries == []
        assert audit_store.query()[-1].event_type == AuditEventType.KEIN_TREFFER

    def test_should_raise_ingestion_error_for_empty_input(
        self,
        pipeline_and_audit: tuple[Pipeline, JsonLinesAuditStore],
    ) -> None:
        # Given a fully wired pipeline
        pipeline, _ = pipeline_and_audit

        # When pipeline.run is called with empty string
        # Then IngestionError is raised
        with pytest.raises(IngestionError):
            pipeline.run("")
