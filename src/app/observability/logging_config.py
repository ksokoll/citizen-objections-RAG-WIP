"""One-sink, default-deny logging configuration (ADR-026).

All log output, from our structlog calls and from third-party stdlib loggers
alike, passes through a single shared processor chain into a single file handler
that appends to one log file. The chain enforces default-deny by both key and
origin, following lift-stamp-filter ordering (ADR-026): foreign data is lifted
first or not at all, authoritative truth is stamped after, filtering is last.
The controls at the sink:

- default-deny by origin: the chain has no extra-merging processor, so a
  foreign record's ``extra`` fields are never lifted into the event dict at all;
- authoritative stamps (correlation id, level, timestamp) assign
  unconditionally, so a pre-existing key from an own-code kwarg cannot spoof
  them;
- value normalization (sanitize_values), so control characters are stripped
  from every string value and a foreign event message is length-bounded before
  rendering;
- a default-deny key allowlist (allowed_keys(), the root-assembled union of
  each context's declared keys plus the CLI keys), so a field is invisible
  until it is allowlisted on purpose;
- a registered event vocabulary (events.registered_events(), the root-assembled
  union of each context's declared events), so a structlog event name that is
  not a registered constant fails loudly;
- exception reduction to type plus location, so an exception message (foreign
  authored text) is never written to disk.

structlog routes into stdlib via ProcessorFormatter.wrap_for_formatter; foreign
stdlib records route through the same shared processors via
ProcessorFormatter(foreign_pre_chain=shared). Configuration is an explicit
composition-root call: the CLI entrypoint calls configure_logging(log_dir=...)
before any pipeline work, with the sink path passed as a parameter resolved at
the entrypoint, never read from the process environment here (security finding
5; the Round 15.2 import-time stopgap is retired, ADR-026 phase separation).
The test suite configures via an explicit session fixture in conftest.

The two enforcement phases are deliberately separated (ADR-026, phase
separation): strict at configuration time, unbreakable at request time.

- Strict bootstrap. configure_logging() fails loud: any directory, handler, or
  structlog setup failure becomes ObservabilityBootstrapError with an
  actionable message, and a missing default-deny allowlist raises
  ProcessorChainError, both at configure time. There is no degradation to a
  NullHandler or bare stderr, because running without the governed sink would
  be fail-open for the central PII control (ADR-026, no-degradation rationale).
  The allowlist self-check therefore runs once at configure time, not per event.
- Unbreakable runtime. Every own processor is wrapped by never_raise so a
  processor exception becomes a substitute processor_failed event rather than
  propagating into the business call. The event-vocabulary check is
  mode-dependent: in strict mode (OBSERVABILITY_STRICT=1, set by the test
  suite) an unregistered name raises so CI catches every typo; in production it
  substitutes the unregistered_log_event constant plus the caller location and
  discards the original name entirely (it is potential payload).
"""

from __future__ import annotations

import functools
import logging
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from types import FrameType
from typing import cast

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from app.observability.correlation import add_correlation_id
from app.observability.events import (
    LOG_SINK_SIZE_BYTES,
    PROCESSOR_FAILED,
    UNREGISTERED_LOG_EVENT,
    UnregisteredLogEventError,
    registered_events,
)

#: Module logger for the sink self-checks. Routes through the same governed
#: chain as every other event.
_log = structlog.get_logger()

LOG_FILENAME: str = "observability.log"

#: Name of the environment variable the composition root (the CLI) reads to
#: decide strict mode. It is read only at the root and passed to
#: configure_logging (ADR-026, composition-root wiring: behavior flags are
#: resolved once at the root, never live from the environment deep in the
#: stack); the processor chain consults the wired
#: _STRICT_MODE flag, never the environment. Strict (1): an unregistered event
#: name or key raises and a processor exception propagates, so CI catches every
#: typo and bug. Unset (production): the same conditions are contained and
#: never reach the business call (ADR-026, phase separation).
ENV_STRICT: str = "OBSERVABILITY_STRICT"

#: The wired strict-mode flag, resolved once at the composition root via
#: set_strict_mode. Production-safe default (False): the runtime is unbreakable
#: unless a root opts into strict CI enforcement. The CLI passes the
#: OBSERVABILITY_STRICT reading; the test conftest sets it True.
_STRICT_MODE: bool = False

#: Upper bound on a foreign record's ``event`` value (the arbitrary third-party
#: message text). Bounds the unredacted foreign-message residual; closure
#: remains with the deferred sink scan and redaction (ADR-026).
MAX_FOREIGN_EVENT_CHARS: int = 200

#: Translation table that deletes the C0 control characters (\x00-\x1f),
#: including newlines, carriage returns, and tabs, from string values so a
#: foreign record cannot forge log lines or inject terminal control sequences.
_CONTROL_CHAR_TABLE: dict[int, None] = {codepoint: None for codepoint in range(0x20)}

#: The observability layer's own allowlisted log keys (ADR-026, default-deny).
#: These are the mechanism's own fields, not domain knowledge, so they live
#: here and seed the registry at import: the chain's authoritative stamps and
#: exception reduction (event, level, timestamp, correlation_id, exc_type,
#: exc_location), the self-instrumentation fields (sink_size_bytes,
#: failed_processor, caller_location), and the @traced decorator's stage timing
#: fields (stage, duration_ms, status). Each is operational metadata, never
#: payload. Domain field names are declared per context in <context>/events.py
#: and unioned in at the composition root (H3).
OBSERVABILITY_KEYS: frozenset[str] = frozenset(
    {
        "event",
        "level",
        "timestamp",
        "correlation_id",
        "exc_type",
        "exc_location",
        "sink_size_bytes",
        "failed_processor",
        "caller_location",
        "stage",
        "duration_ms",
        "status",
    }
)

#: The live key allowlist the chain enforces against, assembled at runtime.
#: Default-deny: every key not in the assembled set is dropped before the record
#: is rendered. Seeded with the layer's own keys; the composition root adds each
#: context's declared keys plus the CLI/self-instrumentation keys via
#: register_keys. Mutable on purpose: the allowlist is a root-assembled union,
#: not a frozen central god-list pinned by a golden test (H3). The reset hook
#: returns it to the seed between tests.
_registered_keys: set[str] = set(OBSERVABILITY_KEYS)


def register_keys(keys: Iterable[str]) -> None:
    """Union a set of allowlisted key names into the live allowlist.

    Called by the composition root with each context's declared keys
    (TRIAGE_KEYS, INGESTION_KEYS, ...) plus the root's own CLI keys. Idempotent:
    registering the same keys twice is a no-op union, so two roots (the CLI and
    the test conftest) calling it does not conflict.

    Args:
        keys: Allowlisted field-name constants to register.
    """
    _registered_keys.update(keys)


def allowed_keys() -> frozenset[str]:
    """Return the currently allowlisted log keys.

    The allowlist processor consults this rather than a module-level frozen
    set, so the allowlist is the root-assembled union of the layer's own keys
    and the per-context declarations plus the CLI keys (H3).
    """
    return frozenset(_registered_keys)


def reset_registered_keys() -> None:
    """Reset the key allowlist to the layer's own keys (test hook).

    Returns the allowlist to its import-time seed (the observability self
    keys), discarding any context or CLI keys a root unioned in. Part of the
    symmetric between-tests reset so a test that registers a key cannot leak it
    into a later test.
    """
    _registered_keys.clear()
    _registered_keys.update(OBSERVABILITY_KEYS)


#: ProcessorFormatter meta keys that must survive the allowlist so the
#: formatter's remove_processors_meta can strip them after rendering decisions.
_META_KEYS: frozenset[str] = frozenset({"_record", "_from_structlog"})

#: Third-party loggers clamped to WARNING so their INFO/DEBUG chatter never
#: reaches the sink. What does reach it still passes the allowlist.
_THIRD_PARTY_LOGGERS: tuple[str, ...] = (
    "presidio-analyzer",
    "presidio-anonymizer",
    "opentelemetry",
    "urllib3",
    "sentence_transformers",
    "faiss",
    "httpx",
    "httpcore",
)

#: The handler this module installed, tracked so a reconfigure replaces it
#: rather than stacking a second sink.
_INSTALLED_HANDLER: logging.Handler | None = None


class ProcessorChainError(Exception):
    """Raised by the configure-time self-check when the allowlist processor is
    not in the active structlog configuration.

    Signals that a later reconfiguration (a refactor, a test setup, a migration
    script) removed the default-deny control. The fix is to restore the
    allowlist processor, not to suppress the error. The check runs once at
    configure time, not per event: post-startup chain tampering is out of scope
    for the runtime path (ADR-026, phase separation).
    """


class ObservabilityBootstrapError(Exception):
    """Raised when the logging configuration cannot be installed at startup.

    Fail-loud, no degradation (ADR-026): a directory, handler, or structlog
    setup failure aborts configuration with a named, actionable message
    (operation, path, what to check) rather than falling back to a NullHandler
    or bare stderr. Running the pipeline without its governed sink would be
    fail-open for the central PII control, so a bootstrap failure must stop the
    process, not silently downgrade it.
    """


class UnregisteredLogKeyError(Exception):
    """Raised in strict mode when a registered own event carries a key that is
    not in the assembled allowlist.

    The key allowlist is default-deny: a non-allowlisted key is dropped before
    the record is rendered (ADR-026). In strict mode (the test suite) that
    silent drop becomes a loud failure, the sibling of UnregisteredLogEventError
    for keys rather than for event names, so a mistyped or unallowlisted field
    fails in CI at its origin instead of vanishing from the line. The fix is to
    declare the key in the emitting context's events.py (its *_KEYS set) and
    union it at the composition root via register_keys, not to suppress the
    error.

    Foreign stdlib records are exempt: their fields are governed by origin
    (extras are never merged into the event dict), so the raise is gated on our
    own events via the _from_structlog discriminant.
    """


def set_strict_mode(enabled: bool) -> None:
    """Set the wired strict-mode flag (ADR-026, composition-root wiring).

    Strict mode is resolved once at the root and set here, not read live from
    the environment inside the processor chain. The CLI reads OBSERVABILITY_STRICT
    and passes it through configure_logging; the test conftest sets it True
    directly. Independent of configure_logging so a test can toggle enforcement
    without reconfiguring the sink (ADR-026, phase separation).

    Args:
        enabled: True for strict CI enforcement (unregistered names and keys
            raise, processor exceptions propagate), False for the unbreakable
            production runtime (the same conditions are contained).
    """
    global _STRICT_MODE
    _STRICT_MODE = enabled


def _is_strict() -> bool:
    """Return whether runtime enforcement is strict (the wired flag).

    Strict mode is the wired _STRICT_MODE flag, set once at the composition
    root via set_strict_mode, not read from the environment deep in the chain
    (ADR-026, composition-root wiring). A test toggles the mode by calling
    set_strict_mode, so a chain reconfiguration to a new sink path does not flip
    enforcement (ADR-026, phase separation).
    """
    return _STRICT_MODE


#: Path substrings whose frames are skipped when locating the caller of an
#: unregistered event: structlog internals, the stdlib logging package, and
#: this module. The first frame outside them is the application call site.
_CALLER_SKIP_MARKERS: tuple[str, ...] = (
    f"{os.sep}structlog{os.sep}",
    f"{os.sep}logging{os.sep}",
    "logging_config.py",
)


def _caller_location() -> str:
    """Return ``basename:lineno`` of the first application frame above us.

    Walks the stack past structlog internals, the stdlib logging package, and
    this module, returning the first application frame. Best-effort: returns
    ``unknown`` if no such frame is found. The location carries no payload, only
    where an unregistered event name was logged so the typo can be fixed.
    """
    frame: FrameType | None = sys._getframe(1)
    while frame is not None:
        filename = frame.f_code.co_filename
        if not any(marker in filename for marker in _CALLER_SKIP_MARKERS):
            return f"{os.path.basename(filename)}:{frame.f_lineno}"
        frame = frame.f_back
    return "unknown"


def never_raise(processor: Processor) -> Processor:
    """Wrap an own processor so a runtime exception can never reach the caller.

    Round A enforcement could abort a business call from inside the telemetry:
    a processor exception propagated out of the log call. This wrapper contains
    that. On a processor exception, the original event dict is discarded (a
    processor that failed mid-chain may hold half-processed, untrusted data) and
    replaced by a substitute PROCESSOR_FAILED event naming the failing
    processor, which then flows through the remaining chain and the allowlist.
    The business call returns normally.

    The single exception is strict mode (OBSERVABILITY_STRICT=1, the test
    suite): there the wrapper re-raises so CI catches both typos and processor
    bugs. Enforcement belongs where the error originates (CI), not where it
    happens to surface (the request path) (ADR-026, phase separation).
    """

    @functools.wraps(processor)
    def wrapper(
        logger: WrappedLogger, method_name: str, event_dict: EventDict
    ) -> EventDict:
        try:
            # never_raise wraps our own processors, which return EventDict; the
            # structlog Processor return type is the broader renderer union.
            return cast(EventDict, processor(logger, method_name, event_dict))
        except Exception:
            if _is_strict():
                raise
            return {
                "event": PROCESSOR_FAILED,
                "failed_processor": processor.__name__,
                "_from_structlog": event_dict.get("_from_structlog"),
            }

    return wrapper


def _assert_allowlist_in_chain() -> None:
    """Configure-time self-check: the allowlist processor is in the active chain.

    Runs once during configure_logging, after structlog.configure. The allowlist
    processor is wrapped by never_raise in the chain, so the check accepts either
    the function itself or a wrapper whose ``__wrapped__`` is it.

    Raises:
        ProcessorChainError: If _filter_allowlist is absent from the configured
            structlog processors (directly or as a never_raise wrapper target).
    """
    processors = structlog.get_config().get("processors", [])
    for processor in processors:
        if processor is _filter_allowlist or (
            getattr(processor, "__wrapped__", None) is _filter_allowlist
        ):
            return
    raise ProcessorChainError(
        "default-deny allowlist processor missing from the logging chain; "
        "restore _filter_allowlist in _build_shared_processors"
    )


def _enforce_event_vocabulary(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Reject structlog events whose name is not a registered constant.

    Foreign stdlib records (marked by ProcessorFormatter with
    ``_from_structlog`` set to False) are exempt: their message is arbitrary by
    nature. structlog-originated events carry no ``_from_structlog`` marker at
    this stage, so the absence of the marker identifies our own events.

    Mode-dependent (ADR-026, phase separation):

    - Strict mode (the test suite): an unregistered name raises
      UnregisteredLogEventError, so CI catches every typo at its origin.
    - Production: the original name is discarded entirely (it is potential
      payload, e.g. an interpolated f-string), and the event is replaced by the
      UNREGISTERED_LOG_EVENT constant plus the caller location, so the typo is
      locatable without writing the unvetted name to disk.

    Raises:
        UnregisteredLogEventError: In strict mode, if a structlog event name is
            not in the registered vocabulary (events.registered_events()).
    """
    if event_dict.get("_from_structlog") is False:
        return event_dict
    event_name = event_dict.get("event")
    if event_name in registered_events():
        return event_dict
    if _is_strict():
        raise UnregisteredLogEventError(
            f"log event {event_name!r} is not a registered constant; "
            "declare it in the emitting context's events.py and union it at "
            "the composition root via register_events"
        )
    # Production: discard the original name (potential payload) and substitute
    # the registered constant plus the caller location. Authoritative stamps
    # already on the dict (correlation_id, level, timestamp) are preserved; the
    # untrusted original name and any other own-code keys are dropped.
    location = _caller_location()
    substitute: EventDict = {
        key: value
        for key, value in event_dict.items()
        if key in ("level", "timestamp", "correlation_id", "_from_structlog")
    }
    substitute["event"] = UNREGISTERED_LOG_EVENT
    substitute["caller_location"] = location
    return substitute


def _reduce_exception(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Reduce any attached exception to type plus location, never message.

    Replaces exc_info (and any pre-rendered exception text) with exc_type and
    exc_location (basename:lineno of the originating frame). The exception
    message, which is foreign-authored text of unknown content, is discarded
    (ADR-026, exception policy).
    """
    exc_info = event_dict.pop("exc_info", None)
    # Drop any rendered exception text a stdlib formatter or renderer added.
    event_dict.pop("exception", None)
    event_dict.pop("exc_text", None)

    if not exc_info:
        return event_dict

    if exc_info is True:
        import sys

        exc_info = sys.exc_info()

    if isinstance(exc_info, BaseException):
        exc: BaseException | None = exc_info
        traceback_obj = exc_info.__traceback__
    elif isinstance(exc_info, tuple) and len(exc_info) == 3:
        exc = exc_info[1]
        traceback_obj = exc_info[2]
    else:
        return event_dict

    if exc is None:
        return event_dict

    event_dict["exc_type"] = type(exc).__name__
    if traceback_obj is not None:
        import traceback as _traceback

        frames = _traceback.extract_tb(traceback_obj)
        if frames:
            last = frames[-1]
            filename = os.path.basename(last.filename)
            event_dict["exc_location"] = f"{filename}:{last.lineno}"
    return event_dict


def _sanitize_values(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Normalize and bound every string value before rendering.

    Two controls, applied last before the allowlist (ADR-026, lift-stamp-filter:
    filter last):

    - Control-character strip. Every string value has the C0 control characters
      (``\\x00``-``\\x1f``, including newlines, carriage returns, and tabs)
      removed, so a foreign record cannot forge a second log line or inject
      terminal control sequences through the ``event`` message or any other
      string field.
    - Foreign-event length bound. A foreign record's ``event`` value (the
      arbitrary third-party message text) is capped at MAX_FOREIGN_EVENT_CHARS
      and a literal ``[truncated]`` marker appended, bounding the unredacted
      foreign-message residual that the allowlist cannot inspect. Our own events
      are registered constants, so the cap targets foreign messages only.
    """
    is_foreign = event_dict.get("_from_structlog") is False
    for key, value in event_dict.items():
        if not isinstance(value, str):
            continue
        cleaned = value.translate(_CONTROL_CHAR_TABLE)
        if is_foreign and key == "event" and len(cleaned) > MAX_FOREIGN_EVENT_CHARS:
            cleaned = cleaned[:MAX_FOREIGN_EVENT_CHARS] + "[truncated]"
        event_dict[key] = cleaned
    return event_dict


def _filter_allowlist(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Default-deny: keep only allowlisted keys and the formatter meta keys.

    Every key not in the assembled allowlist (allowed_keys()) is dropped.
    Foreign ``extra`` fields are not
    lifted into the event dict in the first place (default-deny by origin: the
    chain has no extra-merging processor), so this is the key control's backstop
    for own-code and structlog-internal keys rather than the foreign-extra gate.
    The two ProcessorFormatter meta keys are preserved so the formatter can
    strip them itself after this point.

    Mode-dependent, the sibling of the event-vocabulary check (ADR-026, phase
    separation):

    - Strict mode (the test suite): an own event carrying a key that is neither
      allowlisted nor a formatter meta key raises UnregisteredLogKeyError, so a
      mistyped or unallowlisted field fails in CI instead of being dropped
      silently. Gated on own code via the _from_structlog discriminant: a
      foreign record never raises.
    - Production: the non-allowlisted key is dropped, never raised, so a stray
      key can never abort the request path (unbreakable runtime).

    Raises:
        UnregisteredLogKeyError: In strict mode, if an own event carries a key
            that is not in the assembled allowlist (allowed_keys()) or
            _META_KEYS.
    """
    allowlist = allowed_keys()
    is_own_event = event_dict.get("_from_structlog") is not False
    if _is_strict() and is_own_event:
        for key in event_dict:
            if key not in allowlist and key not in _META_KEYS:
                raise UnregisteredLogKeyError(
                    f"log key {key!r} on event {event_dict.get('event')!r} is "
                    "not allowlisted; declare it in the emitting context's "
                    "events.py (its *_KEYS set) and union it at the composition "
                    "root via register_keys"
                )
    return {
        key: value
        for key, value in event_dict.items()
        if key in allowlist or key in _META_KEYS
    }


def _build_shared_processors() -> list[Processor]:
    """Build the shared chain used for both structlog and foreign records.

    Order (ADR-026, lift-stamp-filter): contextvars merge, then the
    authoritative stamps (correlation, log level, timestamp) that assign
    unconditionally and so cannot be spoofed by a pre-existing key, then
    vocabulary enforcement and exception reduction, then value normalization,
    then the allowlist last before handoff. The chain has no extra-merging
    processor: foreign ``extra`` data is default-denied by origin and never
    lifted into the event dict at all.

    Every own processor is wrapped by never_raise so a runtime exception is
    contained as a substitute event rather than aborting the business call
    (ADR-026, unbreakable runtime). The structlog built-ins (contextvars merge,
    add_log_level, TimeStamper) are left unwrapped. The allowlist's presence is
    verified once at configure time by _assert_allowlist_in_chain, not per event.
    """
    return [
        structlog.contextvars.merge_contextvars,
        never_raise(add_correlation_id),
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        never_raise(_enforce_event_vocabulary),
        never_raise(_reduce_exception),
        never_raise(_sanitize_values),
        never_raise(_filter_allowlist),
    ]


def _build_renderer(fmt: str) -> Processor:
    """Return the final renderer for the resolved output format.

    JSON is the default and the mandatory renderer in security-relevant
    environments (ADR-026); the console renderer is a developer convenience
    only.
    """
    if fmt == "console":
        return structlog.dev.ConsoleRenderer(colors=False)
    return structlog.processors.JSONRenderer()


def _emit_log_sink_size(log_dir: Path) -> None:
    """Emit the active sink file size once, after configuration.

    Surfaces the Windows rotation failure mode (ADR-026): if a second process
    holds the active file open, the midnight rename fails silently and the file
    grows without bound. The size reported at the next startup makes that
    visible. The file may not exist yet (the handler opens with delay), in which
    case the size is 0.
    """
    log_path = log_dir / LOG_FILENAME
    size_bytes = log_path.stat().st_size if log_path.exists() else 0
    _log.info(LOG_SINK_SIZE_BYTES, sink_size_bytes=size_bytes)


def configure_logging(
    log_dir: Path,
    fmt: str = "json",
    strict: bool | None = None,
) -> None:
    """Install the one-sink, default-deny logging configuration.

    Strict at bootstrap (ADR-026, phase separation): a directory, handler, or
    structlog setup failure raises ObservabilityBootstrapError with an
    actionable message, and a missing allowlist raises ProcessorChainError. No
    degradation to a NullHandler or bare stderr. Idempotent: a second call
    replaces the handler this module installed rather than stacking a second
    sink. Called explicitly by the composition root (the CLI entrypoint, the
    conftest fixture); the sink path is a parameter resolved at the entrypoint,
    never an environment read in this module (ADR-026, composition-root wiring).

    Args:
        log_dir: Sink directory, resolved by the caller at the entrypoint.
        fmt: Output format, "json" or "console".
        strict: When None (default) the current strict mode is left unchanged,
            so reconfiguring the sink path does not flip enforcement; a
            composition root passes an explicit bool (the CLI from
            OBSERVABILITY_STRICT, the test conftest True). Forwarded to
            set_strict_mode (ADR-026, composition-root wiring).

    Raises:
        ObservabilityBootstrapError: If the log directory, the structlog chain,
            or the sink handler cannot be set up.
        ProcessorChainError: If the default-deny allowlist processor is not in
            the configured chain.
    """
    global _INSTALLED_HANDLER

    if strict is not None:
        set_strict_mode(strict)

    resolved_dir = log_dir
    resolved_fmt = fmt.lower()

    try:
        resolved_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ObservabilityBootstrapError(
            f"could not create the log directory '{resolved_dir}': "
            "check that the path is a directory and not an existing file, that "
            "the parent exists and is writable, and that the filesystem is not "
            "read-only"
        ) from exc

    shared = _build_shared_processors()

    try:
        structlog.configure(
            processors=[
                *shared,
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            # Disabled so tests can reconfigure the chain and have the change
            # take effect on the next event.
            cache_logger_on_first_use=False,
        )
    except Exception as exc:
        raise ObservabilityBootstrapError(
            "could not configure the structlog processor chain: check the "
            "observability.logging_config processor definitions"
        ) from exc

    # Configure-time self-check (not per event): the default-deny control must
    # be in the chain we just installed, or bootstrap fails loud.
    _assert_allowlist_in_chain()

    try:
        formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                _build_renderer(resolved_fmt),
            ],
        )
        handler = logging.FileHandler(
            filename=resolved_dir / LOG_FILENAME,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(formatter)
    except OSError as exc:
        raise ObservabilityBootstrapError(
            f"could not open the log sink file '{resolved_dir / LOG_FILENAME}': "
            "check directory permissions and that no other process holds the "
            "active file open"
        ) from exc

    root = logging.getLogger()
    if _INSTALLED_HANDLER is not None and _INSTALLED_HANDLER in root.handlers:
        root.removeHandler(_INSTALLED_HANDLER)
        _INSTALLED_HANDLER.close()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    _INSTALLED_HANDLER = handler

    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Sink is configured; emit its size through the governed chain.
    _emit_log_sink_size(resolved_dir)
