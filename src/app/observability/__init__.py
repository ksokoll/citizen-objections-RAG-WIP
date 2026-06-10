"""Observability infrastructure for the citizen-objections pipeline.

A sixth top-level module beside the five bounded contexts. It is explicitly
not a bounded context and not a cross-context contract holder: it is
cross-cutting infrastructure (ADR-023). It depends on stdlib and structlog
only; it must not import any bounded context or core (no domain types in the
instrumentation layer).

Importing this package configures logging as an import-time side effect (via
logging_config), so the entry point importing observability before any other
module is what guarantees no log escapes the default-deny chain (ADR-026).
"""

from app.observability import logging_config  # noqa: F401  (import-time config)
from app.observability.correlation import (
    correlation_scope,
    get_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)
from app.observability.events import (
    AUDIT_APPEND_FAILED,
    REGISTERED_EVENTS,
    UnregisteredLogEventError,
)
from app.observability.logging_config import (
    configure_logging,
    sweep_expired_logs,
)

__all__ = [
    "AUDIT_APPEND_FAILED",
    "REGISTERED_EVENTS",
    "UnregisteredLogEventError",
    "configure_logging",
    "correlation_scope",
    "get_correlation_id",
    "reset_correlation_id",
    "set_correlation_id",
    "sweep_expired_logs",
]
