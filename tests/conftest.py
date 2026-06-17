"""Shared test fakes and fixtures for the citizen-objections-RAG test suite."""

from collections.abc import Iterator
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
from app.observability import (
    configure_logging,
    reset_registered_events,
    reset_registered_keys,
    set_strict_mode,
)
from app.observability.tracing import reset_tracing, set_tracing_enabled
from app.observability_registry import register_observability_vocabulary
from app.pipeline import Pipeline
from app.retrieval.entities import NormWithSource
from app.triage.llm_schema import LLMArgument, LLMTriageOutput
from app.triage.service import TriageService

load_dotenv()


@pytest.fixture(scope="session", autouse=True)
def _configured_log_sink(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Configure the governed sink once for the whole suite (ADR-026).

    Round B retired the import-time configuration stopgap: importing the
    observability package installs nothing, and configuration is an explicit
    composition-root act. For the test suite that composition root is this
    fixture. Tests that assert at the sink reconfigure to their own tmp path
    via their local log_sink fixtures; configure_logging is idempotent.

    The event vocabulary is a root-assembled union (H2): the test root unions
    every context's declared events plus the CLI's into the registry the chain
    enforces against, the same act the CLI performs. Registered here once for
    the session; the autouse reset re-registers it after clearing the registry
    so a test cannot leak vocabulary.
    """
    register_observability_vocabulary()
    configure_logging(log_dir=tmp_path_factory.mktemp("observability-logs"))


@pytest.fixture(autouse=True)
def _reset_observability_globals() -> Iterator[None]:
    """Symmetrically reset every observability global between tests (ADR-026).

    The observability layer keeps process-wide globals (the declared single-run
    debt: a run-scoped instrumentation object is the deferred retrofit). Until
    that lands, a test that flips one of them can poison whatever test runs
    next. This one autouse fixture resets them all symmetrically, so no test
    inherits another's observability state regardless of collection order. It
    replaces the former selective tracing fixture (autouse only inside the
    tracing module) and the strict-only fixture: a tracing-flipping test in any
    module could otherwise leak into a following test the tracing fixture never
    covered.

    Setup establishes the test default: the vocabulary registries re-assembled
    (the union the CLI also builds), strict enforcement on so CI catches every
    typo and key at its origin, and tracing off (the production default; tests
    that exercise the enabled path set it via set_tracing_enabled). Teardown
    clears all of it: tracing provider/exporter/flag, strict mode, and both
    vocabulary registries. A test that needs production behaviour opts out in
    its body (set_strict_mode(False)); this fixture restores the defaults for
    the next test either way.
    """
    register_observability_vocabulary()
    set_strict_mode(True)
    set_tracing_enabled(False)
    yield
    reset_tracing()
    set_strict_mode(False)
    reset_registered_events()
    reset_registered_keys()


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


# Source revision exposed by the FakeRetriever wired into the pipeline
# fixtures. A recognizable constant rather than a real hash: provenance tests
# assert the briefing carries exactly the id of the wired retriever (ADR-028).
TEST_CORPUS_ID = "corpus-id-of-the-wired-fake-retriever"


class FakeRetriever:
    """Fake Retriever implementing the resolve() and source_revision contract.

    Resolves every citation to a fixed source text, so pipeline
    orchestration tests exercise the resolved path without a vector index
    or embedding model. Tests that need an unresolved citation can set
    resolve_all to False. The retriever owns the source identity (ADR-028),
    so the fake carries a source_revision like the production service.
    """

    def __init__(
        self,
        resolve_all: bool = True,
        source_revision: str = TEST_CORPUS_ID,
    ) -> None:
        self.resolve_all = resolve_all
        self.source_revision = source_revision

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

    Drives the fail-closed _emit path (ADR-027 armed, ADR-033): the first custody
    emit fails, is logged at ERROR as AUDIT_APPEND_FAILED, counted in
    audit_write_failures_total, and then raised, so run() aborts at that first
    emit rather than swallowing and continuing. The abort is carried by the
    existing AuditLogError (no new type; ADR-033). publish_calls records how many
    publishes were attempted, so a test can assert the run aborted at the first
    one rather than walking the whole stage sequence.
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


def _build_pipeline(tmp_path: Path, audit_publisher: Any) -> Pipeline:
    """Wire a pipeline with test doubles around the given audit publisher.

    The triage FakeLLMClient is preloaded with a single-argument
    LLMTriageOutput whose original_zitat matches the smoke-test sample text.
    The Briefing context uses no LLM, so no drafting double is needed; the
    FakeRetriever supplies resolved norm text deterministically. The ingestion
    masker is a pass-through FakePiiMasker, since masking is not the subject of
    pipeline orchestration tests. Only the audit publisher varies across the
    pipeline fixtures, so it is the single injected parameter here.
    """
    triage_llm = FakeLLMClient(parse_response=_DEFAULT_TRIAGE_OUTPUT)
    return Pipeline(
        ingestion=DocumentIngestionService(
            raw_store_path=tmp_path / "raw",
            masker=FakePiiMasker(),
        ),
        triage=TriageService(llm=triage_llm),
        retrieval=FakeRetriever(),
        briefing=BriefingService(),
        audit=AuditLogService(store=audit_publisher),
    )


@pytest.fixture()
def pipeline_and_audit(tmp_path: Path) -> tuple[Pipeline, JsonLinesAuditStore]:
    """Fully wired pipeline with a stubbed triage LLM and a fake retriever.

    The audit store is a real JsonLinesAuditStore, so custody events are
    durably appended and queryable.
    """
    audit_store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    return _build_pipeline(tmp_path, audit_store), audit_store


@pytest.fixture()
def pipeline_with_failing_audit(
    tmp_path: Path,
) -> tuple[Pipeline, RaisingAuditStoreFake]:
    """Pipeline whose audit store raises AuditLogError on every publish.

    The store raises the recoverable failure class, so every custody emit hits
    the interim _emit failure path (ADR-027): logged at ERROR as
    AUDIT_APPEND_FAILED and swallowed. Used to exercise the
    governed-ERROR-and-swallow behaviour and the constant correlation id across
    a run's failed emits.
    """
    raising_store = RaisingAuditStoreFake()
    return _build_pipeline(tmp_path, raising_store), raising_store


@pytest.fixture()
def pipeline_with_crashing_audit(
    tmp_path: Path,
) -> tuple[Pipeline, RaisingAuditStoreFake]:
    """Pipeline whose audit store raises a programming error on every publish.

    The store raises TypeError, not AuditLogError: a deterministic bug, not a
    recoverable store failure. _emit must not swallow it (ADR-027,
    failure-routing rule), so it propagates out of run(). Used to assert that
    the hard-failure class is routed differently from the recoverable one.
    """
    crashing_store = RaisingAuditStoreFake(
        error=TypeError("programming error in the audit publish path")
    )
    return _build_pipeline(tmp_path, crashing_store), crashing_store
