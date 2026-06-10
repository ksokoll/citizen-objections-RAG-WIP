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

Round A defines the single governed event the pipeline emits, the interim
audit-append failure. Rounds B and C extend this registry (timing, tracing,
metrics, custody-write events) deliberately, one constant at a time.
"""

from __future__ import annotations

from typing import Final

#: Interim governed event for a failed audit publish (ADR-027). Emitted at
#: ERROR by Pipeline._emit in place of the former stderr print. Round C turns
#: the same call site fail-closed; the log line stays.
AUDIT_APPEND_FAILED: Final[str] = "audit.append_failed"

REGISTERED_EVENTS: Final[frozenset[str]] = frozenset(
    {
        AUDIT_APPEND_FAILED,
    }
)


class UnregisteredLogEventError(Exception):
    """Raised when a structlog event name is not in REGISTERED_EVENTS.

    Signals a message-policy violation: an event was logged with a name that
    is not a registered static constant. The fix is to register the constant
    in this module, not to suppress the error.
    """
