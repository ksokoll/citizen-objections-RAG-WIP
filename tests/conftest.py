"""Shared test fakes and fixtures for the citizen-objections-RAG test suite."""

from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv
from pydantic import BaseModel

from app.audit_log.service import AuditLogService
from app.audit_log.store import JsonLinesAuditStore
from app.briefing.service import BriefingService
from app.core import EinwendungsTyp
from app.core.events import AuditEvent, AuditEventType
from app.core.failures import AuditLogError
from app.document_ingestion.entities import MaskingResult
from app.document_ingestion.service import DocumentIngestionService
from app.pipeline import Pipeline
from app.retrieval.entities import NormWithSource
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


class FakePiiMasker:
    """Test double for the PiiMasker protocol.

    Deterministic and free of spaCy or Presidio. By default it is a
    pass-through (returns the text unchanged, empty counts), which suits
    pipeline and smoke fixtures where masking is not the subject under test.

    Tests that exercise masking behaviour configure a replacements map of
    {string_to_mask: placeholder}; each occurrence of a key is replaced by
    its placeholder, and entity_counts records one count per replaced
    occurrence keyed by the placeholder label without brackets (e.g. "NAME").
    The mask calls are recorded for tests that assert the masker was invoked.
    """

    def __init__(self, replacements: dict[str, str] | None = None) -> None:
        self.replacements = replacements or {}
        self.mask_calls: list[str] = []

    def mask(self, text: str) -> MaskingResult:
        self.mask_calls.append(text)
        masked = text
        counts: dict[str, int] = {}
        for target, placeholder in self.replacements.items():
            occurrences = masked.count(target)
            if occurrences == 0:
                continue
            masked = masked.replace(target, placeholder)
            label = placeholder.strip("[]")
            counts[label] = counts.get(label, 0) + occurrences
        return MaskingResult(text=masked, entity_counts=counts)


class FakeRetriever:
    """Fake Retriever implementing the resolve() contract.

    Resolves every citation to a fixed source text, so pipeline
    orchestration tests exercise the resolved path without a vector index
    or embedding model. Tests that need an unresolved citation can set
    resolve_all to False.
    """

    def __init__(self, resolve_all: bool = True) -> None:
        self.resolve_all = resolve_all

    def resolve(self, citations: list[str]) -> list[NormWithSource]:
        results: list[NormWithSource] = []
        for citation in citations:
            if self.resolve_all:
                results.append(
                    NormWithSource(
                        canonical_citation=citation,
                        paragraph_key=citation,
                        source_text=f"Gesetzestext zu {citation}.",
                        method="exact",
                        confidence=None,
                        resolved=True,
                    )
                )
            else:
                results.append(
                    NormWithSource(
                        canonical_citation=citation,
                        paragraph_key="",
                        source_text="",
                        method="none",
                        confidence=None,
                        resolved=False,
                    )
                )
        return results


class RaisingAuditStoreFake:
    """AuditEventPublisherProtocol fake whose publish always raises.

    Drives the interim _emit failure path (ADR-027): every custody emit fails,
    is logged at ERROR as AUDIT_APPEND_FAILED, and is swallowed in Round A. In
    Round C the same fake will make run() raise AuditWriteError; the pipeline
    logging test notes that pending mutation in its docstring. publish_calls
    records how many publishes were attempted, so a test can assert the failure
    path was exercised.
    """

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error or AuditLogError("simulated audit store write failure")
        self.publish_calls = 0

    def publish(self, event: AuditEvent) -> None:
        self.publish_calls += 1
        raise self._error

    def query(
        self,
        einwendungs_id: str | None = None,
        event_type: AuditEventType | None = None,
        after: Any = None,
        before: Any = None,
    ) -> list[AuditEvent]:
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
    """Fully wired pipeline with a stubbed triage LLM and a fake retriever.

    The triage FakeLLMClient is preloaded with a single-argument
    LLMTriageOutput whose original_zitat matches the smoke-test sample
    text. The Briefing context uses no LLM, so no drafting double is
    needed; the FakeRetriever supplies resolved norm text deterministically.
    The ingestion masker is a pass-through FakePiiMasker, since masking is
    not the subject of pipeline orchestration tests.
    """
    audit_store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    triage_llm = FakeLLMClient(parse_response=_DEFAULT_TRIAGE_OUTPUT)
    pipeline = Pipeline(
        ingestion=DocumentIngestionService(
            raw_store_path=tmp_path / "raw",
            masker=FakePiiMasker(),
        ),
        triage=TriageService(llm=triage_llm),
        retrieval=FakeRetriever(),
        briefing=BriefingService(),
        audit=AuditLogService(store=audit_store),
    )
    return pipeline, audit_store


@pytest.fixture()
def pipeline_with_failing_audit(
    tmp_path: Path,
) -> tuple[Pipeline, RaisingAuditStoreFake]:
    """Pipeline whose audit store raises on every publish.

    Identical to pipeline_and_audit except the audit store is a
    RaisingAuditStoreFake, so every custody emit hits the interim _emit failure
    path (ADR-027). Used to exercise the governed-ERROR-and-swallow behaviour
    and the constant correlation id across a run's failed emits.
    """
    raising_store = RaisingAuditStoreFake()
    triage_llm = FakeLLMClient(parse_response=_DEFAULT_TRIAGE_OUTPUT)
    pipeline = Pipeline(
        ingestion=DocumentIngestionService(
            raw_store_path=tmp_path / "raw",
            masker=FakePiiMasker(),
        ),
        triage=TriageService(llm=triage_llm),
        retrieval=FakeRetriever(),
        briefing=BriefingService(),
        audit=AuditLogService(store=raising_store),
    )
    return pipeline, raising_store
