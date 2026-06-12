"""Behaviour tests for @traced and the off-by-default tracing scaffold.

Timing must not depend on tracing: every traced call emits the governed
stage_timing event whether or not spans exist (ADR-023). Spans exist only
under OBSERVABILITY_TRACING=1, land in a bounded in-memory exporter, and the
run owner (the Coordinator) clears the exporter explicitly at run start (M5).
Asserted at the sink and at the exporter, never at the call site.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

from app.audit_log.service import AuditLogService
from app.audit_log.store import JsonLinesAuditStore
from app.briefing.service import BriefingService
from app.core import EinwendungsTyp
from app.document_ingestion.service import DocumentIngestionService
from app.observability.events import STAGE_TIMING
from app.observability.logging_config import LOG_FILENAME, configure_logging
from app.observability.tracing import (
    get_finished_spans,
    reset_tracing,
    traced,
    tracer_provider_is_built,
)
from app.pipeline import Pipeline
from app.retrieval.entities import GesetzParagraph, LoadedCorpus
from app.retrieval.service import NormRetrievalService
from app.triage.llm_schema import LLMArgument, LLMTriageOutput
from app.triage.service import TriageService
from tests.conftest import FakeLLMClient, FakePiiMasker

_SAMPLE_EINWENDUNG = (
    "Die Versiegelung der Grundfläche ist deutlich zu hoch und "
    "widerspricht dem Gebot des sparsamen Umgangs mit Boden."
)

#: The five linear pipeline stages plus the root; each must produce exactly
#: one span per run. The audit_log stage is traced per publish and therefore
#: produces one span per custody event (four on the happy path).
_SINGLE_SPAN_STAGES = (
    "pipeline.run",
    "document_ingestion",
    "triage",
    "retrieval",
    "briefing",
)


@pytest.fixture(autouse=True)
def _tracing_off_and_clean(monkeypatch: pytest.MonkeyPatch):
    """Default every test to tracing disabled and reset the scaffold after.

    The flag could leak in from the developer environment; tests that
    exercise the enabled path set it explicitly. Teardown discards the
    module-held provider so one test's backend never leaks into the next.
    """
    monkeypatch.delenv("OBSERVABILITY_TRACING", raising=False)
    yield
    reset_tracing()


@pytest.fixture()
def log_sink(tmp_path: Path) -> Callable[[], list[dict]]:
    """Redirect the single sink to tmp_path; return a JSON-lines reader."""
    configure_logging(log_dir=tmp_path, fmt="json")
    log_file = tmp_path / LOG_FILENAME

    def read_lines() -> list[dict]:
        for handler in logging.getLogger().handlers:
            handler.flush()
        if not log_file.exists():
            return []
        return [
            record
            for line in log_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
            for record in [json.loads(line)]
            if record.get("event") == STAGE_TIMING
        ]

    yield read_lines

    configure_logging(log_dir=tmp_path, fmt="json")


def _real_pipeline(tmp_path: Path) -> Pipeline:
    """Wire a pipeline whose five services are all the real, traced classes.

    The external boundaries stay faked (LLM client, PII masker), but every
    context service is the production class, so each of the five @traced
    methods is actually on the call path. The retriever is a real
    NormRetrievalService over one in-memory paragraph: exact-match lookup
    needs no model or index.
    """
    triage_output = LLMTriageOutput(
        argumente=[
            LLMArgument(
                catalog_id="baugb",
                einwendungs_typ=EinwendungsTyp.TYP_2,
                argument_text="Zu hohe Versiegelung",
                original_zitat=(
                    "Die Versiegelung der Grundfläche ist deutlich zu hoch"
                ),
            ),
        ]
    )
    corpus = LoadedCorpus(
        paragraphs=[
            GesetzParagraph(
                gesetz="BauGB",
                paragraph="§ 1a",
                canonical_key="§ 1a BauGB",
                title="Ergänzende Vorschriften zum Umweltschutz",
                text="Mit Grund und Boden soll sparsam umgegangen werden.",
            )
        ],
        corpus_id="corpus-id-tracing-test",
    )
    return Pipeline(
        ingestion=DocumentIngestionService(
            raw_store_path=tmp_path / "raw",
            masker=FakePiiMasker(),
        ),
        triage=TriageService(llm=FakeLLMClient(parse_response=triage_output)),
        retrieval=NormRetrievalService(corpus),
        briefing=BriefingService(),
        audit=AuditLogService(store=JsonLinesAuditStore(tmp_path / "audit.jsonl")),
    )


def test_traced_emits_timing_event_with_tracing_disabled(
    log_sink: Callable[[], list[dict]],
) -> None:
    """Timing is captured unconditionally, without any tracing backend.

    Given tracing is disabled, when a traced callable runs, then the sink
    carries one stage_timing event with the stage name, a numeric
    duration_ms, and status ok, and no tracer provider was built.
    """

    @traced(stage="probe")
    def probe() -> str:
        return "done"

    assert probe() == "done"

    lines = log_sink()
    assert len(lines) == 1
    record = lines[0]
    assert record["stage"] == "probe"
    assert record["status"] == "ok"
    assert isinstance(record["duration_ms"], int | float)
    assert record["duration_ms"] >= 0
    assert tracer_provider_is_built() is False


def test_traced_error_path_logs_status_error_and_reraises(
    log_sink: Callable[[], list[dict]],
) -> None:
    """A raising stage logs status error with the reduced exception, then raises.

    Given a traced callable that raises, when it is called, then the original
    exception propagates unchanged and the sink carries a stage_timing ERROR
    event with the exception reduced to type plus location, never its message.
    """

    @traced(stage="probe")
    def probe() -> None:
        raise ValueError("foreign-authored detail that must not reach the sink")

    with pytest.raises(ValueError):
        probe()

    lines = log_sink()
    assert len(lines) == 1
    record = lines[0]
    assert record["stage"] == "probe"
    assert record["status"] == "error"
    assert record["level"] == "error"
    assert record["exc_type"] == "ValueError"
    assert "exc_location" in record
    assert "foreign-authored detail" not in json.dumps(lines)


def test_traced_captures_no_argument_values(
    log_sink: Callable[[], list[dict]],
) -> None:
    """Call arguments never reach the sink without an explicit opt-in.

    Given a traced callable invoked with a PII-shaped argument, when the
    timing event is rendered, then the argument's value appears nowhere in
    the sink (default-deny by origin, third application).
    """

    @traced(stage="probe")
    def probe(text: str) -> int:
        return len(text)

    probe("Max Mustermann, Musterweg 5")

    rendered = json.dumps(log_sink())
    assert "Max Mustermann" not in rendered
    assert "Musterweg" not in rendered


def test_each_stage_has_exactly_one_span_under_the_run_span(
    tmp_path: Path,
    log_sink: Callable[[], list[dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With tracing enabled, the span tree is flat: run root, one per stage.

    Given OBSERVABILITY_TRACING=1 and a pipeline wired with the real traced
    services, when one document is processed, then each linear stage has
    exactly one span, every stage span is a direct child of the pipeline.run
    root, and the per-publish audit_log stage has one span per custody event.
    """
    monkeypatch.setenv("OBSERVABILITY_TRACING", "1")
    pipeline = _real_pipeline(tmp_path)

    pipeline.run(_SAMPLE_EINWENDUNG)

    spans = get_finished_spans()
    by_name: dict[str, list] = {}
    for span in spans:
        by_name.setdefault(span.name, []).append(span)

    for stage in _SINGLE_SPAN_STAGES:
        assert len(by_name[stage]) == 1, stage
    assert len(by_name["audit_log"]) == 4

    root = by_name["pipeline.run"][0]
    assert root.parent is None
    children = [span for span in spans if span is not root]
    assert all(
        span.parent is not None and span.parent.span_id == root.context.span_id
        for span in children
    )


def test_exporter_is_cleared_when_the_next_run_starts(
    tmp_path: Path,
    log_sink: Callable[[], list[dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each run sees only its own spans; memory cannot accumulate.

    Given tracing is enabled, when a second run starts, then the Coordinator
    clears the in-memory exporter at run start (M5: the run owner defines
    the run), so after two runs only the second run's spans exist.
    """
    monkeypatch.setenv("OBSERVABILITY_TRACING", "1")
    pipeline = _real_pipeline(tmp_path)

    pipeline.run(_SAMPLE_EINWENDUNG)
    spans_after_first_run = len(get_finished_spans())

    pipeline.run(_SAMPLE_EINWENDUNG)

    assert len(get_finished_spans()) == spans_after_first_run


def test_one_wired_run_emits_exactly_one_timing_event_per_stage(
    tmp_path: Path,
    log_sink: Callable[[], list[dict]],
) -> None:
    """Fitness function: no stage decorator is silently lost (M2).

    Given a pipeline wired with the real traced services, when one document
    is processed, then the sink carries exactly one stage_timing event per
    linear stage plus the run root, and one per custody publish for the
    audit_log stage. A decorator dropped by a refactor (for example a
    wrapper class whose method no longer carries @traced) fails this count.
    """
    pipeline = _real_pipeline(tmp_path)

    pipeline.run(_SAMPLE_EINWENDUNG)

    counts = Counter(line["stage"] for line in log_sink())
    for stage in _SINGLE_SPAN_STAGES:
        assert counts[stage] == 1, stage
    assert counts["audit_log"] == 4


def test_no_tracer_provider_is_built_when_tracing_is_disabled(
    tmp_path: Path,
    log_sink: Callable[[], list[dict]],
) -> None:
    """With the flag unset, no provider, processor, or exporter exists.

    Given tracing is disabled, when a full pipeline run completes through all
    traced stages, then no tracer provider was constructed: spans are never
    created without a destination (ADR-023).
    """
    pipeline = _real_pipeline(tmp_path)

    pipeline.run(_SAMPLE_EINWENDUNG)

    assert tracer_provider_is_built() is False
    assert get_finished_spans() == ()
