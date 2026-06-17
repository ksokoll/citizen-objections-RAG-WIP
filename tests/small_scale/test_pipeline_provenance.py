"""Behaviour tests for the provenance the pipeline stamps into a briefing.

A briefing is only an auditable result if its statute state and creation time
are determinable afterward (ADR-028). The corpus identity is owned by the
retriever (H2, Round 16.1): the Coordinator reads the id from the wired
retriever and stamps a timezone-aware UTC creation time. No separate id
parameter exists, so false provenance is unconstructable.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

from app.audit_log.store import JsonLinesAuditStore
from app.pipeline import Pipeline
from tests.conftest import TEST_CORPUS_ID

_SAMPLE_EINWENDUNG = (
    "Ein vorhabenbezogener Bebauungsplan, der von dieser "
    "Darstellung des Flächennutzungsplans abweicht."
)


def test_briefing_carries_the_corpus_id_of_the_wired_retriever(
    pipeline_and_audit: tuple[Pipeline, JsonLinesAuditStore],
) -> None:
    """The briefing carries exactly the wired retriever's corpus id.

    Given a pipeline composed with a retriever exposing a known corpus id,
    when a document is processed, then the briefing's corpus_id is that
    retriever's id, so the statute state behind every delivered briefing is
    structurally the one resolved against (ADR-028, H2).
    """
    pipeline, _ = pipeline_and_audit

    briefing = pipeline.run(_SAMPLE_EINWENDUNG)

    assert briefing.corpus_id == TEST_CORPUS_ID


def test_pipeline_has_no_corpus_id_parameter_to_lie_with() -> None:
    """The Coordinator accepts no corpus id beside the retriever's own.

    A change-detector for the H2 fix: reintroducing a free corpus_id
    constructor parameter (an id that could disagree with the wired corpus)
    fails this assertion.
    """
    assert "corpus_id" not in inspect.signature(Pipeline.__init__).parameters


def test_briefing_created_at_is_timezone_aware_utc(
    pipeline_and_audit: tuple[Pipeline, JsonLinesAuditStore],
) -> None:
    """The briefing's creation time is timezone-aware UTC and current.

    Given a wired pipeline, when a document is processed, then created_at
    carries an explicit UTC offset (never a naive timestamp) and lies between
    the moments just before and just after the run.
    """
    pipeline, _ = pipeline_and_audit
    before = datetime.now(UTC)

    briefing = pipeline.run(_SAMPLE_EINWENDUNG)

    after = datetime.now(UTC)
    assert briefing.created_at.tzinfo is not None
    assert briefing.created_at.utcoffset().total_seconds() == 0
    assert before <= briefing.created_at <= after
