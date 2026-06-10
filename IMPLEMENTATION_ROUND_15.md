# CLAUDE.md: Establish observability logging foundation (default-deny)

## Context

The pipeline is functionally complete through Round 14 (NormResolution) but has no
observability layer. The implementation plan (docs/OBSERVABILITY_IMPLEMENTATION.md)
splits the work into three rounds; this is Round A. An external adversarial review
produced findings that grow this round beyond the original Step 1: the structlog
allowlist alone is bypassable by stdlib logging from third-party libraries (Presidio,
OpenTelemetry, urllib3), the event and exception fields are uncontrolled free-text
channels, size-based rotation does not implement a time-bound retention, and the
current `_emit` fallback is a silent-fallback anti-pattern that itself violates the
exception policy (`str(e)` interpolation) and bypasses every control via stderr print.

The conceptual answer is default-deny enforced at the single point all log output
passes through. One sink, one processor chain: structlog routes into stdlib via
`ProcessorFormatter.wrap_for_formatter`, foreign stdlib records route through the
same shared processors via `foreign_pre_chain`, and the chain enforces a key
allowlist, a registered event vocabulary, and exception reduction to type plus
location, before a `TimedRotatingFileHandler` writes the only output. Mechanisms,
not conventions: configuration is an import-time side effect with a runtime
self-check, and every control has a test that asserts behavior at the sink.
Audit-write fail-closed semantics are ratified now (ADR) but implemented in Round C,
when the chain invariants that make aborts diagnosable exist.

## Scope

**In scope:**
- ADR renumbering: the observability policy candidate becomes ADR-026; correct all
  cross-references (PII masking keeps ADR-025).
- ADR-026 "Observability logging policy": one sink architecture (ProcessorFormatter,
  foreign_pre_chain, third-party WARNING clamp), key allowlist as default-deny,
  message policy (event names are registered static constants, variable data goes
  into allowlisted fields), exception policy (type plus location, never `str(e)` or
  rendered tracebacks; rationale: exception messages are foreign-authored text),
  time-based rotation plus startup sweep as the retention mechanism, the
  three-stores lifecycle rationale (chain undeletable and content-free, logs
  pseudonymous with short retention, raw store full content with erasure path),
  and the named residual channels: foreign message text (mitigated by WARNING
  clamp only), values under allowlisted keys (mitigated by type and length
  restriction plus review), `print()` and C-level stderr (out of logging's reach).
- ADR-027 "Audit-write failure policy": fail-closed for the six custody events,
  implemented in Round C; end-to-end ordering (completion event durable before
  `run()` returns); threat model section stating what the keyless hash chain
  protects against (accidental corruption, naive single-event tampering) and what
  it does not (rewrite-from-N and tail truncation by an actor with write access;
  external head anchor in eval results.json is sparse and committed by the same
  actor; advisory lock guards against accidental concurrency, not intent); NTP
  assumption for timestamp evidentiary value; deferrals as decisions: HMAC with
  managed key, WORM storage, RFC 3161 timestamps, SIEM export, periodic log-sink
  scan (re-justified as defense-in-depth, not primary control, after the stdlib
  routing closes the main bypass).
- New module `src/app/observability/` (infrastructure, not a bounded context, not
  core): `logging_config.py`, `events.py`, `correlation.py`.
- `logging_config.py`: shared processor list (merge_contextvars, correlation
  processor, add_log_level, ISO-8601 UTC TimeStamper, ExtraAdder for foreign
  extras, event vocabulary enforcement, exception reduction, key allowlist),
  ProcessorFormatter with `foreign_pre_chain=shared`, structlog configured with
  `stdlib.LoggerFactory` plus `wrap_for_formatter`, root logger with a single
  `TimedRotatingFileHandler(when="midnight", utc=True, backupCount=RETENTION_DAYS)`,
  JSON/Console toggle via `OBSERVABILITY_FORMAT`, third-party loggers clamped to
  WARNING, `sweep_expired_logs()` deleting rotated files past the retention by
  mtime, configuration executed as import-time side effect, runtime self-check on
  first event that raises if the allowlist processor is absent from the chain.
- `events.py`: registered event vocabulary as `Final` string constants plus
  `REGISTERED_EVENTS` frozenset; `UnregisteredLogEventError`.
- `correlation.py`: ContextVar with set/get, correlation id is the document_id.
- `src/app/core/events.py`: extend AuditEvent with `serialization_version: int = 1`
  and `event_hash: str | None = None` (layout only, populated in Round C; None is
  honest for pre-chain events).
- `pipeline.py`: set the correlation id at the start of `run()`, replace existing
  stderr prints with registered structlog events, interim `_emit` fix: catch,
  `log.error(AUDIT_APPEND_FAILED, audit_event_type=..., exc_info=True)`, no raise,
  docstring states the interim status with reference to ADR-027.
- Tests (small_scale unless noted): stdlib bypass (PII via
  `logging.getLogger("presidio-analyzer")` with extra field never reaches the
  sink), allowlist golden test (frozen key set), exception policy test (payload in
  exception message absent at sink, type present), unregistered event name raises,
  correlation id constant across all events of one run, self-check raises when the
  allowlist processor is removed from the chain, sweep deletes only expired rotated
  files, `_emit` failure is logged at ERROR with correlation id and swallowed
  (docstring notes the Round C mutation to `pytest.raises`).
- Dependency: add `structlog` pinned exact (`==` the version resolved at install
  time), consistent with the Presidio pinning lesson.
- Skill update (outside the repo, `~/.claude/skills/ksokoll-engineering/`): add the
  logging rules to the relevant reference file: log messages are static registered
  constants, never interpolated; variable data goes into named fields; exceptions
  are logged as type plus location, never `str(e)` or tracebacks.

**Out of scope:**
- Round B: `@traced` decorator, timing logs, OpenTelemetry wiring, Prometheus
  metrics, corpus identifier, minimal CLI composition root.
- Round C: hash computation, sequence numbers, canonical serialization, fsync,
  single-writer lock, quarantine recovery, `verify_chain()`, head anchoring,
  AuditEvent payload allowlist, the fail-closed raise in `_emit`.
- Periodic log-sink scan (deferred as a decision, documented in ADR-027).
- `_resolve_norms` tidy-up refactor (separate small commit outside this round).
- Any change to masking, triage, retrieval, or briefing logic.
- Retention period legal determination (30-day default is a documented placeholder).

## Steps

1. Create branch: `git checkout -b feat/observability-round-a`.
2. Renumber the observability policy ADR candidate to ADR-026 and fix all
   references: `grep -rn "ADR-025" docs/ src/ tests/` and correct any that mean the
   observability policy; verify ADR-025 refers exclusively to PII masking afterward.
3. Write `docs/decisions/ADR-026-observability-logging-policy.md` per Scope.
4. Write `docs/decisions/ADR-027-audit-write-failure-policy.md` per Scope,
   including the threat model and deferral sections.
5. Commit 1.
6. `pip install structlog --break-system-packages`, then `pip show structlog` and
   pin the exact version in requirements.
7. Create `src/app/observability/` with `__init__.py`, `correlation.py`,
   `events.py`, `logging_config.py` per Scope. Order inside the shared chain:
   contextvars merge, correlation, log level, timestamp, ExtraAdder, vocabulary
   enforcement, exception reduction, allowlist last before handoff.
8. Write the logging test module
   `tests/small_scale/test_observability_logging.py` with a `log_sink` fixture
   (handler redirected to `tmp_path`), covering: stdlib bypass, allowlist golden,
   exception policy, unregistered event, self-check, sweep.
9. Run `pytest tests/small_scale/test_observability_logging.py -q` until green.
10. Commit 2.
11. Extend `src/app/core/events.py` (AuditEvent fields per Scope). Run the full
    suite: `pytest -q`. All existing tests stay green without modification; if an
    existing test asserts the AuditEvent schema, extend the assertion, do not
    weaken it.
12. Commit 3.
13. Wire `pipeline.py`: correlation id at `run()` start, registered events for the
    former stderr prints, interim `_emit` per Scope. Add `RaisingAuditStoreFake`
    to `tests/conftest.py` next to the existing fakes.
14. Add `tests/small_scale/test_pipeline_logging.py`: correlation id constant
    across a run, `_emit` failure logged and swallowed.
15. Verify no ungoverned output remains: `grep -rn "print(" src/app/` returns no
    hits in pipeline or bounded contexts (eval scripts under `scripts/` are
    exempt).
16. Run the full suite: `pytest -q`. Green.
17. Commit 4.
18. Update the skill file outside the repo per Scope (no repo commit).
19. Update `docs/OBSERVABILITY_IMPLEMENTATION.md`: mark Step 1 items as done,
    note the review-driven additions, and record the sink-scan deferral with the
    corrected rationale. Commit 5.

## Commits

5 commits:

```
docs(adr): renumber observability policy to ADR-026, add ADR-027 failure policy

The audit-write failure policy must be ratified before _emit changes
(pre-registration discipline applied to an architecture decision). The
threat model section bounds what the keyless hash chain claims, because
an overclaimed control objective is itself an audit finding.

- renumber observability policy candidate to ADR-026, fix references
- ADR-026: one-sink logging architecture, default-deny allowlist,
  message and exception policy, time-based retention, residual channels
- ADR-027: fail-closed for custody events (implementation in Round C),
  end-to-end ordering, threat model, NTP assumption, deferrals (HMAC,
  WORM, RFC 3161, SIEM, sink scan as defense-in-depth)

State: policies ratified, no code changed.
```

```
feat(observability): logging foundation with default-deny enforcement

Single sink, single processor chain for both logging worlds: structlog
hands off to stdlib via wrap_for_formatter, foreign records route
through the same shared processors via foreign_pre_chain. The allowlist,
event vocabulary, and exception reduction are mechanisms with tests at
the sink, not conventions.

- src/app/observability/: correlation.py (ContextVar, document_id as
  correlation id), events.py (registered vocabulary, frozen set),
  logging_config.py (shared chain, ProcessorFormatter, WARNING clamp,
  TimedRotatingFileHandler, startup sweep, import-time configuration,
  runtime self-check)
- pin structlog exact in requirements
- tests: stdlib bypass, allowlist golden, exception policy, unregistered
  event raises, self-check raises, sweep expiry

State: all log output passes one allowlisted chain; logging tests green.
```

```
feat(core): add serialization_version and event_hash slots to AuditEvent

Round C populates the hash chain; laying out the on-disk shape now
avoids a JSONL format migration of events written in Rounds A and B.
event_hash defaults to None because no hash exists yet; the data format
records the system's history instead of masking it.

- AuditEvent: serialization_version: int = 1, event_hash: str | None = None

State: schema extended, full suite green, no behavior change.
```

```
refactor(pipeline): governed logging and interim _emit visibility

The stderr fallback was a silent-fallback anti-pattern using the one
output channel that bypasses every control, and its str(e) interpolation
violated the exception policy. Interim per ADR-027: failures become
governed ERROR events; the fail-closed raise lands in Round C behind
this same log line.

- set correlation id at run() start
- replace stderr prints with registered structlog events
- _emit: catch, log.error(AUDIT_APPEND_FAILED, audit_event_type=...,
  exc_info=True), no raise, docstring states interim status
- conftest: RaisingAuditStoreFake
- tests: correlation id constant across a run, _emit failure logged at
  ERROR with correlation id and swallowed

State: no print() in src/app pipeline or bounded contexts, full suite green.
```

```
docs(observability): mark Round A complete in the implementation plan

- check off Step 1 items, note review-driven additions (stdlib routing,
  message and exception policy, golden test, time-based retention,
  interim _emit fix)
- record sink-scan deferral with corrected defense-in-depth rationale

State: plan reflects implemented reality.
```

## Done When

- `pytest -q` passes with zero failures, including all pre-existing tests.
- `pytest tests/small_scale/test_observability_logging.py tests/small_scale/test_pipeline_logging.py -q` passes; the suite contains the eight scoped tests.
- `grep -rn "print(" src/app/` returns no hits outside `scripts/` exemptions.
- `grep -rn "str(e)" src/app/` returns no hits.
- A smoke run writes JSON lines to the configured sink where every line carries
  `timestamp`, `level`, `event`, and (within a run) a constant `correlation_id`,
  and every `event` value is in `REGISTERED_EVENTS` or originates from a foreign
  record.
- `docs/decisions/ADR-026-observability-logging-policy.md` and
  `docs/decisions/ADR-027-audit-write-failure-policy.md` exist with the threat
  model and deferral sections; no reference to the old candidate numbering remains.
- AuditEvent carries `serialization_version` and `event_hash`; no existing test
  was weakened.
- The five commits above exist in the stated format on
  `feat/observability-round-a`.
- New ideas surfaced during implementation go to the Round B/C backlog, not into
  this branch.