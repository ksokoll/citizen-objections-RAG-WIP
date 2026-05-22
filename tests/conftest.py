"""Shared test fakes and fixtures for the citizen-objections-RAG test suite."""

from pathlib import Path

import pytest

from citizen_objections_rag.audit_log.service import AuditLogService
from citizen_objections_rag.audit_log.store import JsonLinesAuditStore
from citizen_objections_rag.core.entities import RetrievedChunk
from citizen_objections_rag.document_ingestion.service import DocumentIngestionService
from citizen_objections_rag.pipeline import Pipeline
from citizen_objections_rag.response_drafting.service import ResponseDraftingService
from citizen_objections_rag.triage.service import TriageService


class FakeLLMClient:
    """Fake LLMClient returning a fixed Würdigung string.

    Used by ResponseDrafting stub. TriageService._extract_arguments()
    is independently stubbed and does not call the LLM in the skeleton.
    """

    FIXED_RESPONSE = "Skeleton-Würdigung: Dies ist ein Platzhalter."

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        return self.FIXED_RESPONSE


class FakeRetriever:
    """Fake Retriever returning an empty chunk list."""

    def retrieve(
        self, query_embedding: list[float], top_k: int = 5
    ) -> list[RetrievedChunk]:
        return []


@pytest.fixture()
def pipeline_and_audit(tmp_path: Path) -> tuple[Pipeline, JsonLinesAuditStore]:
    """Fully wired pipeline with stubbed LLM and retriever."""
    audit_store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    pipeline = Pipeline(
        ingestion=DocumentIngestionService(raw_store_path=tmp_path / "raw"),
        triage=TriageService(llm=FakeLLMClient()),
        drafting=ResponseDraftingService(
            llm=FakeLLMClient(),
            retriever=FakeRetriever(),
            model_version="skeleton-v0.1",
        ),
        audit=AuditLogService(store=audit_store),
    )
    return pipeline, audit_store
