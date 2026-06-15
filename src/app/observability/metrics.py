"""In-process Prometheus metrics for the pipeline (ADR-023, ADR-027).

Logs tell the story of one run; metrics count the fleet across runs. Exactly
seven metrics exist, one purpose each, cardinality far under 100: the six
defined in docs/OBSERVABILITY_IMPLEMENTATION.md plus the Round 16.1
triage-contradiction counter (S3; a deliberate widening of the committed
six, recorded in the round-15 deviations log). The registry is in-process
with no scrape endpoint in this phase: the metrics are instrumentation
readiness, not live signals. Production adds a scrape endpoint and alert
rules against the thresholds documented below; that is a wiring step, not a
redesign (ADR-023).

Norm resolution is one counter family with a result label (resolved or
unresolved): the quality signal is the ratio unresolved / total, and both
counts exist as the two labeled series. The plan's wording "two counters"
materializes as the two series of one family, keeping the six-metric count
literal (recorded in the Round B deviations log).

Containment: every increment and observation goes through a helper that can
never raise. A metric increment may no more abort the business path than a
log call (ADR-026, unbreakable runtime). Unlike the logging chain's
never_raise, containment here is unconditional, with no strict-mode
re-raise: a metrics failure writes nothing to any sink, so there is no PII
control to fail loud for, and the metrics tests assert on counter values, so
a wiring bug still surfaces in CI as a wrong value rather than a swallowed
exception.

Alert thresholds, defined now as documentation and wired in production
(initial values, to be calibrated against real traffic):

- Unresolved-norm ratio: increase(norm_resolution_total{result="unresolved"})
  divided by the total increase, sustained above 0.20 over one hour, opens a
  ticket. The absolute counts alone say little; the ratio is the signal.
- Argument-verification failure rate: increase of
  argument_verification_failures_total divided by the increase of observed
  arguments (the arguments_per_objection histogram sum), sustained above
  0.10 over one hour, opens a ticket. Direct Triage-LLM quality signal
  (ADR-006 Layer 1).
- audit_write_failures_total: any nonzero value pages immediately. A
  degrading audit store must be visible regardless of the failure policy and
  independently of the log sink (ADR-027, interim double-failure risk).
- triage_contradictions_total: a sustained increase warrants reviewing the
  affected documents; a single increment is a quality signal, not an alarm.
  The observable signature of a prompt-injected argument suppression (S3).
"""

from __future__ import annotations

import functools
from collections.abc import Callable

from prometheus_client import CollectorRegistry, Counter, Histogram

#: In-process registry holding exactly the seven pipeline metrics.
#: Deliberately not the prometheus_client global REGISTRY, so importing this
#: module twice under test never trips duplicate registration and nothing
#: third-party can leak into our metric namespace.
REGISTRY = CollectorRegistry()

OBJECTIONS_PROCESSED = Counter(
    "objections_processed_total",
    "Objections processed, by terminal pipeline status "
    "(briefing_erstellt, kein_treffer, pipeline_fehler).",
    labelnames=("status",),
    registry=REGISTRY,
)

STAGE_DURATION = Histogram(
    "stage_duration_seconds",
    "Duration of one instrumented stage, fed from the @traced measurement.",
    labelnames=("stage",),
    registry=REGISTRY,
)

NORM_RESOLUTION = Counter(
    "norm_resolution_total",
    "Norm citations resolved against the statute corpus, by result "
    "(resolved or unresolved). The signal is the unresolved ratio.",
    labelnames=("result",),
    registry=REGISTRY,
)

ARGUMENTS_PER_OBJECTION = Histogram(
    "arguments_per_objection",
    "Extracted arguments per objection document.",
    buckets=(0, 1, 2, 3, 5, 8, 13, 21),
    registry=REGISTRY,
)

ARGUMENT_VERIFICATION_FAILURES = Counter(
    "argument_verification_failures_total",
    "Arguments whose original_zitat substring check failed "
    "(ADR-006 Layer 1); rate over observed arguments is the signal.",
    registry=REGISTRY,
)

AUDIT_WRITE_FAILURES = Counter(
    "audit_write_failures_total",
    "Swallowed-or-handled audit publish failures; sink-independent "
    "visibility for a degrading audit store (ADR-027).",
    registry=REGISTRY,
)

TRIAGE_CONTRADICTIONS = Counter(
    "triage_contradictions_total",
    "Documents whose deterministic norm extraction found citations while "
    "the LLM returned no arguments; the prompt-injection signature (S3).",
    registry=REGISTRY,
)


def _contained[**P](write: Callable[P, None]) -> Callable[P, None]:
    """Wrap a metrics write so it can never abort the business path.

    See the module docstring for why this containment is unconditional,
    unlike the logging chain's never_raise.
    """

    @functools.wraps(write)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            write(*args, **kwargs)
        except Exception:
            pass

    return wrapper


@_contained
def inc_objection_processed(status: str) -> None:
    """Count one processed objection under its terminal status."""
    OBJECTIONS_PROCESSED.labels(status=status).inc()


@_contained
def observe_stage_duration(stage: str, seconds: float) -> None:
    """Record one stage duration measured by the @traced decorator."""
    STAGE_DURATION.labels(stage=stage).observe(seconds)


@_contained
def inc_norm_resolutions(resolved: int, unresolved: int) -> None:
    """Count a run's resolved and unresolved norm citations."""
    NORM_RESOLUTION.labels(result="resolved").inc(resolved)
    NORM_RESOLUTION.labels(result="unresolved").inc(unresolved)


@_contained
def observe_arguments_per_objection(count: int) -> None:
    """Record how many arguments one objection document yielded."""
    ARGUMENTS_PER_OBJECTION.observe(count)


@_contained
def inc_argument_verification_failures(count: int) -> None:
    """Count arguments whose original_zitat substring check failed."""
    ARGUMENT_VERIFICATION_FAILURES.inc(count)


@_contained
def inc_audit_write_failure() -> None:
    """Count one handled audit publish failure (ADR-027 visibility)."""
    AUDIT_WRITE_FAILURES.inc()


@_contained
def inc_triage_contradiction() -> None:
    """Count one norms-present-but-no-arguments contradiction (S3)."""
    TRIAGE_CONTRADICTIONS.inc()
