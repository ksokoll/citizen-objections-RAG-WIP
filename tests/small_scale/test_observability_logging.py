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
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import structlog

from app.observability import logging_config
from app.observability.correlation import correlation_scope
from app.observability.events import (
    AUDIT_APPEND_FAILED,
    LOG_SINK_SIZE_BYTES,
    PROCESSOR_FAILED,
    UNREGISTERED_LOG_EVENT,
    UnregisteredLogEventError,
)
from app.observability.logging_config import (
    ALLOWED_KEYS,
    LOG_FILENAME,
    MAX_FOREIGN_EVENT_CHARS,
    ObservabilityBootstrapError,
    ProcessorChainError,
    _OwnerOnlyTimedRotatingFileHandler,
    configure_logging,
    never_raise,
    sweep_expired_logs,
)

_POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX mode bits do not apply on Windows (ADR-026 limitation)",
)


@pytest.fixture()
def log_sink(tmp_path: Path) -> Callable[[], list[dict]]:
    """Redirect the single sink to tmp_path; return a JSON-lines reader.

    The reader filters out the startup ``log_sink_size_bytes`` event that
    configure_logging emits, so a per-test assertion sees only the lines the
    test produced. Teardown restores a good configuration in the same tmp path
    so a test that deliberately breaks the chain does not leak a broken
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
            record
            for line in log_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
            for record in [json.loads(line)]
            if record.get("event") != LOG_SINK_SIZE_BYTES
        ]

    yield read_lines

    configure_logging(log_dir=tmp_path, fmt="json")


def test_importing_observability_has_no_side_effects(tmp_path: Path) -> None:
    """Importing the package configures nothing (the 15.2 stopgap is retired).

    Given a clean interpreter in an empty working directory, when
    app.observability is imported, then no handler is installed on the root
    logger and no logs directory appears: configuration is an explicit
    composition-root act, not an import side effect (ADR-026, Round B).
    """
    code = (
        "import logging\n"
        "import pathlib\n"
        "import app.observability\n"
        "assert logging.getLogger().handlers == [], logging.getLogger().handlers\n"
        "assert not pathlib.Path('logs').exists()\n"
        "print('clean-import')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "clean-import" in result.stdout
    assert not (tmp_path / "logs").exists()


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
    """The allowlist is exactly the golden set of Rounds A, B, and 16.1.

    A change-detector golden test: a new allowlisted key cannot be added
    without this assertion changing, so widening the default-deny surface is a
    deliberate, reviewable act. Round B widened the set by the three stage
    timing fields of the @traced decorator (stage, duration_ms, status) and
    the seven startup_config provenance fields of the CLI composition root.
    Round 16.1 widened it by the four resolved store paths in startup_config
    (app_home, log_dir, raw_store, audit_log; S5) and the document_id of the
    raw-document access trace (H4/S4).
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
            "sink_size_bytes",
            "failed_processor",
            "caller_location",
            "stage",
            "duration_ms",
            "status",
            "git_sha",
            "model_id",
            "package_versions",
            "corpus_id",
            "allowlist_size",
            "tracing_enabled",
            "log_format",
            "app_home",
            "log_dir",
            "raw_store",
            "audit_log",
            "document_id",
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


def test_configure_raises_when_allowlist_missing_from_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The self-check fails loudly at configure time if the allowlist is absent.

    Given a chain builder that drops the default-deny allowlist processor, when
    configure_logging runs, then ProcessorChainError is raised at configure time
    (not per event), before any ungoverned sink is installed. The check now runs
    once at bootstrap; post-startup tampering is out of scope for the runtime
    path (ADR-026, phase separation).
    """
    original_build = logging_config._build_shared_processors

    def build_without_allowlist() -> list:
        return [
            processor
            for processor in original_build()
            if getattr(processor, "__wrapped__", None)
            is not logging_config._filter_allowlist
        ]

    monkeypatch.setattr(
        logging_config, "_build_shared_processors", build_without_allowlist
    )

    with pytest.raises(ProcessorChainError):
        configure_logging(log_dir=tmp_path, fmt="json")


def test_configure_against_a_file_path_raises_bootstrap_error(
    tmp_path: Path,
) -> None:
    """A log-dir path that is an existing file fails loud with the path named.

    Given a path that already exists as a file, when configure_logging targets
    it as the log directory, then ObservabilityBootstrapError is raised (no
    degradation) and its message names the offending path so the operator can
    act (ADR-026, strict bootstrap).
    """
    clash = tmp_path / "not_a_dir"
    clash.write_text("i am a file, not a directory\n", encoding="utf-8")

    with pytest.raises(ObservabilityBootstrapError) as exc_info:
        configure_logging(log_dir=clash, fmt="json")

    assert str(clash) in str(exc_info.value)


def test_configure_sweeps_expired_rotated_files(
    tmp_path: Path,
) -> None:
    """An over-age rotated file present before configure is gone after configure.

    Given an expired rotated log file, when configure_logging runs, then the
    wired-in sweep deletes it as part of bootstrap, so a startup always enforces
    the retention horizon (ADR-026, retention).
    """
    expired = tmp_path / f"{LOG_FILENAME}.2026-01-01"
    expired.write_text("expired rotated line\n", encoding="utf-8")
    expired_mtime = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    os.utime(expired, (expired_mtime, expired_mtime))

    configure_logging(log_dir=tmp_path, fmt="json", retention_days=30)

    assert not expired.exists()


def test_configure_emits_the_sink_size_event(
    tmp_path: Path,
) -> None:
    """A registered log_sink_size_bytes event appears in the sink after configure.

    Given a fresh sink, when configure_logging runs, then a governed
    log_sink_size_bytes event carrying a sink_size_bytes field is written, the
    startup signal for the Windows rotation failure mode (ADR-026).
    """
    configure_logging(log_dir=tmp_path, fmt="json")

    for handler in logging.getLogger().handlers:
        handler.flush()
    lines = [
        json.loads(line)
        for line in (tmp_path / LOG_FILENAME).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    size_events = [line for line in lines if line["event"] == LOG_SINK_SIZE_BYTES]
    assert len(size_events) == 1
    assert "sink_size_bytes" in size_events[0]


def test_unregistered_event_is_substituted_in_production(
    log_sink: Callable[[], list[dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With strict mode off, an unregistered event is substituted, not raised.

    Given strict mode disabled (production), when an f-string-shaped event name
    carrying a PII payload is logged, then exactly one sink line appears whose
    event is the unregistered_log_event constant with a caller_location, and the
    original interpolated text is nowhere in the sink: the unvetted name is
    discarded entirely (ADR-026, unbreakable runtime).
    """
    monkeypatch.delenv("OBSERVABILITY_STRICT", raising=False)
    secret = "citizen Max Mustermann leaked into an ad hoc event"

    structlog.get_logger().info(secret, count=1)

    lines = log_sink()
    assert len(lines) == 1
    record = lines[0]
    assert record["event"] == UNREGISTERED_LOG_EVENT
    assert "caller_location" in record
    assert secret not in json.dumps(record)
    assert "Max Mustermann" not in json.dumps(record)


def test_a_raising_processor_is_contained_as_processor_failed(
    log_sink: Callable[[], list[dict]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A processor exception is contained as a processor_failed line, no raise.

    Given strict mode off and a processor injected into the chain that always
    raises, when an event is logged, then the log call returns normally and the
    sink carries a processor_failed line naming the failing processor: no
    logging call can abort a business operation (ADR-026, unbreakable runtime).
    The log_sink fixture is requested before monkeypatch so its teardown
    restores a clean chain after the monkeypatch is undone.
    """
    monkeypatch.delenv("OBSERVABILITY_STRICT", raising=False)

    def boom(logger: object, method_name: str, event_dict: dict) -> dict:
        raise RuntimeError("processor blew up")

    original_build = logging_config._build_shared_processors

    def build_with_boom() -> list:
        chain = original_build()
        chain.insert(0, never_raise(boom))
        return chain

    monkeypatch.setattr(logging_config, "_build_shared_processors", build_with_boom)
    configure_logging(log_dir=tmp_path, fmt="json")

    # The call must return normally despite the always-raising processor.
    structlog.get_logger().error(AUDIT_APPEND_FAILED)

    failed = [line for line in log_sink() if line["event"] == PROCESSOR_FAILED]
    assert failed
    assert all(line["failed_processor"] == "boom" for line in failed)


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


def test_control_characters_in_a_foreign_message_do_not_reach_the_sink(
    log_sink: Callable[[], list[dict]],
) -> None:
    """Control characters are stripped from foreign message text before render.

    Given a third-party record whose message embeds a newline (a log-forging
    attempt), a NUL, and a tab, when it reaches the sink, then those control
    characters are absent from the rendered ``event`` value and the residual
    text is contiguous.
    """
    logging.getLogger("presidio-analyzer").error("line one\nFORGED two\x00\ttabbed")

    lines = log_sink()

    assert len(lines) == 1
    event = lines[0]["event"]
    assert "\n" not in event
    assert "\x00" not in event
    assert "\t" not in event
    assert event == "line oneFORGED twotabbed"


def test_oversized_foreign_event_arrives_truncated_with_marker(
    log_sink: Callable[[], list[dict]],
) -> None:
    """A foreign event longer than the cap is truncated with a literal marker.

    Given a third-party record whose message exceeds MAX_FOREIGN_EVENT_CHARS,
    when it reaches the sink, then the ``event`` value is the first
    MAX_FOREIGN_EVENT_CHARS characters followed by a literal ``[truncated]``
    marker, bounding the unredacted foreign-message residual (ADR-026).
    """
    logging.getLogger("presidio-analyzer").error("A" * 500)

    lines = log_sink()

    assert len(lines) == 1
    event = lines[0]["event"]
    assert event == "A" * MAX_FOREIGN_EVENT_CHARS + "[truncated]"
