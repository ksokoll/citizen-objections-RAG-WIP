"""Behaviour tests for the one-sink, default-deny logging chain (ADR-026).

Every control is asserted at the sink (the rendered log file), not at the call
site, because the point of the architecture is that the control cannot be
bypassed by choosing a different logging API. The log_sink fixture redirects
the single handler to a tmp path and returns the parsed JSON lines.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import structlog

from app.observability.correlation import correlation_scope
from app.observability.events import AUDIT_APPEND_FAILED, UnregisteredLogEventError
from app.observability.logging_config import (
    ALLOWED_KEYS,
    LOG_FILENAME,
    ProcessorChainError,
    _OwnerOnlyTimedRotatingFileHandler,
    _self_check,
    configure_logging,
    sweep_expired_logs,
)

_POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX mode bits do not apply on Windows (ADR-026 limitation)",
)


@pytest.fixture()
def log_sink(tmp_path: Path) -> Callable[[], list[dict]]:
    """Redirect the single sink to tmp_path; return a JSON-lines reader.

    Teardown restores a good configuration in the same tmp path so a test that
    deliberately breaks the chain (the self-check test) does not leak a broken
    structlog config into later tests.
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


def test_foreign_stdlib_extra_field_never_reaches_the_sink(
    log_sink: Callable[[], list[dict]],
) -> None:
    """A PII-shaped extra on a foreign stdlib record is dropped by the allowlist.

    Given a third-party logger (Presidio) logging above the WARNING clamp with a
    PII-shaped extra field, when the record reaches the sink, then the foreign
    message text survives as the event but the extra field and its value are
    absent. This is the stdlib-bypass closure: foreign records route through the
    same allowlist as our own.
    """
    logging.getLogger("presidio-analyzer").error(
        "presidio analysis complete",
        extra={"pii_field": "Max Mustermann"},
    )

    lines = log_sink()

    assert len(lines) == 1
    record = lines[0]
    assert record["event"] == "presidio analysis complete"
    assert "pii_field" not in record
    assert "Max Mustermann" not in json.dumps(record)


def test_allowlist_is_the_frozen_golden_set() -> None:
    """The allowlist is exactly the Round A golden set.

    A change-detector golden test: a new allowlisted key cannot be added
    without this assertion changing, so widening the default-deny surface is a
    deliberate, reviewable act.
    """
    assert ALLOWED_KEYS == frozenset(
        {
            "event",
            "level",
            "timestamp",
            "correlation_id",
            "audit_event_type",
            "exc_type",
            "exc_location",
            "survivor_count",
            "name_regions_masked",
            "store_mode",
        }
    )


def test_exception_is_reduced_to_type_and_location(
    log_sink: Callable[[], list[dict]],
) -> None:
    """An exception is logged as type plus location, never its message.

    Given an exception whose message carries a PII-shaped payload, when it is
    logged with exc_info, then the sink line carries exc_type and exc_location
    but neither the message string nor a rendered traceback.
    """
    secret_payload = "citizen Max Mustermann IBAN DE00 1234 5678"
    log = structlog.get_logger()
    try:
        raise ValueError(secret_payload)
    except ValueError:
        log.error(AUDIT_APPEND_FAILED, exc_info=True)

    lines = log_sink()

    assert len(lines) == 1
    record = lines[0]
    assert record["exc_type"] == "ValueError"
    assert "exc_location" in record
    assert "exception" not in record
    assert secret_payload not in json.dumps(record)


def test_unregistered_structlog_event_name_raises(
    log_sink: Callable[[], list[dict]],
) -> None:
    """A structlog event whose name is not a registered constant fails loudly.

    The message policy is a mechanism: an ad hoc event name raises at the
    processor chain rather than silently widening the vocabulary.
    """
    log = structlog.get_logger()

    with pytest.raises(UnregisteredLogEventError):
        log.info("ad.hoc.unregistered.event", count=1)


def test_self_check_raises_when_allowlist_removed_from_chain(
    log_sink: Callable[[], list[dict]],
) -> None:
    """The self-check fails loudly if the allowlist leaves the active chain.

    Given a reconfiguration that keeps the self-check but drops the allowlist
    processor, when the next event is emitted, then ProcessorChainError is
    raised before any ungoverned output is produced.
    """
    structlog.configure(
        processors=[_self_check, structlog.processors.JSONRenderer()],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    log = structlog.get_logger()

    with pytest.raises(ProcessorChainError):
        log.info(AUDIT_APPEND_FAILED)


def test_sweep_deletes_only_expired_rotated_files(tmp_path: Path) -> None:
    """The startup sweep deletes over-age rotated files, never the active log.

    Given an active log, a fresh rotated file, and an over-age rotated file,
    when the sweep runs, then only the over-age rotated file is deleted.
    """
    reference = datetime(2026, 6, 10, tzinfo=UTC)
    active = tmp_path / LOG_FILENAME
    active.write_text("active line\n", encoding="utf-8")
    fresh = tmp_path / f"{LOG_FILENAME}.2026-06-05"
    fresh.write_text("fresh rotated line\n", encoding="utf-8")
    expired = tmp_path / f"{LOG_FILENAME}.2026-04-01"
    expired.write_text("expired rotated line\n", encoding="utf-8")

    fresh_mtime = reference.timestamp() - 5 * 86400
    expired_mtime = reference.timestamp() - 60 * 86400
    os.utime(fresh, (fresh_mtime, fresh_mtime))
    os.utime(expired, (expired_mtime, expired_mtime))

    deleted = sweep_expired_logs(log_dir=tmp_path, retention_days=30, now=reference)

    assert deleted == [expired]
    assert not expired.exists()
    assert fresh.exists()
    assert active.exists()


def test_foreign_extra_cannot_spoof_correlation_id_or_inject_a_key(
    log_sink: Callable[[], list[dict]],
) -> None:
    """A foreign record's extras never reach the sink (default-deny by origin).

    Given a run anchored on a known correlation id, when a third-party record is
    logged with an extra spoofing ``correlation_id`` and an extra carrying an
    allowlisted key (``survivor_count``), then the sink line shows the
    ContextVar correlation id, not the spoofed one, and the injected
    allowlisted key is absent: foreign extras are not lifted into the event dict
    at all.
    """
    with correlation_scope("doc-truth-0001"):
        logging.getLogger("presidio-analyzer").error(
            "presidio analysis complete",
            extra={"correlation_id": "spoofed-foreign-id", "survivor_count": 999},
        )

    lines = log_sink()

    assert len(lines) == 1
    record = lines[0]
    assert record["correlation_id"] == "doc-truth-0001"
    assert "spoofed-foreign-id" not in json.dumps(record)
    assert "survivor_count" not in record


def test_own_code_kwarg_correlation_id_is_overwritten_by_the_contextvar(
    log_sink: Callable[[], list[dict]],
) -> None:
    """An own-code correlation_id kwarg cannot override the ContextVar truth.

    Given a run anchored on a known correlation id, when our own structlog call
    passes a conflicting ``correlation_id`` kwarg, then the authoritative
    correlation processor stamps the ContextVar value unconditionally and the
    kwarg value never reaches the sink.
    """
    log = structlog.get_logger()
    with correlation_scope("doc-truth-0002"):
        log.error(AUDIT_APPEND_FAILED, correlation_id="spoofed-by-kwarg")

    lines = log_sink()

    assert len(lines) == 1
    record = lines[0]
    assert record["correlation_id"] == "doc-truth-0002"
    assert "spoofed-by-kwarg" not in json.dumps(record)


@_POSIX_ONLY
def test_sink_file_and_directory_are_owner_only_after_first_write(
    log_sink: Callable[[], list[dict]],
    tmp_path: Path,
) -> None:
    """The sink file is 0o600 and its directory 0o700 after the first write.

    The logs are a third store of pseudonymous data (ADR-026); the sink is held
    to the same owner-only posture as the raw store (ADR-025). Asserted at the
    filesystem on POSIX, skipped on Windows.
    """
    structlog.get_logger().error(AUDIT_APPEND_FAILED)
    log_sink()

    log_file = tmp_path / LOG_FILENAME
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


@_POSIX_ONLY
def test_sink_modes_survive_a_forced_rollover(
    log_sink: Callable[[], list[dict]],
    tmp_path: Path,
) -> None:
    """A rotated file inherits the owner-only mode and the new active file too.

    Given a written sink, when a rollover is forced and another event is
    written, then both the rotated file and the fresh active file are 0o600 and
    the directory stays 0o700.
    """
    log = structlog.get_logger()
    log.error(AUDIT_APPEND_FAILED)
    log_sink()

    handler = next(
        h
        for h in logging.getLogger().handlers
        if isinstance(h, _OwnerOnlyTimedRotatingFileHandler)
    )
    handler.doRollover()

    log.error(AUDIT_APPEND_FAILED)
    log_sink()

    log_file = tmp_path / LOG_FILENAME
    rotated = list(tmp_path.glob(f"{LOG_FILENAME}.*"))
    assert rotated
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600
    for rotated_file in rotated:
        assert stat.S_IMODE(rotated_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
