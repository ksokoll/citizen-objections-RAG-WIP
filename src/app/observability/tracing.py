"""Span instrumentation and the off-by-default tracing scaffold (ADR-023).

Two lessons from the prior invoice-agent project are enforced here, not
documented. Timing must not depend on tracing: the @traced decorator always
emits a governed timing event (observability.stage_timing), whether or not a
span is opened. And spans must never be created without a destination: the
tracer provider is built only when OBSERVABILITY_TRACING=1, and when it is,
spans land in an in-memory exporter that the run owner clears explicitly at
run start (clear_finished_spans, called by the Coordinator), so span memory
is bounded per run and cannot accumulate across runs. The run owner defines
the run; the earlier root-span heuristic in this module guessed it from span
parentage and was removed (M5, Round 16.1).

The span structure is flat (ADR-023): pipeline.run is the root, one child
span per stage. Production tracing is a configuration change, not a rewrite:
enable the flag and swap the in-memory exporter for an OTLP exporter.

OpenTelemetry and the metrics module are imported lazily, never at module load
(H1). Every context service imports this module for the @traced decorator, so
an eager top-level import would pull the whole telemetry stack into the context
import graph. The OTel imports live in the flag-guarded lazy path (the provider
is built only when tracing is enabled); the metrics write goes through a lazy
metrics import deferred to the first traced call. After this, importing a
context service does not import opentelemetry or prometheus_client.

The decorator captures no argument values (default-deny by origin, ADR-026,
third application): call arguments are payload of unknown content. The
capture_fields parameter is the explicit opt-in for named safe fields and is
deliberately unused in Round B; any captured field is still subject to the
sink's key allowlist.
"""

from __future__ import annotations

import functools
import inspect
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

import structlog

from app.observability.events import STAGE_TIMING

if TYPE_CHECKING:
    # Type-only imports: OpenTelemetry is never imported at module load (H1).
    # Every context service imports this module for the @traced decorator, so an
    # eager top-level OTel import would pull the whole telemetry stack into the
    # context import graph. The runtime imports live in the flag-guarded lazy
    # path (_ensure_tracer, _stage_span), which runs only when tracing is
    # enabled (ADR-023, no spans without a destination). Under
    # from __future__ import annotations these names are strings at runtime, so
    # the module-level type annotations below cost no import.
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from opentelemetry.trace import Span, Tracer

_log = structlog.get_logger()

P = ParamSpec("P")
R = TypeVar("R")

#: Name of the environment variable the composition root (the CLI) reads to
#: decide tracing. It is read only at the root and wired via set_tracing_enabled
#: (finding 8); the decorator consults the wired _TRACING_ENABLED flag, never
#: the environment. Off by default: in normal single-process operation timing
#: comes from the structured logs (ADR-023).
ENV_TRACING: str = "OBSERVABILITY_TRACING"

#: The wired tracing flag, set once at the composition root via
#: set_tracing_enabled. Off by default, so with no root opting in no provider,
#: processor, or exporter is ever constructed (ADR-023, no spans without a
#: destination). The CLI passes the OBSERVABILITY_TRACING reading; tracing
#: tests set it via the wired setter.
_TRACING_ENABLED: bool = False

#: Lazily built tracing backend. Both stay None until the first traced call
#: with tracing enabled, so with the flag unset no provider, processor, or
#: exporter object is ever constructed (ADR-023, no spans without a
#: destination).
_TRACER_PROVIDER: TracerProvider | None = None
_EXPORTER: InMemorySpanExporter | None = None


def set_tracing_enabled(enabled: bool) -> None:
    """Set the wired tracing flag (composition-root wiring, finding 8).

    Tracing is resolved once at the root and set here, not read live from the
    environment inside the decorator. The CLI reads OBSERVABILITY_TRACING and
    passes it; tracing tests set it directly. Toggling to False does not tear
    down an already-built provider; reset_tracing does that.

    Args:
        enabled: True to open OTel spans in addition to the always-on timing
            event, False for timing only (the default).
    """
    global _TRACING_ENABLED
    _TRACING_ENABLED = enabled


def tracing_enabled() -> bool:
    """Return whether span creation is enabled (the wired flag).

    Span creation follows the wired _TRACING_ENABLED flag, set once at the
    composition root via set_tracing_enabled, not read from the environment in
    the decorator (finding 8). A test toggles it via the setter.
    """
    return _TRACING_ENABLED


def tracer_provider_is_built() -> bool:
    """Return whether a tracer provider has been constructed (test hook)."""
    return _TRACER_PROVIDER is not None


def get_finished_spans() -> tuple[Any, ...]:
    """Return the spans finished since the current run started (test hook)."""
    if _EXPORTER is None:
        return ()
    return _EXPORTER.get_finished_spans()


def clear_finished_spans() -> None:
    """Discard the finished spans of the previous run.

    Called by the run owner (the Coordinator) at run() start: the owner of
    the run defines where a run begins, rather than this module guessing it
    from span parentage (M5, Round 16.1). Bounds span memory to one run. A
    no-op when tracing is disabled or no exporter exists yet.
    """
    if _EXPORTER is not None:
        _EXPORTER.clear()


def reset_tracing() -> None:
    """Tear down the tracing backend and clear the wired flag (test hook).

    Shuts down the provider so its span processor stops, discards both module
    references, and resets the wired tracing flag, returning the module to its
    import-time state in which no provider exists and tracing is off.
    """
    global _TRACER_PROVIDER, _EXPORTER, _TRACING_ENABLED
    if _TRACER_PROVIDER is not None:
        _TRACER_PROVIDER.shutdown()
    _TRACER_PROVIDER = None
    _EXPORTER = None
    _TRACING_ENABLED = False


def _ensure_tracer() -> Tracer:
    """Build the provider and bounded in-memory exporter once, return a tracer.

    OpenTelemetry is imported here, in the flag-guarded lazy path, not at module
    load (H1): this runs only after tracing is enabled and a span is about to be
    opened, so importing a context service for the @traced decorator never pulls
    the OTel stack. The provider is module-held, not installed as the OTel
    global provider: span parenting works through the context regardless, and
    the global stays untouched for a production setup that wires its own.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

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

    Exporter clearing is not done here: the run owner (the Coordinator)
    clears explicitly at run start via clear_finished_spans, because the run
    owner defines the run; a parentage heuristic in the instrumentation
    layer would guess it (M5, Round 16.1).
    """
    if not tracing_enabled():
        yield None
        return
    tracer = _ensure_tracer()
    with tracer.start_as_current_span(stage) as span:
        yield span


def _observe_stage_duration(stage: str, seconds: float) -> None:
    """Feed one stage duration to the metric via a lazy metrics import (H1).

    The metrics module constructs the Prometheus registry at its import, so an
    eager top-level import here would pull prometheus_client into the context
    import graph through the @traced decorator. The import is deferred to the
    first traced call at runtime instead; sys.modules caches it, so the cost is
    paid once. The metrics write is itself contained and cannot abort the
    business path (ADR-023).
    """
    from app.observability.metrics import observe_stage_duration

    observe_stage_duration(stage, seconds)


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
                    _observe_stage_duration(stage, duration_ms / 1000)
                    if span is not None:
                        # Imported lazily on the error path only: a span exists
                        # solely when tracing is enabled, so this never pulls
                        # OTel into the no-tracing import graph (H1).
                        from opentelemetry.trace import StatusCode

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
                _observe_stage_duration(stage, duration_ms / 1000)
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
