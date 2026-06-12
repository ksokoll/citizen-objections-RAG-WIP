"""Span instrumentation and the off-by-default tracing scaffold (ADR-023).

Two lessons from the prior invoice-agent project are enforced here, not
documented. Timing must not depend on tracing: the @traced decorator always
emits a governed timing event (observability.stage_timing), whether or not a
span is opened. And spans must never be created without a destination: the
tracer provider is built only when OBSERVABILITY_TRACING=1, and when it is,
spans land in an in-memory exporter that is cleared whenever a new root span
starts, so span memory is bounded per run and cannot accumulate across runs.

The span structure is flat (ADR-023): pipeline.run is the root, one child
span per stage. Production tracing is a configuration change, not a rewrite:
enable the flag and swap the in-memory exporter for an OTLP exporter.

The decorator captures no argument values (default-deny by origin, ADR-026,
third application): call arguments are payload of unknown content. The
capture_fields parameter is the explicit opt-in for named safe fields and is
deliberately unused in Round B; any captured field is still subject to the
sink's key allowlist.
"""

from __future__ import annotations

import functools
import inspect
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, ParamSpec, TypeVar

import structlog
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import Span, StatusCode

from app.observability.events import STAGE_TIMING
from app.observability.metrics import observe_stage_duration

_log = structlog.get_logger()

P = ParamSpec("P")
R = TypeVar("R")

#: When set to "1", the @traced decorator additionally opens OTel spans.
#: Read live per call (like OBSERVABILITY_STRICT), so a test can toggle
#: tracing without reconfiguring anything. Off by default: in normal
#: single-process operation timing comes from the structured logs (ADR-023).
ENV_TRACING: str = "OBSERVABILITY_TRACING"

#: Lazily built tracing backend. Both stay None until the first traced call
#: with tracing enabled, so with the flag unset no provider, processor, or
#: exporter object is ever constructed (ADR-023, no spans without a
#: destination).
_TRACER_PROVIDER: TracerProvider | None = None
_EXPORTER: InMemorySpanExporter | None = None


def tracing_enabled() -> bool:
    """Return whether span creation is enabled (read live, per call)."""
    return os.environ.get(ENV_TRACING) == "1"


def tracer_provider_is_built() -> bool:
    """Return whether a tracer provider has been constructed (test hook)."""
    return _TRACER_PROVIDER is not None


def get_finished_spans() -> tuple[Any, ...]:
    """Return the spans finished since the current run started (test hook)."""
    if _EXPORTER is None:
        return ()
    return _EXPORTER.get_finished_spans()


def reset_tracing() -> None:
    """Tear down the tracing backend (test hook).

    Shuts down the provider so its span processor stops, then discards both
    module references, returning the module to its import-time state in which
    no provider exists.
    """
    global _TRACER_PROVIDER, _EXPORTER
    if _TRACER_PROVIDER is not None:
        _TRACER_PROVIDER.shutdown()
    _TRACER_PROVIDER = None
    _EXPORTER = None


def _ensure_tracer() -> trace.Tracer:
    """Build the provider and bounded in-memory exporter once, return a tracer.

    The provider is module-held, not installed as the OTel global provider:
    span parenting works through the context regardless, and the global stays
    untouched for a production setup that wires its own.
    """
    global _TRACER_PROVIDER, _EXPORTER
    if _TRACER_PROVIDER is None:
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        _EXPORTER = exporter
        _TRACER_PROVIDER = provider
    return _TRACER_PROVIDER.get_tracer("app.observability")


@contextmanager
def _stage_span(stage: str) -> Iterator[Span | None]:
    """Open a span for the stage when tracing is enabled, else yield None.

    A span with no active parent starts a new run, so the in-memory exporter
    is cleared at that moment: span memory is explicitly bounded to one run
    and cannot accumulate across requests (ADR-023).
    """
    if not tracing_enabled():
        yield None
        return
    tracer = _ensure_tracer()
    if not trace.get_current_span().get_span_context().is_valid:
        assert _EXPORTER is not None  # _ensure_tracer just built it
        _EXPORTER.clear()
    with tracer.start_as_current_span(stage) as span:
        yield span


def _capture_safe_fields(
    func: Callable[..., Any],
    capture_fields: tuple[str, ...],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the explicitly opted-in argument fields, never anything else.

    Best-effort and contained: a binding failure yields no fields rather than
    an instrumentation error in the business path. Captured fields are still
    subject to the sink's key allowlist.
    """
    if not capture_fields:
        return {}
    try:
        bound = inspect.signature(func).bind(*args, **kwargs)
        bound.apply_defaults()
        return {
            name: bound.arguments[name]
            for name in capture_fields
            if name in bound.arguments
        }
    except Exception:
        return {}


def traced(
    stage: str,
    capture_fields: tuple[str, ...] = (),
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Instrument a stable method boundary with timing and an optional span.

    The decorated callable always emits the registered stage_timing event
    with the stage name, duration_ms, and status ok or error; on error the
    exception is attached via exc_info, reduced by the logging chain to type
    plus location, and then re-raised unchanged. A span is opened only when
    OBSERVABILITY_TRACING=1; its status is set to ERROR on the error path.
    The measured duration also feeds the stage_duration_seconds metric, so
    the metric needs no module-global start-time state of its own; the
    metrics write is contained and cannot abort the business path.

    No argument values are captured by default. capture_fields is the
    explicit opt-in for named safe fields (unused in Round B).

    Args:
        stage: Stage name stamped on the timing event and used as span name.
        capture_fields: Names of arguments to attach to the timing event.

    Returns:
        A decorator preserving the wrapped callable's signature.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            extra = _capture_safe_fields(func, capture_fields, args, kwargs)
            start = time.perf_counter()
            with _stage_span(stage) as span:
                try:
                    result = func(*args, **kwargs)
                except Exception:
                    duration_ms = round((time.perf_counter() - start) * 1000, 3)
                    observe_stage_duration(stage, duration_ms / 1000)
                    if span is not None:
                        span.set_status(StatusCode.ERROR)
                    _log.error(
                        STAGE_TIMING,
                        stage=stage,
                        duration_ms=duration_ms,
                        status="error",
                        exc_info=True,
                        **extra,
                    )
                    raise
                duration_ms = round((time.perf_counter() - start) * 1000, 3)
                observe_stage_duration(stage, duration_ms / 1000)
                _log.info(
                    STAGE_TIMING,
                    stage=stage,
                    duration_ms=duration_ms,
                    status="ok",
                    **extra,
                )
                return result

        return wrapper

    return decorator
