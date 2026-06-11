"""One-sink, default-deny logging configuration (ADR-026).

All log output, from our structlog calls and from third-party stdlib loggers
alike, passes through a single shared processor chain into a single
TimedRotatingFileHandler. The chain enforces default-deny by both key and
origin, following lift-stamp-filter ordering (ADR-026): foreign data is lifted
first or not at all, authoritative truth is stamped after, filtering is last.
The controls at the sink:

- default-deny by origin: the chain has no extra-merging processor, so a
  foreign record's ``extra`` fields are never lifted into the event dict at all;
- authoritative stamps (correlation id, level, timestamp) assign
  unconditionally, so a pre-existing key from an own-code kwarg cannot spoof
  them;
- a default-deny key allowlist (ALLOWED_KEYS), so a field is invisible until
  it is allowlisted on purpose;
- a registered event vocabulary (events.REGISTERED_EVENTS), so a structlog
  event name that is not a registered constant fails loudly;
- exception reduction to type plus location, so an exception message (foreign
  authored text) is never written to disk.

structlog routes into stdlib via ProcessorFormatter.wrap_for_formatter; foreign
stdlib records route through the same shared processors via
ProcessorFormatter(foreign_pre_chain=shared). Configuration runs as an
import-time side effect (configure_logging() at module bottom) so there is no
bootstrap window before the controls are installed. A self-check processor
asserts the allowlist is still in the active chain on every event.

Retention is time-based: the handler rotates at midnight UTC and keeps
RETENTION_DAYS backups; sweep_expired_logs() removes over-age rotated files by
mtime, covering boundaries the process was not alive for.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from app.observability.correlation import add_correlation_id
from app.observability.events import REGISTERED_EVENTS, UnregisteredLogEventError

#: Documented placeholder retention. The legal determination of the period is
#: out of scope (ADR-026, Retention).
RETENTION_DAYS: int = 30

#: Default sink directory, overridable via OBSERVABILITY_LOG_DIR.
DEFAULT_LOG_DIR: Path = Path("logs")
LOG_FILENAME: str = "observability.log"

ENV_LOG_DIR: str = "OBSERVABILITY_LOG_DIR"
ENV_FORMAT: str = "OBSERVABILITY_FORMAT"

#: The frozen key allowlist (ADR-026). Default-deny: every other key is dropped
#: before the record is rendered. Frozen by a golden test; a key cannot be
#: added without that test changing. Rounds B and C extend this set
#: deliberately (timing, status, metric fields).
ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "event",
        "level",
        "timestamp",
        "correlation_id",
        "audit_event_type",
        "exc_type",
        "exc_location",
        # Operational counts and a mode string for the two governed
        # DocumentIngestion warnings. Counts only: the PII coverage anomaly
        # never carries the surviving tokens, only how many survived.
        "survivor_count",
        "name_regions_masked",
        "store_mode",
    }
)

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
    """Raised by the self-check when the allowlist processor is not in the
    active structlog configuration.

    Signals that a later reconfiguration (a refactor, a test setup, a migration
    script) removed the default-deny control. The fix is to restore the
    allowlist processor, not to suppress the error.
    """


def _self_check(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Assert the allowlist processor is present in the active chain.

    Runs on every event. If a reconfiguration dropped _filter_allowlist from
    the structlog configuration, the next event raises rather than emitting
    through an ungoverned chain.

    Raises:
        ProcessorChainError: If _filter_allowlist is absent from the configured
            structlog processors.
    """
    processors = structlog.get_config().get("processors", [])
    if _filter_allowlist not in processors:
        raise ProcessorChainError(
            "default-deny allowlist processor missing from the logging chain"
        )
    return event_dict


def _enforce_event_vocabulary(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Reject structlog events whose name is not a registered constant.

    Foreign stdlib records (marked by ProcessorFormatter with
    ``_from_structlog`` set to False) are exempt: their message is arbitrary by
    nature. structlog-originated events carry no ``_from_structlog`` marker at
    this stage, so the absence of the marker identifies our own events.

    Raises:
        UnregisteredLogEventError: If a structlog event name is not in
            REGISTERED_EVENTS.
    """
    if event_dict.get("_from_structlog") is False:
        return event_dict
    event_name = event_dict.get("event")
    if event_name not in REGISTERED_EVENTS:
        raise UnregisteredLogEventError(
            f"log event {event_name!r} is not a registered constant; "
            "add it to observability.events.REGISTERED_EVENTS"
        )
    return event_dict


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


def _filter_allowlist(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Default-deny: keep only allowlisted keys and the formatter meta keys.

    Every key not in ALLOWED_KEYS is dropped. Foreign ``extra`` fields are not
    lifted into the event dict in the first place (default-deny by origin: the
    chain has no extra-merging processor), so this is the key control's backstop
    for own-code and structlog-internal keys rather than the foreign-extra gate.
    The two ProcessorFormatter meta keys are preserved so the formatter can
    strip them itself after this point.
    """
    return {
        key: value
        for key, value in event_dict.items()
        if key in ALLOWED_KEYS or key in _META_KEYS
    }


def _build_shared_processors() -> list[Processor]:
    """Build the shared chain used for both structlog and foreign records.

    Order (ADR-026, lift-stamp-filter): self-check, contextvars merge, then the
    authoritative stamps (correlation, log level, timestamp) that assign
    unconditionally and so cannot be spoofed by a pre-existing key, then
    vocabulary enforcement and exception reduction, then the allowlist last
    before handoff. The chain has no extra-merging processor: foreign ``extra``
    data is default-denied by origin and never lifted into the event dict at
    all.
    """
    return [
        _self_check,
        structlog.contextvars.merge_contextvars,
        add_correlation_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _enforce_event_vocabulary,
        _reduce_exception,
        _filter_allowlist,
    ]


def _build_renderer(fmt: str) -> Processor:
    """Return the final renderer for the resolved OBSERVABILITY_FORMAT."""
    if fmt == "console":
        return structlog.dev.ConsoleRenderer(colors=False)
    return structlog.processors.JSONRenderer()


def configure_logging(
    log_dir: Path | None = None,
    fmt: str | None = None,
    retention_days: int = RETENTION_DAYS,
) -> None:
    """Install the one-sink, default-deny logging configuration.

    Idempotent: a second call replaces the handler this module installed rather
    than stacking a second sink. Runs as an import-time side effect with
    defaults; tests call it explicitly to redirect the sink to a tmp path.

    Args:
        log_dir: Sink directory. Defaults to OBSERVABILITY_LOG_DIR or
            DEFAULT_LOG_DIR.
        fmt: Output format, "json" or "console". Defaults to
            OBSERVABILITY_FORMAT or "json".
        retention_days: Rotated-backup count and sweep horizon.
    """
    global _INSTALLED_HANDLER

    resolved_dir = log_dir or Path(os.environ.get(ENV_LOG_DIR, str(DEFAULT_LOG_DIR)))
    resolved_fmt = (fmt or os.environ.get(ENV_FORMAT, "json")).lower()
    resolved_dir.mkdir(parents=True, exist_ok=True)

    shared = _build_shared_processors()

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # Disabled so tests can reconfigure the chain (the self-check test) and
        # have the change take effect on the next event.
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _build_renderer(resolved_fmt),
        ],
    )

    handler = TimedRotatingFileHandler(
        filename=resolved_dir / LOG_FILENAME,
        when="midnight",
        utc=True,
        backupCount=retention_days,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    if _INSTALLED_HANDLER is not None and _INSTALLED_HANDLER in root.handlers:
        root.removeHandler(_INSTALLED_HANDLER)
        _INSTALLED_HANDLER.close()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    _INSTALLED_HANDLER = handler

    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def sweep_expired_logs(
    log_dir: Path | None = None,
    retention_days: int = RETENTION_DAYS,
    now: datetime | None = None,
) -> list[Path]:
    """Delete rotated log files whose mtime is past the retention horizon.

    Only rotated files (``observability.log.*``) are considered; the active log
    is never swept. Belt-and-suspenders beyond the handler's backupCount: it
    catches over-age files left when the process was not alive at a rotation
    boundary.

    Args:
        log_dir: Sink directory to sweep.
        retention_days: Files older than this many days are deleted.
        now: Reference time (UTC). Defaults to the current time; injectable for
            tests.

    Returns:
        The list of deleted file paths.
    """
    resolved_dir = log_dir or Path(os.environ.get(ENV_LOG_DIR, str(DEFAULT_LOG_DIR)))
    reference = now or datetime.now(UTC)
    cutoff = reference - timedelta(days=retention_days)

    deleted: list[Path] = []
    if not resolved_dir.exists():
        return deleted
    for rotated in resolved_dir.glob(f"{LOG_FILENAME}.*"):
        mtime = datetime.fromtimestamp(rotated.stat().st_mtime, UTC)
        if mtime < cutoff:
            rotated.unlink()
            deleted.append(rotated)
    return deleted


# Import-time side effect: install the controls before any other module logs.
configure_logging()
