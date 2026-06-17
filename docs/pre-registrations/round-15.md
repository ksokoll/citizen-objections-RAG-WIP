# Iteration 15 Plan: Observability and Defensibility Layer (three rounds)

Status: Pre-registered. Written before implementation per the process changes in LESSONS_LEARNED_EXPERIMENTS.md. Predictions and scope boundaries in this document are committed before any code is written.

Date: 2026-06-09

Companion documents: OBSERVABILITY_IMPLEMENTATION.md (the design, three review passes), ADR-023 (observability as an architecture characteristic), ADR-024 (hash-chained audit trail), ADR-026 (observability logging policy), ADR-027 (audit-write failure policy).

---

## Motivation

The pipeline (DocumentIngestion -> Triage -> Retrieval -> Briefing -> AuditLog) is functionally complete and has a tamper-evidence design (ADR-024) but no operational layer and no enforcement of the integrity and completeness invariants that design names. This iteration builds the non-functional layer: structured logging with PII discipline, instrumentation-ready tracing and metrics, and a hash-chained, single-writer, fail-closed audit trail. The driver is the simulated Behoerde deployment, where the audit trail is potential legal evidence and a missing or alterable entry is a compliance failure, not a degraded result.

This is an engineering iteration, not a measurement experiment. Most predictions below are pass/fail invariants verified by tests, not metrics with a recall target. Where a quantity is predicted (latency, overhead), it is labeled as such. The pre-registration discipline still applies: the round boundaries, the enforced-versus-assumed split, and the explicit scope cuts are committed here before implementation so that scope creep during the build is visible as a deviation.

## Hypothesis (invariants and predictions)

The layer makes the load-bearing properties of OBSERVABILITY_IMPLEMENTATION.md enforced rather than documented, at a cost proportionate to a single-process pipeline.

Sub-hypotheses / committed invariants:

- H-A (PII discipline): With the default-deny allowlist processor installed as an import side effect, no non-allowlist key reaches the log sink, and a missing processor fails loudly on the first log event. Predicted: a PII-shaped field pushed through the processor is absent from output; removing the processor raises on first emit.
- H-B (correlation): Every event of a single run carries the same correlation id, anchored on document_id. Predicted: constant correlation id across all events of a run.
- H-C (timing always, spans optional): The @traced decorator emits a timing log unconditionally and opens an OTel span only when tracing is enabled; when enabled without a backend, span memory is bounded (in-memory exporter cleared per run). Predicted: timing log present with tracing off; exactly one span per stage with tracing on; exporter empty after the run.
- H-D (tamper-evidence): Any single-byte mutation of a past event breaks verify_chain() at that event and every successor. Predicted: a guarding test that mutates a past event observes a break; an unmutated chain verifies clean.
- H-E (single writer): A second concurrent writer on the append path fails loudly on lock contention rather than appending and corrupting the chain. Predicted: the second writer raises; the chain remains valid.
- H-F (durable append before head advance): A failed or partial write does not advance the in-memory head; on restart, chain-head recovery truncates a trailing partial line and resumes. Predicted: a simulated partial line is truncated on recovery and verify_chain() reports it rather than flaking.
- H-G (completeness / fail-closed): A failed durable append of a custody event aborts the run with AuditWriteError; no briefing is returned. Predicted: with a store that raises on publish, run() raises and returns no briefing; audit_write_failures_total increments.

## Architecture

New infrastructure module `src/app/observability/`, a sixth top-level module beside the five bounded contexts. It is explicitly not a bounded context and not a cross-context contract holder: it is cross-cutting infrastructure. (Decision: recorded in ADR-023; placement ratified here rather than in core/ to preserve the core "cross-context contracts only" rule that the MaskingResult relocation also enforces.)

Dependency rule for the new module:
```
observability  ->  (stdlib, structlog, opentelemetry, prometheus_client)   OK
pipeline.py    ->  observability                                            OK
BC services    ->  observability (the @traced decorator, the logger)        OK
observability  ->  any BC                                                   FORBIDDEN
observability  ->  core                                                     FORBIDDEN (no domain types in the instrumentation layer)
```

Module contents:
- `logging_config.py`: structlog configuration, the default-deny allowlist processor, the first-event self-check. Installs the processor chain as an import side effect; the entry point imports this before any other module logs.
- `correlation.py`: the contextvars.ContextVar and a context manager that sets it on run() entry.
- `tracing.py`: the @traced decorator, the OTel provider wiring, the per-run-cleared in-memory exporter, the OBSERVABILITY_TRACING gate.
- `metrics.py`: the in-process prometheus_client registry and the six metric definitions. Alert thresholds documented here as comments, not wired.

The audit-store hardening (Round C) lives in the existing `src/app/audit_log/store.py`, not in the observability module: it is the AuditLog context's own infrastructure. The observability module provides the metric the store increments on write failure.

## Round split

Three rounds, each independently committable, ordered by the data-model coupling in OBSERVABILITY_IMPLEMENTATION.md (the AuditEvent layout must exist before the hash chain populates it). ADR-027 is ratified before Round C touches Pipeline._emit.

### Round A: Structured logging, PII discipline, AuditEvent layout

Gate: ADR-025 (PII, already done), ADR-026 (observability logging policy) and ADR-027 (audit-write failure policy) ratified, BOUNDED_CONTEXTS.md error-propagation table corrected.

Build:
- structlog with JSON/console toggle via OBSERVABILITY_FORMAT; ISO-8601 UTC, level, message, correlation id per event.
- Log retention: RotatingFileHandler (or equivalent) with defined max age and max size. This satisfies the storage-limitation obligation for the logs as a third store of pseudonymous data; not deferred.
- Correlation id via contextvars.ContextVar anchored on document_id, set on run() entry.
- Default-deny allowlist processor installed as an import side effect; runtime self-check on the first log event asserts the processor is in the chain and fails loudly otherwise.
- AuditEvent gains serialization_version and an event_hash slot (layout only; the hash is populated in Round C). prev_hash slot likewise.

Tools: structlog (pinned ==, per the Presidio pinning lesson), stdlib logging.handlers.RotatingFileHandler, stdlib contextvars.

### Round B: Timing, tracing-readiness, metrics, corpus identifier

Build:
- @traced decorator: always emits a timing log (duration_ms, status); opens an OTel span only when OBSERVABILITY_TRACING is set. Applied to the five BC service methods and run(). This is Stufe 1 (decorator on stable method boundaries) from the Invoice three-stage scheme.
- OTel wiring: flat span hierarchy (pipeline.run root, one child per BC), per-run-cleared in-memory exporter, no backend.
- prometheus_client in-process registry with the six metrics: objections processed (by type/status), processing duration, norm resolution (resolved_total and unresolved_total, the ratio is the signal), arguments per objection, argument-verification failure rate, audit_write_failures_total. Alert thresholds documented, not wired.
- Corpus reproducibility: created_at and a corpus identifier on WuerdigungsBriefing. Corpus id = per-statute standangabe + SHA-256 over sorted (canonical_key, text) pairs.

Tracked separately, not part of this round: the _resolve_norms extraction refactor. A decorator instruments the method; it does not tidy it. Done as its own small commit if done at all.

Tools: opentelemetry-sdk, opentelemetry-api, prometheus_client, stdlib hashlib.

### Round C: Hash chain, single writer, durable append, fail-closed

> Round 21 rollback (2026-06-17). Three mechanisms in the Round C build below,
> fsync durability, the single-writer advisory file lock, and the truncating
> quarantine tail recovery, were built and then deliberately rolled back as out
> of demo scope; a damaged tail now fails loudly at open instead of being healed.
> The manipulation-evidence core (the hash chain with genesis and sequence
> numbers, verify_chain, the Form-B content-free payload gate, head anchoring,
> and fail-closed _emit) stays. See the Deviations Log (Round 21) and ADR-030
> (superseded). The build bullets below are the original pre-registered plan,
> preserved as the pre-registration record, not the current build.

Build:
- Canonical serialization: json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False), versioned via the Round A field. Not model_dump_json() (no ordering or separator guarantee for payload: dict[str, Any]).
- SHA-256 over canonical content plus prev_hash. Genesis event with all-zero prev_hash. Timestamp inside the hash.
- In-memory chain-head replaces the O(n) duplicate check. Startup recovery: read to last valid line, truncate a trailing partial line, resume.
- Durable append: write, flush, os.fsync, then advance head.
- Advisory file lock on the append path (filelock; works on Windows, unlike fcntl). Second writer fails loudly on contention.
- verify_chain() with version dispatch. Golden-bytes regression test freezes v1 event bytes and runs them against current verify logic in CI.
- Pipeline._emit rewritten to fail-closed for the six custody events per ADR-027: raise AuditWriteError, increment audit_write_failures_total, ERROR timing status.

Tools: stdlib hashlib, stdlib os.fsync, filelock.

## Configurations

- Environment knobs: OBSERVABILITY_FORMAT (json|console), OBSERVABILITY_TRACING (off by default).
- Log sink: rotating file, max age and max size defined in logging_config.py (committed values, not left implicit).
- Tracing exporter: in-memory, per-run-cleared. No OTLP exporter in this phase.
- Metrics registry: in-process, no scrape endpoint in this phase.
- Hash: SHA-256. Lock: advisory file lock, one append path, single process.
- Custody event set (closed, named): EINGANG, TRIAGE, RETRIEVAL, BRIEFING_ERSTELLT, KEIN_TREFFER, PIPELINE_FEHLER.

## Predicted Outcomes

Invariants (pass/fail, verified by the tests under Measurement): H-A through H-G as stated above.

Quantitative predictions (committed before measurement):
- @traced timing-log overhead with tracing off: under 1 ms per decorated call (a log emit plus a perf_counter delta; no span machinery on this path).
- verify_chain() over a chain of 1000 events: under 100 ms (linear SHA-256 recompute, negligible at this volume).
- fsync per audit append: the dominant per-event cost; predicted single-digit milliseconds on local disk, acceptable for the Sachbearbeiter-synchronous use case.
- Corpus content-hash build over the nine Gesetze: under 1 second (hashing already-parsed text, no embedding).

These are predictions, not targets to optimize toward. If fsync latency proves materially higher, that is a recorded finding, not a reason to weaken durability.

## Smoketest (before the guarding tests in each round)

- Round A: import the entry point, emit one log event, assert JSON output contains the correlation id and the timestamp and contains no non-allowlist key. Sixty seconds, catches a broken processor chain before the full test run.
- Round B: run() once with tracing off, assert a timing log per stage; run() once with tracing on, assert one span per stage and an empty exporter afterward.
- Round C: append a genesis event and one successor, call verify_chain(), assert clean; mutate the successor in memory, assert verify_chain() reports the break at the successor.

## Measurement

Verification is by test, not by an eval harness. Tests are placed by scale per the existing convention (small_scale unit, medium_scale against real infrastructure).

- Round A (small_scale): PII-shaped field absent from output; correlation id constant across a run; self-check raises when the processor is removed from the chain.
- Round B: timing log present with tracing off; exactly one span per stage with tracing on and empty exporter after; corpus hash changes on a paragraph text amendment and on a missing key (two separate tests, two failure modes); the resolved/unresolved counters increment on an unresolved norm.
- Round C: mutating a past event breaks verify_chain(); a concurrent second writer fails loudly rather than corrupting; recovery truncates a simulated trailing partial line and resumes; golden-bytes regression test passes against frozen v1 bytes; fail-closed: a Fake store that raises on publish makes run() raise AuditWriteError and return no briefing, and increments audit_write_failures_total.
- A contract test (small_scale) asserts every AuditEventType member is classified as either custody or telemetry, so the ADR-027 custody set cannot silently drift when the enum grows.

No per-Gesetz recall table or aggregation step: this iteration produces no eval JSON. The reproducibility infrastructure (git short-sha in result files) does not apply because there are no result files; the corpus hash is the reproducibility artifact for the briefing instead.

## Stop Rule

The iteration is complete when:
1. ADR-027 is Accepted and BOUNDED_CONTEXTS.md reflects the fail-closed propagation.
2. Round A is committed: structured logging, PII allowlist with self-check, rotation, correlation id, AuditEvent layout fields present.
3. Round B is committed: @traced (timing always, span optional, bounded exporter), the six metrics with documented thresholds, corpus identifier on the briefing.
4. Round C is committed: hash chain with genesis and versioned canonical serialization, single-writer lock, durable-append-before-head-advance, chain-head recovery, fail-closed _emit, all guarding tests green including the golden-bytes regression in CI.

Deferred to a backlog and explicitly not extending this iteration: OTLP exporter, Prometheus scrape endpoint and live alert rules, BSI TR-03125 / TR-ESOR archival middleware, RFC-3161 qualified timestamps or external anchoring, the periodic log-sink scan, DVC for the corpus.

## Open Risks

- Canonical-serialization stability is the main implementation risk (ADR-024). If the canonical byte form is not stable across Python or library versions, verify_chain() produces false breaks, the worst failure mode for a tamper proof. Mitigated by the golden-bytes regression test in CI and by versioning the serialization so a future change does not invalidate historical events.
- fsync semantics differ across platforms and filesystems. On the Windows development target, os.fsync maps to FlushFileBuffers; this is assumed sufficient for durability and is not independently verified against power-loss. Recorded as an assumed (not enforced) property, consistent with the project's enforced/assumed split.
- filelock advisory semantics: an advisory lock binds only cooperating processes. A writer that bypasses the store and appends to the JSONL directly is not stopped. Accepted for a single-process pipeline with review discipline; recorded rather than silently assumed.
- contextvars and the import-side-effect processor install assume a single synchronous run path. If a future async or threaded entry point is added, the ContextVar propagation and the single-writer assumption both need revisiting. In scope to note, out of scope to build (ADR notes synchronous Coordinator).
- Scope creep risk specific to this iteration: the metric set and the test suite are the two places where "just one more" is tempting. The six metrics and the named guarding tests are the committed set. Adding more is a deviation to be recorded, not a silent extension.

## Deviations Log

Deviations from this pre-registration are recorded here during implementation (what changed, why), per the LESSONS_LEARNED process change.

- Round A. ADR numbering: the observability logging policy took ADR-026 and the audit-write failure policy moved to ADR-027 (the pre-registration originally called the audit-write policy ADR-026). The split into two ADRs was review-driven; the logging policy grew large enough to warrant its own record.
- Round A. Two ungoverned stderr prints in DocumentIngestion (a world-readable-store warning and a PII coverage anomaly) were converted to governed structlog events. The pre-registration scoped masking-file changes out, but step 15's "no ungoverned output in src/app" required it, and one print interpolated surviving citizen NAME tokens to stderr (a real PII leak through the channel logging cannot govern). The conversion changes the output mechanism only, not masking logic, and logs counts not tokens.
- Round A. The key allowlist gained three operational fields (survivor_count, name_regions_masked, store_mode) to carry the two converted ingestion events as counts. This is a deliberate, golden-test-gated widening of the default-deny set, not a silent one.
- Round 15.2. A reliability review found the two Round A enforcement choices sat in the wrong phase: import-time configuration could abort with a raw traceback, the self-check and the vocabulary check raised into the request path, and the documented sweep was never called. Implemented phase separation (ADR-026): strict fail-loud bootstrap (ObservabilityBootstrapError, configure-time self-check, sweep wired in, log_sink_size_bytes startup event) and unbreakable runtime (never_raise around every own processor, mode-dependent vocabulary check that raises in strict CI mode and substitutes in production). No degradation to NullHandler or stderr, recorded as a deliberate decision in ADR-026.
- Round 15.2. The key allowlist gained three more operational fields (sink_size_bytes, failed_processor, caller_location) for the self-instrumentation events. Golden-test-gated widening, not silent.
- Round 15.2. Commit granularity: the planned four commits became three. The bootstrap and runtime commits (planned 1 and 2) were combined because they edit the same functions (the module docstring, ALLOWED_KEYS, _build_shared_processors); an intermediate split would not have passed tests cleanly. The pipeline (planned 3) and docs (planned 4) commits stayed separate.
- Round 15.2. Manual Windows rollover check (deployment host, WindowsPath sink). Forcing a rollover while a second handle held the active file open raised PermissionError ("Der Prozess kann nicht auf die Datei zugreifen, da sie von einem anderen Prozess verwendet wird"), created no rotated file, and left the active file in place (observed growth 227 to 323 bytes after a further write). Confirms the documented failure mode: a held handle blocks the midnight rename, rotation and retention silently fail on Windows, and the active file grows unbounded. The log_sink_size_bytes startup event is the observability for this; the single-process assumption in ADR-026 now extends to open file handles.
- Round B. Norm resolution is one counter family, norm_resolution_total with a result label (resolved, unresolved), instead of the pre-registered "two counters resolved_total and unresolved_total". Both counts exist as the two labeled series and the ratio signal is unchanged; the labeled family keeps the committed "six metrics, no seventh" literal at the collector level (guarded by a change-detector test) and is the idiomatic Prometheus shape for a two-way split of one thing.
- Round B. The corpus identifier is the content hash only: SHA-256 over the sorted (canonical_key, text) pairs, NUL-separated. The per-statute standangabe named in the pre-registration is not folded into the id, per the Round 16 spec (deliberately content-based and free of any tool version). The hash subsumes what the standangabe would signal (any text or paragraph change changes the id); the standangabe remains in the XML when display metadata is needed.
- Round B. The planned README boundary sentence had no target: README.md did not exist. Created a minimal README (project summary, scope section carrying the ADR-028 boundary sentence, run commands) rather than dropping the planned edit.
- Round B. Span and timing grain for AuditLog: the @traced decorator sits on AuditLogService.publish, which the Coordinator calls once per custody event, so a happy-path run has four audit_log spans and timing events while the five linear stages have exactly one each under the pipeline.run root. "One child span per bounded context" holds for the linear stages; per-publish grain is the honest shape for a service invoked per event and is pinned in the span test.
- Round B. Alert thresholds got initial concrete values as documentation in metrics.py (unresolved-norm ratio above 0.20 sustained one hour, verification-failure rate above 0.10 sustained one hour, any nonzero audit_write_failures_total pages). The pre-registration committed to defining thresholds without naming numbers; these are starting values to calibrate against real traffic, not validated operating points.
- Round B. startup_config is emitted by both CLI commands, but show-document omits corpus_id and model_id: it loads no corpus and wires no LLM, and reporting invented values would be false provenance. The process command emits the full field set.
- Round 16.1. The metric set grew from the committed six to seven: triage_contradictions_total counts the norms-present-but-no-arguments contradiction, the observable signature of a prompt-injected extraction suppression (security finding S3). This is exactly the scope-creep class the pre-registration named, widened deliberately rather than silently: the change-detector test now pins seven collectors (its purpose is visibility, not prohibition), and this entry is the record. No further metric joined.
- Round 16.1. The OBSERVABILITY_FORMAT environment knob named under Configurations was removed (security finding S7): the log format is now a CLI decision only (--log-format, default json), because an environment variable could silently switch a deployment to the console renderer. The active format is recorded in startup_config (ADR-026, Renderer).
- Round 16.1. The key allowlist gained five operational fields, golden-test-gated as before: the four resolved store paths recorded in startup_config (app_home, log_dir, raw_store, audit_log; finding S5) and the document_id of the raw-document access trace (findings H4/S4). Three events joined the registered vocabulary one constant at a time: app.unhandled_error (CLI catch-all, S1/M4), ingestion.raw_document_accessed (H4/S4), triage.contradiction_detected (S3).
- Round 21 (Rollback A and B). Three Round C mechanisms were deliberately rolled back as out of demo scope: fsync durability (the append still flushes but no longer fsyncs) and the truncating quarantine tail recovery, both under H-F, and the single-writer advisory file lock under H-E. A damaged tail now raises AuditLogError loudly at open instead of being healed, so H-E and H-F are no longer enforced. The manipulation-evidence core stays intact: the hash chain with genesis and sequence numbers, verify_chain, the Form-B content-free payload gate, head anchoring, and fail-closed _emit (H-G), which still holds because the append still raises on OSError. An ownership review judged the rolled-back infra depth target-far for an ML/AI portfolio (the applicant builds ML/AI, not systems/infra); ADR-030, now superseded, records the deferral and the production-restore path.