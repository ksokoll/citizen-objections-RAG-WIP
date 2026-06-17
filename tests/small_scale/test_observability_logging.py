"""Behaviour tests for the one-sink, default-deny logging chain (ADR-026).

Every control is asserted at the sink (the rendered log file), not at the call
site, because the point of the architecture is that the control cannot be
bypassed by choosing a different logging API. The log_sink fixture redirects
the single handler to a tmp path and returns the parsed JSON lines.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
import structlog

from app.audit_log.events import AUDIT_APPEND_FAILED
from app.observability import logging_config
from app.observability.correlation import correlation_scope
from app.observability.events import (
    PROCESSOR_FAILED,
    UNREGISTERED_LOG_EVENT,
    UnregisteredLogEventError,
)
from app.observability.logging_config import (
    LOG_FILENAME,
    MAX_FOREIGN_EVENT_CHARS,
    ProcessorChainError,
    UnregisteredLogKeyError,
    _filter_allowlist,
    configure_logging,
    never_raise,
    set_strict_mode,
)


@pytest.fixture()
def log_sink(tmp_path: Path) -> Callable[[], list[dict]]:
    """Redirect the single sink to tmp_path; return a JSON-lines reader.

    A per-test assertion sees only the lines the test produced. Teardown
    restores a good configuration in the same tmp path so a test that
    deliberately breaks the chain does not leak a broken structlog config into
    later tests.
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


def test_unregistered_key_on_a_registered_event_raises_in_strict_mode(
    log_sink: Callable[[], list[dict]],
) -> None:
    """A registered event carrying a non-allowlisted key fails loudly (strict).

    The sibling of the event-name check: in strict mode a key that is not in
    ALLOWED_KEYS does not vanish silently from the line, it raises
    UnregisteredLogKeyError at the allowlist processor, so a mistyped or
    unallowlisted field is caught in CI at its origin (ADR-026, enforcement at
    origin). The registered event passes the vocabulary check first, so the
    raise is attributable to the key, not the name.
    """
    log = structlog.get_logger()

    with pytest.raises(UnregisteredLogKeyError):
        log.error(AUDIT_APPEND_FAILED, not_an_allowlisted_key="x")


def test_unregistered_key_is_dropped_in_production_and_the_line_survives(
    log_sink: Callable[[], list[dict]],
) -> None:
    """With strict off, a non-allowlisted key is dropped, the rest survives.

    Production keeps the silent default-deny drop (unbreakable runtime): the
    unallowlisted key and its value are absent, while the event and its
    allowlisted fields reach the sink. A stray key must never abort the request
    path. Strict is the wired flag now, so production is selected with
    set_strict_mode(False); the autouse fixture restores strict for the next
    test.
    """
    set_strict_mode(False)

    structlog.get_logger().error(
        AUDIT_APPEND_FAILED,
        audit_event_type="triage",
        leaked="citizen Max Mustermann",
    )

    lines = log_sink()
    assert len(lines) == 1
    record = lines[0]
    assert record["event"] == AUDIT_APPEND_FAILED
    assert record["audit_event_type"] == "triage"
    assert "leaked" not in record
    assert "Max Mustermann" not in json.dumps(record)


def test_foreign_record_with_a_stray_key_is_dropped_not_raised_in_strict_mode() -> None:
    """The strict key-raise is gated on own code; a foreign record never raises.

    The discriminant: a foreign record (``_from_structlog`` is False) carrying a
    non-allowlisted key is dropped silently even in strict mode, because foreign
    fields are governed by origin and the loud failure targets our own events
    only. Asserted on the processor directly, since the chain never merges a
    foreign extra into the event dict to begin with (the only way to present
    one here).
    """
    foreign_record = {
        "event": "presidio analysis complete",
        "_from_structlog": False,
        "pii_field": "Max Mustermann",
    }

    result = _filter_allowlist(None, "error", dict(foreign_record))

    assert "pii_field" not in result
    assert result["event"] == "presidio analysis complete"


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


def test_unregistered_event_is_substituted_in_production(
    log_sink: Callable[[], list[dict]],
) -> None:
    """With strict mode off, an unregistered event is substituted, not raised.

    Given strict mode disabled (production), when an f-string-shaped event name
    carrying a PII payload is logged, then exactly one sink line appears whose
    event is the unregistered_log_event constant with a caller_location, and the
    original interpolated text is nowhere in the sink: the unvetted name is
    discarded entirely (ADR-026, unbreakable runtime).
    """
    set_strict_mode(False)
    secret = "citizen Max Mustermann leaked into an ad hoc event"

    structlog.get_logger().info(secret, count=1)

    lines = log_sink()
    assert len(lines) == 1
    record = lines[0]
    assert record["event"] == UNREGISTERED_LOG_EVENT
    assert "caller_location" in record
    assert secret not in json.dumps(record)
    assert "Max Mustermann" not in json.dumps(record)


def test_strict_mode_follows_the_wired_flag_not_the_environment(
    log_sink: Callable[[], list[dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict enforcement follows the wired flag; the environment is ignored.

    Given OBSERVABILITY_STRICT in the environment set to the opposite of the
    wired value, when an unregistered event is logged, then the wired flag
    decides: strict wired on raises regardless of the env, because the chain
    reads the wired flag and never the environment (finding 8, flags are wiring,
    not ambient reads).
    """
    monkeypatch.setenv("OBSERVABILITY_STRICT", "0")
    set_strict_mode(True)

    with pytest.raises(UnregisteredLogEventError):
        structlog.get_logger().info("ad.hoc.unregistered.event")


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
    set_strict_mode(False)

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


def test_correlation_id_is_stamped_inside_the_scope_and_cleared_after(
    log_sink: Callable[[], list[dict]],
) -> None:
    """The correlation scope stamps the id inside and clears it on exit.

    The run-scoped correlation lifecycle that run() relies on is the ContextVar
    via correlation_scope, not a value threaded into the chain: an event logged
    inside the scope carries the id at the sink, and an event logged after the
    scope carries none, so the id cannot leak past the run and the transport is
    the ambient ContextVar, not a degraded attribute.
    """
    log = structlog.get_logger()

    with correlation_scope("doc-scope-0001"):
        log.error(AUDIT_APPEND_FAILED, audit_event_type="inside")
    log.error(AUDIT_APPEND_FAILED, audit_event_type="after")

    lines = log_sink()
    inside = next(line for line in lines if line.get("audit_event_type") == "inside")
    after = next(line for line in lines if line.get("audit_event_type") == "after")
    assert inside["correlation_id"] == "doc-scope-0001"
    assert "correlation_id" not in after


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
