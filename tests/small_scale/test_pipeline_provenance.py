"""Behaviour tests for the provenance the pipeline stamps into a briefing.

A briefing is only an auditable result if its statute state and creation time
are determinable afterward (ADR-028). The Coordinator carries the corpus id
of the wired retriever and stamps a timezone-aware UTC creation time.
"""

from __future__ import annotations

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
    """The briefing carries exactly the corpus id the composition root wired.

    Given a pipeline composed with a known corpus id, when a document is
    processed, then the briefing's corpus_id is that id, so the statute state
    behind every delivered briefing is determinable afterward (ADR-028).
    """
    pipeline, _ = pipeline_and_audit

    briefing = pipeline.run(_SAMPLE_EINWENDUNG)

    assert briefing.corpus_id == TEST_CORPUS_ID


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
