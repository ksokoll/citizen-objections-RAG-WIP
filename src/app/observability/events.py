"""Log event vocabulary mechanism for the observability layer.

Log messages are static, registered constants, never interpolated free text.
Variable data goes into named, allowlisted fields on the event, not into the
message string (ADR-026, message policy). The logging chain rejects any
structlog event whose name is not registered, so a typo or an ad hoc message
fails loudly at the sink instead of silently widening the vocabulary.

This module holds the enforcement mechanism plus a registration API, not the
domain vocabulary. Each bounded context declares the event constants it emits
in its own ``<context>/events.py``; the composition root unions those
per-context declarations into the registry via register_events. observability
no longer knows any domain event name (H2): the only constants defined here are
the layer's own self-instrumentation events, which observability itself emits.
Those own events seed the registry at import so the instrumentation works
before any root assembly runs.

Foreign stdlib records (Presidio, OpenTelemetry, urllib3) are not subject to
this vocabulary: their message text is arbitrary by nature and is governed only
by the key allowlist and the WARNING clamp.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

#: A governed processor raised at runtime and was contained by the never-raise
#: wrapper (observability). The substitute event carries the failing processor's
#: name so the bug is attributable, while the business call returns normally
#: (ADR-026, unbreakable runtime). The original event dict is discarded: a
#: processor that failed mid-chain may hold half-processed, untrusted data.
PROCESSOR_FAILED: Final[str] = "observability.processor_failed"

#: Timing of one instrumented stage, emitted by the @traced decorator on
#: every invocation regardless of the tracing flag (observability). Carries the
#: stage name, duration_ms, and status ok or error; on error the exception is
#: reduced to type plus location by the chain. Timing must not depend on
#: tracing (ADR-023).
STAGE_TIMING: Final[str] = "observability.stage_timing"

#: An own-code structlog event whose name was not a registered constant, seen
#: in production mode (observability). The original name is discarded entirely
#: (it is potential payload) and replaced by this constant plus the caller
#: location, so the typo is locatable without writing the unvetted name to disk.
#: In strict mode (the test suite) the same condition raises instead, so CI
#: catches every typo (ADR-026, enforcement at origin).
UNREGISTERED_LOG_EVENT: Final[str] = "observability.unregistered_log_event"

#: The observability layer's own self-instrumentation events. These are the
#: mechanism's own vocabulary, not domain knowledge, so they live here and seed
#: the registry at import time: a degraded-event substitution or a sink
#: self-check must work before any composition root unions the context
#: registries in.
OBSERVABILITY_EVENTS: Final[frozenset[str]] = frozenset(
    {
        PROCESSOR_FAILED,
        STAGE_TIMING,
        UNREGISTERED_LOG_EVENT,
    }
)

#: The live registry the chain enforces against, assembled at runtime. Seeded
#: with the layer's own events; the composition root adds each context's
#: declared events via register_events. Mutable on purpose: the registry is a
#: union built at the root, not a frozen central god-list (H2). The reset hook
#: returns it to the seed between tests.
_registered_events: set[str] = set(OBSERVABILITY_EVENTS)


def register_events(events: Iterable[str]) -> None:
    """Union a set of event constants into the live registry.

    Called by the composition root with each context's declared events
    (TRIAGE_EVENTS, INGESTION_EVENTS, ...) plus the root's own CLI events.
    Idempotent: registering the same events twice is a no-op union, so two
    roots (the CLI and the test conftest) calling it does not double-count.

    Args:
        events: Event-name constants to register.
    """
    _registered_events.update(events)


def registered_events() -> frozenset[str]:
    """Return the currently registered event vocabulary.

    The logging chain consults this rather than a module-level frozen set, so
    the registry is the root-assembled union of the layer's own events and the
    per-context declarations (H2).
    """
    return frozenset(_registered_events)


def reset_registered_events() -> None:
    """Reset the registry to the layer's own events (test hook).

    Returns the registry to its import-time seed (the observability self
    events), discarding any context events a root unioned in. Part of the
    symmetric between-tests reset so a test that registers an event cannot leak
    its vocabulary into a later test.
    """
    _registered_events.clear()
    _registered_events.update(OBSERVABILITY_EVENTS)


class UnregisteredLogEventError(Exception):
    """Raised when a structlog event name is not registered.

    Signals a message-policy violation: an event was logged with a name that
    is not a registered static constant. The fix is to declare the constant in
    the emitting context's events.py and union it at the composition root, not
    to suppress the error.
    """
