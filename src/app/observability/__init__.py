"""Observability infrastructure for the citizen-objections pipeline.

A sixth top-level module beside the five bounded contexts. It is explicitly
not a bounded context and not a cross-context contract holder: it is
cross-cutting infrastructure (ADR-023). It depends on stdlib, structlog, and
the pinned telemetry libraries only; it must not import any bounded context
or core (no domain types in the instrumentation layer).

Importing this package has no side effects. Configuration is an explicit
composition-root act: the CLI entrypoint calls configure_logging(log_dir=...)
before any pipeline work, and the test suite configures via a session fixture
in conftest (ADR-026, phase separation; the Round 15.2 import-time stopgap is
retired).
"""

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
    ObservabilityBootstrapError,
    ProcessorChainError,
    configure_logging,
    sweep_expired_logs,
)

__all__ = [
    "AUDIT_APPEND_FAILED",
    "REGISTERED_EVENTS",
    "ObservabilityBootstrapError",
    "ProcessorChainError",
    "UnregisteredLogEventError",
    "configure_logging",
    "correlation_scope",
    "get_correlation_id",
    "reset_correlation_id",
    "set_correlation_id",
    "sweep_expired_logs",
]
