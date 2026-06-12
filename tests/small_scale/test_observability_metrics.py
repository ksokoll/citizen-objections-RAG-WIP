"""Behaviour tests for the six in-process pipeline metrics.

Logs tell one run's story; metrics count the fleet. Exactly the six metrics
from docs/OBSERVABILITY_IMPLEMENTATION.md exist, every write is contained (a
failing metrics call never aborts the business path), and the domain counters
increment on their defined triggers. The registry is module-global, so every
assertion works on before/after deltas, never on absolute values.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.audit_log.service import AuditLogService
from app.audit_log.store import JsonLinesAuditStore
from app.briefing.service import BriefingService
from app.core import EinwendungsTyp
from app.document_ingestion.service import DocumentIngestionService
from app.observability import metrics
from app.pipeline import Pipeline
from app.triage.llm_schema import LLMArgument, LLMTriageOutput
from app.triage.service import TriageService
from tests.conftest import FakeLLMClient, FakePiiMasker, FakeRetriever

_SAMPLE_EINWENDUNG = (
    "Ein vorhabenbezogener Bebauungsplan, der von dieser "
    "Darstellung des Flächennutzungsplans abweicht."
)


def _sample(name: str, labels: dict[str, str] | None = None) -> float:
    """Read one sample from the in-process registry, defaulting to 0."""
    return metrics.REGISTRY.get_sample_value(name, labels) or 0


def test_exactly_the_six_metrics_exist_and_no_seventh() -> None:
    """The registry holds the six planned metrics, nothing else.

    A change-detector: a seventh metric (the named scope-creep risk of
    Round B) cannot appear without this assertion changing.
    """
    families = {family.name for family in metrics.REGISTRY.collect()}
    assert families == {
        "objections_processed",
        "stage_duration_seconds",
        "norm_resolution",
        "arguments_per_objection",
        "argument_verification_failures",
        "audit_write_failures",
    }


def test_processed_objection_counts_under_its_terminal_status(
    pipeline_and_audit: tuple[Pipeline, JsonLinesAuditStore],
) -> None:
    """A successful run increments objections_processed_total once.

    Given a wired pipeline whose run produces a briefing, when the run
    completes, then the briefing_erstellt series grows by exactly one (the
    plan's objections-processed-by-status trigger).
    """
    pipeline, _ = pipeline_and_audit
    label = {"status": "briefing_erstellt"}
    before = _sample("objections_processed_total", label)

    pipeline.run(_SAMPLE_EINWENDUNG)

    assert _sample("objections_processed_total", label) - before == 1


def test_failed_zitat_check_increments_the_verification_failure_counter(
    tmp_path: Path,
) -> None:
    """An unverifiable original_zitat increments the failure counter.

    Given a triage LLM whose original_zitat is not a substring of the source
    text (the ADR-006 Layer 1 check fails), when the run completes, then
    argument_verification_failures_total grows by one: the deterministic
    Triage-LLM quality signal fires on its defined trigger.
    """
    hallucinated = LLMTriageOutput(
        argumente=[
            LLMArgument(
                catalog_id="baugb",
                einwendungs_typ=EinwendungsTyp.TYP_2,
                argument_text="Halluziniertes Argument",
                original_zitat="Dieser Satz steht nirgends im Dokument.",
            ),
        ]
    )
    pipeline = Pipeline(
        ingestion=DocumentIngestionService(
            raw_store_path=tmp_path / "raw",
            masker=FakePiiMasker(),
        ),
        triage=TriageService(llm=FakeLLMClient(parse_response=hallucinated)),
        retrieval=FakeRetriever(corpus_id="corpus-id-metrics-test"),
        briefing=BriefingService(),
        audit=AuditLogService(store=JsonLinesAuditStore(tmp_path / "audit.jsonl")),
    )
    before = _sample("argument_verification_failures_total")

    pipeline.run(_SAMPLE_EINWENDUNG)

    assert _sample("argument_verification_failures_total") - before == 1


def test_stage_durations_are_fed_from_the_traced_measurement(
    pipeline_and_audit: tuple[Pipeline, JsonLinesAuditStore],
) -> None:
    """Every traced stage feeds one observation into the duration histogram.

    Given a wired pipeline, when one run completes, then the
    stage_duration_seconds histogram has exactly one new observation for the
    run root, so duration needs no module-global start-time state of its own.
    """
    label = {"stage": "pipeline.run"}
    before = _sample("stage_duration_seconds_count", label)
    pipeline, _ = pipeline_and_audit

    pipeline.run(_SAMPLE_EINWENDUNG)

    assert _sample("stage_duration_seconds_count", label) - before == 1


def test_a_sabotaged_registry_does_not_abort_the_run(
    pipeline_and_audit: tuple[Pipeline, JsonLinesAuditStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Metrics failures are contained: the business path always completes.

    Given every metric object replaced by one that raises on any use, when
    the pipeline runs, then the run still returns a briefing: a metric
    increment may no more abort the business path than a log call.
    """

    class SabotagedMetric:
        def labels(self, **kwargs: object) -> SabotagedMetric:
            raise RuntimeError("metrics registry sabotaged")

        def inc(self, *args: object) -> None:
            raise RuntimeError("metrics registry sabotaged")

        def observe(self, *args: object) -> None:
            raise RuntimeError("metrics registry sabotaged")

    for name in (
        "OBJECTIONS_PROCESSED",
        "STAGE_DURATION",
        "NORM_RESOLUTION",
        "ARGUMENTS_PER_OBJECTION",
        "ARGUMENT_VERIFICATION_FAILURES",
        "AUDIT_WRITE_FAILURES",
    ):
        monkeypatch.setattr(metrics, name, SabotagedMetric())
    pipeline, _ = pipeline_and_audit

    briefing = pipeline.run(_SAMPLE_EINWENDUNG)

    assert briefing is not None
    assert len(briefing.entries) == 1
