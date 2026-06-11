"""Registered log event vocabulary for the observability layer.

Log messages are static, registered constants, never interpolated free text.
Variable data goes into named, allowlisted fields on the event, not into the
message string (ADR-026, message policy). This module is the single registry.
The logging chain rejects any structlog event whose name is not in
REGISTERED_EVENTS, so a typo or an ad hoc message fails loudly at the sink
instead of silently widening the vocabulary.

Foreign stdlib records (Presidio, OpenTelemetry, urllib3) are not subject to
this vocabulary: their message text is arbitrary by nature and is governed only
by the key allowlist and the WARNING clamp.

Round A defines the governed events emitted in this round: the interim
audit-append failure and the two DocumentIngestion warnings that previously
escaped to stderr ungoverned. Rounds B and C extend this registry (timing,
tracing, metrics, custody-write events) deliberately, one constant at a time.
"""

from __future__ import annotations

from typing import Final

#: Interim governed event for a failed audit publish (ADR-027). Emitted at
#: ERROR by Pipeline._emit in place of the former stderr print. Round C turns
#: the same call site fail-closed; the log line stays.
AUDIT_APPEND_FAILED: Final[str] = "audit.append_failed"

#: A persisted raw store is world-accessible on POSIX (DocumentIngestion).
#: A misconfiguration, not a masking outcome: logged, processing continues.
INGESTION_RAW_STORE_WORLD_READABLE: Final[str] = "ingestion.raw_store_world_readable"

#: The log sink directory is world-accessible on POSIX (observability). The
#: logs are a third store of pseudonymous data (ADR-026); the check mirrors the
#: raw-store world-readable check (ADR-025). Logged as a mode count only.
LOG_SINK_WORLD_READABLE: Final[str] = "observability.log_sink_world_readable"

#: Deterministic anchor name tokens survived masking in their own zone
#: (DocumentIngestion). An internal contradiction; logged as a count only,
#: never the surviving tokens, so the anomaly signal carries no PII.
INGESTION_PII_COVERAGE_ANOMALY: Final[str] = "ingestion.pii_coverage_anomaly"

#: Size in bytes of the active log sink, emitted once after configuration
#: (observability). Surfaces the Windows rotation failure mode: if a second
#: handle on the active file blocks the midnight rename, the file grows without
#: bound and the size reported at the next startup makes that visible (ADR-026).
LOG_SINK_SIZE_BYTES: Final[str] = "observability.log_sink_size_bytes"

#: A governed processor raised at runtime and was contained by the never-raise
#: wrapper (observability). The substitute event carries the failing processor's
#: name so the bug is attributable, while the business call returns normally
#: (ADR-026, unbreakable runtime). The original event dict is discarded: a
#: processor that failed mid-chain may hold half-processed, untrusted data.
PROCESSOR_FAILED: Final[str] = "observability.processor_failed"

#: An own-code structlog event whose name was not a registered constant, seen
#: in production mode (observability). The original name is discarded entirely
#: (it is potential payload) and replaced by this constant plus the caller
#: location, so the typo is locatable without writing the unvetted name to disk.
#: In strict mode (the test suite) the same condition raises instead, so CI
#: catches every typo (ADR-026, enforcement at origin).
UNREGISTERED_LOG_EVENT: Final[str] = "observability.unregistered_log_event"

REGISTERED_EVENTS: Final[frozenset[str]] = frozenset(
    {
        AUDIT_APPEND_FAILED,
        INGESTION_RAW_STORE_WORLD_READABLE,
        LOG_SINK_WORLD_READABLE,
        INGESTION_PII_COVERAGE_ANOMALY,
        LOG_SINK_SIZE_BYTES,
        PROCESSOR_FAILED,
        UNREGISTERED_LOG_EVENT,
    }
)


class UnregisteredLogEventError(Exception):
    """Raised when a structlog event name is not in REGISTERED_EVENTS.

    Signals a message-policy violation: an event was logged with a name that
    is not a registered static constant. The fix is to register the constant
    in this module, not to suppress the error.
    """
