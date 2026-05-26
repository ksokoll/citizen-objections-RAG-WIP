"""Shared test fakes and fixtures for the citizen-objections-RAG test suite."""

from pathlib import Path
from typing import Any

import pytest

# tests/external/conftest.py
from dotenv import load_dotenv
from pydantic import BaseModel

from app.audit_log.service import AuditLogService
from app.audit_log.store import JsonLinesAuditStore
from app.core.entities import RetrievedChunk
from app.core.statuses import EinwendungsTyp
from app.document_ingestion.service import DocumentIngestionService
from app.pipeline import Pipeline
from app.response_drafting.service import ResponseDraftingService
from app.triage.llm_schema import LLMArgument, LLMTriageOutput
from app.triage.service import TriageService

load_dotenv()


class FakeLLMClient:
    """Test double for the LLMClient protocol.

    Both response values are public attributes that tests set explicitly
    before invoking the service under test. No call argument validation,
    no message history.
    """

    def __init__(
        self,
        generate_response: str = "",
        parse_response: BaseModel | None = None,
    ) -> None:
        self.generate_response = generate_response
        self.parse_response = parse_response
        self.parse_calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        return self.generate_response

    def parse(
        self,
        prompt: str,
        response_format: type[BaseModel],
        system_prompt: str = "",
    ) -> BaseModel:
        self.parse_calls.append(
            {
                "prompt": prompt,
                "response_format": response_format,
                "system_prompt": system_prompt,
            }
        )
        if self.parse_response is None:
            raise RuntimeError(
                "FakeLLMClient.parse called but no parse_response configured. "
                "Set fake.parse_response = LLMTriageOutput(...) in the test setup."
            )
        return self.parse_response


class FakeRetriever:
    """Fake Retriever returning an empty chunk list."""

    def retrieve(
        self, query: str, partition: str, top_k: int = 5
    ) -> list[RetrievedChunk]:
        return []


# Default LLMTriageOutput for pipeline-level fixtures: a single TYP_2 argument
# whose original_zitat is a substring of the smoke-test SAMPLE_EINWENDUNG.
# Pre-configuring this on the triage FakeLLMClient keeps the smoke test
# focused on pipeline orchestration rather than LLM-double setup.
_DEFAULT_TRIAGE_OUTPUT = LLMTriageOutput(
    argumente=[
        LLMArgument(
            catalog_id="baugb",
            einwendungs_typ=EinwendungsTyp.TYP_2,
            argument_text="Widerspruch zum Flächennutzungsplan",
            original_zitat=(
                "Ein vorhabenbezogener Bebauungsplan, der von dieser "
                "Darstellung des Flächennutzungsplans abweicht."
            ),
        ),
    ]
)


@pytest.fixture()
def pipeline_and_audit(tmp_path: Path) -> tuple[Pipeline, JsonLinesAuditStore]:
    """Fully wired pipeline with stubbed LLM and retriever.

    The triage FakeLLMClient is preloaded with a single-argument
    LLMTriageOutput whose original_zitat matches the smoke-test sample
    text; the drafting FakeLLMClient returns an empty string from
    generate(), which is sufficient for the orchestration smoke tests.
    """
    audit_store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    triage_llm = FakeLLMClient(parse_response=_DEFAULT_TRIAGE_OUTPUT)
    drafting_llm = FakeLLMClient(generate_response="Skeleton-Würdigung.")
    pipeline = Pipeline(
        ingestion=DocumentIngestionService(raw_store_path=tmp_path / "raw"),
        triage=TriageService(llm=triage_llm),
        drafting=ResponseDraftingService(
            llm=drafting_llm,
            retriever=FakeRetriever(),
            model_version="skeleton-v0.1",
        ),
        audit=AuditLogService(store=audit_store),
    )
    return pipeline, audit_store
