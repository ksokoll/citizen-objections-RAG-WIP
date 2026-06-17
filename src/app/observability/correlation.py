"""Correlation id propagation for the observability layer.

The correlation id is the document_id of the objection under processing. It is
set once at run() entry so that every log event emitted during a single run,
by our code or by a third-party library, carries the same id. A ContextVar
makes the id ambient to the structlog processor chain without threading it
through every call site.

The id is the pseudonymous uuid4 document_id (ADR-026), never derived from PII.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

from structlog.typing import EventDict, WrappedLogger

_CORRELATION_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)


def set_correlation_id(document_id: str) -> contextvars.Token[str | None]:
    """Set the active correlation id to the given document_id.

    Args:
        document_id: The pseudonymous objection id to anchor logs on.

    Returns:
        A reset token that restores the previous value via
        reset_correlation_id.
    """
    return _CORRELATION_ID.set(document_id)


def get_correlation_id() -> str | None:
    """Return the active correlation id, or None if none is set."""
    return _CORRELATION_ID.get()


def reset_correlation_id(token: contextvars.Token[str | None]) -> None:
    """Restore the correlation id to the value captured in the token.

    Args:
        token: The token returned by set_correlation_id.
    """
    _CORRELATION_ID.reset(token)


@contextmanager
def correlation_scope(document_id: str) -> Iterator[None]:
    """Bind the correlation id for the duration of the with-block.

    Args:
        document_id: The pseudonymous objection id to anchor logs on.

    Yields:
        None. The correlation id is active inside the block and restored
        on exit, even if the block raises.
    """
    token = _CORRELATION_ID.set(document_id)
    try:
        yield
    finally:
        _CORRELATION_ID.reset(token)


def add_correlation_id(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """structlog processor: stamp the active correlation id onto the event.

    The ContextVar is the single source of truth (default-deny by origin,
    ADR-026). When a correlation id is set the key is assigned unconditionally,
    overwriting any pre-existing ``correlation_id`` that an own-code kwarg or a
    foreign record placed in the event dict. When none is set any pre-existing
    value is removed and the key is omitted rather than emitted as null, so an
    untrusted id can never survive and events outside a run stay honestly
    distinguishable from events inside one.

    Args:
        logger: The wrapped logger (unused).
        method_name: The log method name (unused).
        event_dict: The structlog event dict to enrich.

    Returns:
        The event dict, with ``correlation_id`` set to the ContextVar truth
        when one is active and absent otherwise.
    """
    correlation_id = _CORRELATION_ID.get()
    if correlation_id is not None:
        event_dict["correlation_id"] = correlation_id
    else:
        event_dict.pop("correlation_id", None)
    return event_dict
