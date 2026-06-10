"""Behaviour tests for the pipeline's governed logging (ADR-026, ADR-027).

The pipeline anchors every log event of a run on the document_id correlation
id and, in the interim Round A policy, logs a failed audit publish as a
governed ERROR event and swallows it. Both behaviours are asserted at the sink.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from app.observability.events import AUDIT_APPEND_FAILED
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


def test_correlation_id_is_constant_across_all_events_of_a_run(
    pipeline_with_failing_audit: tuple[Pipeline, RaisingAuditStoreFake],
    log_sink: Callable[[], list[dict]],
) -> None:
    """Every log event of a single run carries the same correlation id.

    Given a run whose audit store fails on every publish (so each custody emit
    produces a governed ERROR event), when the run completes, then all of those
    events carry the one correlation id anchored on the run's document_id.
    """
    pipeline, _ = pipeline_with_failing_audit

    briefing = pipeline.run(_SAMPLE_EINWENDUNG)

    lines = log_sink()
    failures = [line for line in lines if line["event"] == AUDIT_APPEND_FAILED]
    assert len(failures) >= 2
    correlation_ids = {line["correlation_id"] for line in failures}
    assert correlation_ids == {briefing.document_id}


def test_emit_failure_is_logged_at_error_and_swallowed(
    pipeline_with_failing_audit: tuple[Pipeline, RaisingAuditStoreFake],
    log_sink: Callable[[], list[dict]],
) -> None:
    """A failed audit publish is logged at ERROR and does not abort the run.

    Interim Round A policy (ADR-027): the run still returns a briefing and each
    failed publish is a governed AUDIT_APPEND_FAILED ERROR event carrying only
    the audit_event_type, never the exception message.

    Round C mutation: when fail-closed lands, this assertion flips to
    pytest.raises(AuditWriteError) and the run returns no briefing.
    """
    pipeline, raising_store = pipeline_with_failing_audit

    briefing = pipeline.run(_SAMPLE_EINWENDUNG)

    assert briefing is not None
    assert raising_store.publish_calls >= 1

    lines = log_sink()
    failures = [line for line in lines if line["event"] == AUDIT_APPEND_FAILED]
    assert failures
    assert all(line["level"] == "error" for line in failures)
    assert all("audit_event_type" in line for line in failures)
    rendered = json.dumps(failures)
    assert "simulated audit store write failure" not in rendered
