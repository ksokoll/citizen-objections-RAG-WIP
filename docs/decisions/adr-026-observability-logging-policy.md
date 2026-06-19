# ADR-026: Observability Logging Policy: One Sink, Default-Deny

Status: Accepted. The SRE parts (time-based rotation and the retention sweep, the
0o600/0o700 file-permission posture, the log_sink_size_bytes metric, and the
guarded fail-loud bootstrap) were rolled back in Round 21b as out of demo scope;
see the banner. The governed-sink core stands. Detailed processor mechanics live
in `logging_config.py`; the roadmap context is in OBSERVABILITY_IMPLEMENTATION.md.
Date: 2026-06-09
Deciders: Kevin Sokoll

> Round 21b rollback (2026-06-17). A second ownership pass removed the logging
> layer's SRE parts as target-far for an ML/AI portfolio, the same scope
> discipline as the Round 21 chain rollback (ADR-030 superseded). Removed:
> time-based rotation and the retention sweep (a single plain FileHandler now
> appends to one log file); the owner-only file posture and the world-readable
> self-check (default umask, since masking and not file permissions keeps PII out
> of the logs); the log_sink_size_bytes metric; and ObservabilityBootstrapError
> with its heavy guard (a setup failure now propagates the underlying error).
> Kept and unchanged in behavior: the single governed sink, the default-deny key
> allowlist, the registered per-context event vocabulary, exception reduction to
> type plus location, value sanitization, the strict-mode vs production split, and
> never_raise. Deferral (built, then deliberately rolled back; production
> restores): operational retention, file ACLs, the sink-size metric, and a
> guarded fail-loud bootstrap. This is a stronger deferral than "not built": it
> shows command of the topic plus the discipline to remove what the demo does not
> need.

## Context

The pipeline carries pseudonymous personal data: the document_id keys a raw store
that holds PII until erasure (ADR-025). Structured logs are therefore a third
store of pseudonymous data alongside the audit chain and the raw store, and they
are the one store written on every code path, by our code and by third-party
libraries alike. An external review surfaced the forces this policy must answer: a
structlog-only allowlist is bypassed by stdlib logging from Presidio,
OpenTelemetry, and urllib3; event names and exception fields are uncontrolled
free-text channels; and an exception logged with str(e) or a rendered traceback
carries foreign-authored text of unknown content to disk.

## Decision

Default-deny, enforced at the single point all log output passes through: one
sink, one shared processor chain.

**One sink.** structlog and foreign stdlib records route through the same shared
processors before one plain FileHandler (`ProcessorFormatter` with
`foreign_pre_chain`), so no second logging API escapes the controls. Third-party
loggers are clamped to WARNING.

**Default-deny on two questions, two controls.** A key allowlist controls what may
enter a record (a new field is invisible until allowlisted on purpose, frozen by a
golden test). Origin rules control who may write a field, enforced by the
ordering: lift untrusted data first or not at all, stamp authoritative fields
after, filter last. Foreign extras are not lifted at all (no `ExtraAdder`), so a
foreign extra can neither overwrite an authoritative field nor inject an
allowlisted key; the authoritative fields (`correlation_id`, `level`, `timestamp`)
are assigned from their source of truth; the key allowlist and value normalization
run last on already-trusted data. The detailed processor mechanics are in
`logging_config.py`.

**Mode-dependent enforcement** (the strict/unbreakable split below). In strict
mode (CI) an own event carrying an unregistered key or an unregistered event name
raises (`UnregisteredLogKeyError`, `UnregisteredLogEventError`), so a mistake
fails at its origin; in production the key is dropped and the name substituted,
never raised, so a logging mistake cannot abort the request path. The raise is
gated to own code via the `_from_structlog` discriminant; a foreign record never
raises. A per-event key schema (each event declaring its own fields) is a
deliberate backlog item, a strict superset that adds nothing to rebuild, deferred
while one reviewer can keep the small own-event field set in view.

**Message and exception policy.** Event names are registered static constants,
never interpolated free text; variable data goes into named allowlisted fields.
Exceptions are logged as type plus location, never `str(e)` and never a rendered
traceback, because an exception message is foreign-authored text that can carry a
fragment of the input under processing.

**Value normalization.** Before the allowlist, string values are stripped of C0
control characters (so a foreign record cannot forge a second log line) and a
foreign event message is length-capped, bounding the foreign-message residual.

**Renderer.** JSON is the default and is mandatory in security-relevant
environments; the console renderer is a developer convenience. Format is a CLI
decision (`--log-format`), not an environment read, so a deployment cannot be
silently switched to console (the former `OBSERVABILITY_FORMAT` fallback was
removed, superseding ADR-023 decision 1); the active format is recorded in
startup_config.

## Phase separation: strict bootstrap, unbreakable runtime

A control belongs where its error originates. Configuration is an explicit
composition-root call (`configure_logging` at the CLI entrypoint, before any
context code logs; the sink path is a parameter, never an environment read at
import time, closing the path-injection finding). Bootstrap fails loud and does
not degrade to a NullHandler or stderr: running the pipeline without the governed
sink would be fail-open for the central PII control, so a bootstrap failure stops
the process. (Round 21b: a setup failure propagates the underlying error rather
than a translated `ObservabilityBootstrapError`; a missing allowlist still raises
`ProcessorChainError`, which the CLI turns into a clean nonzero-exit abort.)

Once configured, no logging call may abort a business operation. Every own
processor is wrapped by `never_raise`: a processor exception becomes a substitute
`processor_failed` event and the business call returns. never_raise is a
systems-design decision, not infra robustness: the instrumentation path must not
tear down the business path, the failure mode an ML engineer meets when a
monitoring error kills a pipeline. Vocabulary and key enforcement therefore live
in CI (strict mode), where a typo is a fixable defect, not in the request path,
where it would turn a logging mistake in a rarely exercised branch into a failed
objection.

## Alternatives Considered

1. structlog-only allowlist (no foreign routing). Rejected: leaves the stdlib
   bypass open, the main leak path given Presidio and OTel.
2. A scrubbing pass over the log file after the fact, as the primary control.
   Rejected: reactive, races the reader, a missed run leaks. Retained only as
   deferred defense-in-depth (ADR-027).
3. Size-based rotation for retention. Rejected: size says nothing about age and
   the obligation is time-bound. (The time-based mechanism that replaced it was
   itself rolled back in Round 21b; see the banner.)
4. Rendering tracebacks for debuggability. Rejected: a traceback is the richest
   foreign-text channel; type plus location keeps diagnosability without the
   content.

## Consequences

Both logging worlds are governed by one allowlist with sink-level tests, so PII
discipline, event vocabulary, and exception reduction are mechanisms, not
conventions: a later refactor or a test setup cannot silently remove a control
without a test changing.

Named residual channels, not hidden: a third-party WARNING/ERROR message string
passes through as the `event` value, bounded (WARNING clamp, control-character
strip, length cap) but not inspected, so it is the weakest residual until the
deferred sink scan (ADR-027); a value under an allowlisted key could carry more
than intended, mitigated by keeping the set operational and by review; `print()`
and C-level stderr bypass logging entirely, mitigated by a no-print grep check,
not a runtime control.

The allowlist is a deliberate maintenance point: a new operational field requires
an allowlist change and a golden-test update, the intended cost of default-deny.

## Declared assumption: single synchronous run

The in-memory span exporter (cleared by the Coordinator at run start) and the
in-process metric registry are deliberately global, correct only while one run
executes at a time. This is a declared simplification, not silent debt (Round 17):
building a run-scoped collector and an injected registry now would be mechanism
against a scenario that does not occur. The assumption breaks the moment runs
overlap (a FastAPI handler, a worker pool, a scrape endpoint), at which point the
named retrofit is a run-scoped span collector filtering by trace id and registry
injection at the composition root. The correlation id needs no retrofit: it is
already a `ContextVar`. The audit chain shares this assumption and states it
without a guard, since ADR-030's single-writer lock was rolled back (the in-memory
head is the sole duplicate mechanism, so two writers on one path would
interleave). Round 17.1's event registry and key allowlist are two further such
globals, with the same trigger (a concurrent entry point) and the same retrofit;
until then the autouse test reset keeps the suite order-independent.