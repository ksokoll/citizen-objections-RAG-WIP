"""End-to-end smoke test for the walking skeleton pipeline."""

import re
from pathlib import Path

import pytest

from app.audit_log.store import JsonLinesAuditStore
from app.core.events import AuditEventType
from app.core.failures import IngestionError
from app.core.results import TriageResult
from app.core.statuses import AbwaegungsStatus, EinwendungsTyp, WuerdigungsStatus
from app.pipeline import Pipeline

SAMPLE_EINWENDUNG = (
    "Ein vorhabenbezogener Bebauungsplan, der von dieser "
    "Darstellung des Flächennutzungsplans abweicht. "
    "Die Öffentlichkeit wurde über grundlegende "
    "Planänderungen nicht frühzeitig unterrichtet."
)


class TestPipelineSmoke:
    def test_should_return_draft_with_all_reproducibility_fields(
        self,
        pipeline_and_audit: tuple[Pipeline, JsonLinesAuditStore],
    ) -> None:
        # Given a fully wired pipeline
        pipeline, _ = pipeline_and_audit

        # When the pipeline runs
        result = pipeline.run(SAMPLE_EINWENDUNG)

        # Then status is DRAFT with all reproducibility fields set
        assert result.status == AbwaegungsStatus.DRAFT
        assert re.match(r"^[0-9a-f-]{36}$", result.einwendungs_id)
        assert result.model_version == "skeleton-v0.1"
        assert result.prompt_version.startswith("1.")
        assert len(result.retrieval_config_hash) >= 4

    def test_should_emit_three_audit_events_in_order(
        self,
        pipeline_and_audit: tuple[Pipeline, JsonLinesAuditStore],
    ) -> None:
        # Given a fully wired pipeline
        pipeline, audit_store = pipeline_and_audit

        # When the pipeline runs
        pipeline.run(SAMPLE_EINWENDUNG)

        # Then three audit events are emitted in correct order
        events = audit_store.query()
        assert len(events) == 3
        event_types = [e.event_type for e in events]
        assert event_types[0] == AuditEventType.EINGANG
        assert event_types[1] == AuditEventType.TRIAGE
        assert event_types[2] in {
            AuditEventType.ENTWURF_GENERIERT,
            AuditEventType.KEIN_TREFFER,
        }

    def test_should_return_kein_treffer_when_triage_returns_no_arguments(
        self,
        tmp_path: Path,
    ) -> None:
        # Given a pipeline where triage produces no arguments (empty text)
        from tests.conftest import FakeLLMClient, FakeRetriever

        from app.audit_log.service import AuditLogService
        from app.audit_log.store import JsonLinesAuditStore
        from app.document_ingestion.service import (
            DocumentIngestionService,
        )
        from app.response_drafting.service import (
            ResponseDraftingService,
        )
        from app.triage.service import TriageService

        class EmptyTriageService(TriageService):
            def triage(self, clean_text: str) -> TriageResult:
                return TriageResult(
                    einwendungs_typ=EinwendungsTyp.TYP_1,
                    extracted_arguments=[],
                )

        pipeline = Pipeline(
            ingestion=DocumentIngestionService(raw_store_path=tmp_path / "raw"),
            triage=EmptyTriageService(llm=FakeLLMClient()),
            drafting=ResponseDraftingService(
                llm=FakeLLMClient(),
                retriever=FakeRetriever(),
                model_version="skeleton-v0.1",
            ),
            audit=AuditLogService(store=JsonLinesAuditStore(tmp_path / "audit.jsonl")),
        )

        # When the pipeline runs with text that yields no arguments
        result = pipeline.run("Kurzer Text.")

        # Then wuerdigungs_status is KEIN_TREFFER
        assert result.wuerdigungs_status == WuerdigungsStatus.KEIN_TREFFER

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
