"""Log event vocabulary owned by the AuditLog context.

Each context declares the event constants it emits, rather than a central
observability registry naming foreign owners (H2). The composition root unions
these per-context declarations into the registry the logging chain enforces
against, so observability keeps the mechanism while domain vocabulary lives
with the context that emits it (ADR-026).

The Coordinator emits this event on the audit publish path (pipeline._emit),
but the vocabulary belongs to the AuditLog context whose store failed: a
failed audit append is an audit-store concern, not a coordinator concern.
"""

from __future__ import annotations

from typing import Final

#: Interim governed event for a failed audit publish (ADR-027). Emitted at
#: ERROR by Pipeline._emit in place of the former stderr print. Round C turns
#: the same call site fail-closed; the log line stays.
AUDIT_APPEND_FAILED: Final[str] = "audit.append_failed"

#: Event constants this context emits, unioned into the registry at the
#: composition root.
AUDIT_EVENTS: Final[frozenset[str]] = frozenset({AUDIT_APPEND_FAILED})

#: Allowlisted log field names this context emits, unioned into ALLOWED_KEYS at
#: the composition root (ADR-026, default-deny). audit_event_type is the
#: AuditEventType value the failed publish carried; operational metadata, never
#: payload.
AUDIT_KEYS: Final[frozenset[str]] = frozenset({"audit_event_type"})
