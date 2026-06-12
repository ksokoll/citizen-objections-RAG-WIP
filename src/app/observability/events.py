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

#: A stored raw document (unmasked PII) was read back out of the raw store
#: (DocumentIngestion, Round 16.1, H4/S4). Emitted on every successful
#: load_raw_document call with the document_id only, never content: the read
#: path on raw PII leaves an operational trace. The chain-level read audit
#: event (custody, not telemetry) is Round C work (ADR-027).
RAW_DOCUMENT_ACCESSED: Final[str] = "ingestion.raw_document_accessed"

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

#: The active toolset at startup, emitted once by the CLI composition root
#: after a successful bootstrap (Round B). Records what produced the run's
#: output: git_sha, model_id, package versions, corpus_id, allowlist size,
#: tracing flag, and log format. Operational provenance, never payload.
STARTUP_CONFIG: Final[str] = "app.startup_config"

#: An unexpected exception reached the CLI dispatch boundary (Round 16.1,
#: S1/M4). Emitted at ERROR by the entrypoint catch-all before the process
#: exits 1; the exception is reduced to type plus location by the chain and
#: its message (foreign-authored text) is never written. The stderr line the
#: user sees carries the type only, no detail and no traceback.
CLI_UNHANDLED_ERROR: Final[str] = "app.unhandled_error"

#: Timing of one instrumented stage, emitted by the @traced decorator on
#: every invocation regardless of the tracing flag (observability, Round B).
#: Carries the stage name, duration_ms, and status ok or error; on error the
#: exception is reduced to type plus location by the chain. Timing must not
#: depend on tracing (ADR-023).
STAGE_TIMING: Final[str] = "observability.stage_timing"

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
        CLI_UNHANDLED_ERROR,
        INGESTION_RAW_STORE_WORLD_READABLE,
        LOG_SINK_WORLD_READABLE,
        INGESTION_PII_COVERAGE_ANOMALY,
        RAW_DOCUMENT_ACCESSED,
        LOG_SINK_SIZE_BYTES,
        PROCESSOR_FAILED,
        STAGE_TIMING,
        STARTUP_CONFIG,
        UNREGISTERED_LOG_EVENT,
    }
)


class UnregisteredLogEventError(Exception):
    """Raised when a structlog event name is not in REGISTERED_EVENTS.

    Signals a message-policy violation: an event was logged with a name that
    is not a registered static constant. The fix is to register the constant
    in this module, not to suppress the error.
    """
