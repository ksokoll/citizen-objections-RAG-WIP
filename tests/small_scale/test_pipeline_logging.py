"""Behaviour tests for the pipeline's governed logging and fail-closed custody.

The pipeline anchors every log event of a run on the document_id correlation id
and, fail-closed (ADR-027 armed, ADR-033), records a failed custody publish (the
governed AUDIT_APPEND_FAILED ERROR event plus the audit_write_failures_total
metric) and then aborts the run by re-raising the AuditLogError. The visibility
is asserted at the sink and the metric; the abort is asserted at run()'s edge.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from app.audit_log.events import AUDIT_APPEND_FAILED
from app.core.failures import AuditLogError
from app.observability import metrics
from app.observability.logging_config import LOG_FILENAME, configure_logging
from app.pipeline import Pipeline
from tests.conftest import RaisingAuditStoreFake

_SAMPLE_EINWENDUNG = (
    "Ein vorhabenbezogener Bebauungsplan, der von dieser "
    "Darstellung des Flächennutzungsplans abweicht. "
    "Die Öffentlichkeit wurde über grundlegende "
    "Planänderungen nicht frühzeitig unterrichtet."
)


@pytest.fixture()
def log_sink(tmp_path: Path) -> Callable[[], list[dict]]:
    """Redirect the single sink to tmp_path; return a JSON-lines reader.

    Teardown restores a good configuration in the same tmp path so a broken
    chain never leaks into a later test.
    """
    configure_logging(log_dir=tmp_path, fmt="json")
    log_file = tmp_path / LOG_FILENAME

    def read_lines() -> list[dict]:
        for handler in logging.getLogger().handlers:
            handler.flush()
        if not log_file.exists():
            return []
        return [
            json.loads(line)
            for line in log_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    yield read_lines

    configure_logging(log_dir=tmp_path, fmt="json")


def _stored_document_id(tmp_path: Path) -> str:
    """The document id of the run's single raw-store file.

    Ingestion stores the unmasked original as ``<document_id>.txt`` before the
    first custody emit, so even a run that aborts at that first emit leaves the
    file behind. Reading its stem recovers the document id the run anchored its
    correlation scope on, without the briefing the aborted run never returns.
    """
    stored = list((tmp_path / "raw").glob("*.txt"))
    assert len(stored) == 1
    return stored[0].stem


def test_custody_write_failure_is_logged_counted_then_aborts_the_run(
    tmp_path: Path,
    pipeline_with_failing_audit: tuple[Pipeline, RaisingAuditStoreFake],
    log_sink: Callable[[], list[dict]],
) -> None:
    """A failed custody publish is logged, counted, then aborts run (fail-closed).

    Fail-closed armed (ADR-027 realized in ADR-033): given a run whose audit
    store raises the recoverable AuditLogError, when run() reaches its first
    custody emit, then the failure is made visible first (a governed
    AUDIT_APPEND_FAILED ERROR event carrying only the audit_event_type, never the
    exception message, and one audit_write_failures_total increment) and then the
    AuditLogError propagates out of run(): no briefing is returned. The run
    aborts at the first emit, so exactly one publish is attempted, not the whole
    stage sequence. The ERROR event is still anchored on the run's correlation
    id. This was the Round 15.x swallow test; fail-closed flips it to a raises
    test while the ERROR-log and metric assertions are retained, not weakened.
    """
    pipeline, raising_store = pipeline_with_failing_audit
    failures_before = (
        metrics.REGISTRY.get_sample_value("audit_write_failures_total") or 0
    )

    with pytest.raises(AuditLogError):
        pipeline.run(_SAMPLE_EINWENDUNG)

    assert raising_store.publish_calls == 1

    lines = log_sink()
    failures = [line for line in lines if line["event"] == AUDIT_APPEND_FAILED]
    assert len(failures) == 1
    assert failures[0]["level"] == "error"
    assert "audit_event_type" in failures[0]
    assert failures[0]["correlation_id"] == _stored_document_id(tmp_path)
    assert "simulated audit store write failure" not in json.dumps(failures)

    failures_after = metrics.REGISTRY.get_sample_value("audit_write_failures_total")
    assert failures_after - failures_before == 1


def test_store_programming_error_propagates_out_of_run(
    pipeline_with_crashing_audit: tuple[Pipeline, RaisingAuditStoreFake],
) -> None:
    """A non-recoverable store error (TypeError) aborts run(), it is not swallowed.

    Given an audit store that raises TypeError (a programming error, not the
    recoverable store-failure class), when run() reaches the first custody emit,
    then the TypeError propagates out of run() rather than being logged and
    swallowed: hard and recoverable failures are routed differently (ADR-027,
    failure-routing rule).
    """
    pipeline, _ = pipeline_with_crashing_audit

    with pytest.raises(TypeError):
        pipeline.run(_SAMPLE_EINWENDUNG)


def test_sink_failure_does_not_mask_the_custody_write_error(
    pipeline_with_failing_audit: tuple[Pipeline, RaisingAuditStoreFake],
    log_sink: Callable[[], list[dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sabotaged sink does not mask or replace the aborting AuditLogError.

    Given a run whose audit store raises the recoverable AuditLogError and whose
    sink logger itself raises on every call, when run() reaches the first custody
    emit, then the AuditLogError (the custody-write failure) is what propagates,
    not the sink's RuntimeError: the guarded _log.error swallows the sink failure
    so the visibility channel being down degrades visibility only, it never
    changes which error aborts the run. The sink-independent
    audit_write_failures_total still counts the failure (ADR-027).
    """
    pipeline, _ = pipeline_with_failing_audit

    class SabotagedLogger:
        def error(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("logging sink sabotaged")

    monkeypatch.setattr("app.pipeline._log", SabotagedLogger())
    failures_before = (
        metrics.REGISTRY.get_sample_value("audit_write_failures_total") or 0
    )

    with pytest.raises(AuditLogError):
        pipeline.run(_SAMPLE_EINWENDUNG)

    failures_after = metrics.REGISTRY.get_sample_value("audit_write_failures_total")
    assert failures_after - failures_before == 1
