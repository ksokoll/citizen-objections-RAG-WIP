# Observability and Defensibility Implementation Plan

Status: Planned
Date: 2026-06-02 (revised, third review pass: reliability)
Scope: Non-functional layer for the citizen-objections pipeline
(DocumentIngestion -> Triage -> Retrieval -> Briefing -> AuditLog). Adds
operational observability and strengthens the technical integrity of the audit
trail. With one deliberate exception, the audit-write failure policy (see
Completeness below), this layer does not change core pipeline behavior.

Technical integrity is a building block of legal defensibility, not the whole
of it. Full evidentiary weight additionally requires access control over the
trail, guaranteed completeness (every step provably produces an entry), and
qualified timestamping. This phase delivers integrity, addresses completeness
as a named decision, and enforces the chain's structural invariants; it does
not deliver qualified timestamping or a court-grade archival format. The scope
wording is kept narrower than "legal defensibility" for that reason.

Enforce, do not document. A recurring principle in this revision: an invariant
that is only written down is an implicit dependency, that is, a bug waiting to
happen. Where a property is load-bearing (single writer, processor wiring,
durable append) the plan specifies enforcement and a loud failure, not a
convention.

This is a living roadmap: it records the order of work and its status. The
detailed specifications live in ADRs and are not repeated here:

- ADR-023: Observability as an Architecture Characteristic (the stack, its
  adaptation to a deterministic pipeline, and the tracing-off-by-default
  posture).
- ADR-024: Tamper-Evident Audit Trail via Hash Chaining. Covers the
  canonical-serialization specification and its versioning, durable append and
  chain-head recovery, single-writer enforcement, and the erasure-coexistence
  rationale.
- ADR-026: Observability Logging Policy (one sink, default-deny allowlist,
  message and exception policy, time-based retention).
- ADR-027: Audit-Write Failure Policy (completeness vs availability,
  see the Completeness section).

## Guiding distinction

The stack is adapted from a prior project that was a stochastic LLM agent
(coordinator loop, variable iterations, tool calls). This project is a
deterministic linear pipeline with an LLM call only in Triage. The full tool
stack is retained; only the metric definitions and the failure mechanics are
re-specified to fit a deterministic pipeline. Rationale in ADR-023.

On tracing specifically, ADR-023 states both halves of the justification
honestly. The reasons to adopt OpenTelemetry are transferability and
exporter-readiness: the industry-standard span model is a genuine value for a
portfolio piece targeting regulated environments, and an exporter-ready build
makes production tracing a configuration change, not a rewrite. The
counterweight: in this in-process, synchronous, single-threaded, five-stage
setup with a flat span tree, the operational value of spans over structured
timing logs with a correlation id is limited. The consequence drawn in this
revision (see Step 1.3/1.4): spans are off by default and bounded; timing is
always captured in logs; OTel stays wired as the production upgrade.

## Two-step sequencing

The split is driven by data-model coupling. Anything that touches the logging
path or the briefing data model belongs in Step 1. The audit-store hardening
belongs in Step 2. Step 2 is sequential after Step 1 with a data-model
precondition (see the design note at the end of Step 2); it is not independent
of Step 1.

### Step 1: Operational observability plus the two coupled concerns

Done together because all of it touches the logging path or the briefing data
model. (Decision: ADR-023.)

The numbers below are reference labels, not an execution order. The PII
processor (item 6) is no longer ordered relative to the other items; it is
enforced at logging-module import time (see item 6), so there is no bootstrap
window in which items 1 to 5 could log unsafely.

1. Structured logging (structlog). JSON vs console via OBSERVABILITY_FORMAT;
   ISO-8601 UTC timestamp, level, message, correlation id per event. Log
   retention is implemented in this phase, not deferred: rotation with a defined
   max age and max size (RotatingFileHandler or equivalent). Unlike the audit
   chain, logs are technically deletable, so the storage-limitation obligation
   for this third store of pseudonymous data is satisfied by rotation here
   rather than by the conceptual retention placeholder in Step 2.

2. Correlation id via contextvars.ContextVar, anchored on document_id.

3. Distributed tracing (OpenTelemetry), off by default. Flat span hierarchy:
   pipeline.run root, one child span per bounded context, created only when
   OBSERVABILITY_TRACING is enabled. In normal single-process operation tracing
   is off and timing comes from the structured logs (item 4). When enabled
   without a backend, the span processor uses a per-run-cleared in-memory
   exporter so span memory is explicitly bounded and cannot accumulate across
   requests. Production enables tracing and adds an OTLP exporter. This resolves
   the "spans created, never exported" cost-and-leak failure mode: no spans are
   held in normal operation, and when held they are bounded.

4. Span instrumentation via the @traced decorator. The decorator always emits a
   structured timing log (duration_ms, status) regardless of the tracing flag,
   so timing is captured unconditionally and cheaply; it additionally opens an
   OTel span only when tracing is enabled. This is Stufe 1 der Instrumentierung
   (Decorator) in the three-stage scheme from the Invoice project (Stufe 1
   decorator on stable method boundaries, Stufe 2 manual with-blocks, Stufe 3
   dynamic event attributes); written out rather than "Level 1" to avoid
   collision with the Constraint Hierarchy levels. Applied to the BC service
   methods and the run method.

   Tracked separately from instrumentation: the _resolve_norms refactor
   (extracting the cross-context argument mapping in the Coordinator into its own
   step). Adding a decorator instruments the method; it does not tidy it.

5. Metrics (prometheus_client), rewritten for the pipeline:
   - objections processed (by type or status)
   - processing duration
   - norm resolution: two counters, resolved_total and unresolved_total. The
     quality signal is the ratio unresolved / total; the absolute counts alone
     say little, so both are required and neither is the signal on its own.
   - arguments per objection
   - argument-verification failure rate: share of arguments where the
     original_zitat substring check fails (ADR-006 Layer 1). Deterministic
     counterpart to the dropped agent concept and a direct Triage-LLM quality
     signal.
   - audit_write_failures_total: counts swallowed-or-handled audit publish
     failures, so a degrading audit store is visible regardless of the failure
     policy chosen in ADR-027.

   On "dropped" metrics: the agent's in-loop verification gate is removed.
   Verification as such is not gone; norm resolution is itself a verification
   step (NORM_UNRESOLVED, captured above) and the substring check is captured by
   the verification-failure rate. "iterations" and "tool calls" are genuinely
   dropped; "verification failures" is re-expressed deterministically.

   Inert without an exporter, by design. In this phase the registry is
   in-process with no scrape endpoint, so the metrics are instrumentation
   readiness, not live signals. To keep them from being decorative, the alert
   thresholds are defined now as documentation (for example: unresolved-norm
   ratio sustained above a set fraction; verification-failure rate above a set
   fraction; any nonzero audit_write_failures_total), so that adding a scrape
   endpoint and alert rules in production is a wiring step against a defined
   target. Building the scrape endpoint and the alerting backend stays deferred
   to production (see Out of scope); defining the thresholds does not.

   Discipline kept: one purpose per metric, cardinality under 100, in-process
   registry.

6. PII discipline in logs, enforced not ordered. The allowlist (default-deny)
   structlog processor is installed as a side effect of importing the logging
   configuration module, which the application entry point imports before any
   other module logs. Only the pseudonymous document_id and a fixed set of
   operational fields (event type, span or stage name, duration, status, counts)
   are emitted; any other key is dropped or flagged. A runtime self-check on the
   first log event asserts the processor is present in the chain and fails loudly
   if it is not, so a later logging refactor, an early init log, or a separate
   test or migration logging setup cannot open a leak window. Tests: one pushes a
   PII-shaped field through the processor and asserts it is absent from the
   output; one asserts the correlation id is constant across all events of a run.
   Optional defense-in-depth: a periodic scan of the log sink for non-allowlist
   keys.

   The identifier: document_id is a random uuid4 token, not derived from PII. It
   is pseudonymization, not anonymization, because re-identification remains
   possible through the raw-store mapping while it exists. Because the logs carry
   document_id, the structured logs are a third store of pseudonymous personal
   data alongside the chain and the raw store, covered by the same erasure
   concept (raw-store deletion severs re-identifiability in the logs too) and by
   the log rotation defined in item 1.

7. Corpus reproducibility. Add created_at and a corpus identifier to
   WuerdigungsBriefing. The corpus identifier is the per-statute standangabe (the
   authoritative legal status from the gii XML) plus a content hash over the
   (canonical_key, text) pairs of the loaded corpus. The combined hash dominates
   the alternatives: it includes the keys, so it detects a missing or corrupt
   paragraph, and the text, so it detects a pure text amendment. A keys-only hash
   misses text amendments; builddate is only the file-generation timestamp, a
   weaker signal. Content-based addressing; no DVC for nine static XMLs.

### Step 2: Tamper-evident audit trail plus retention

Hardens the existing append-only AuditLog. Sequential after Step 1 with a
data-model precondition. (Decision: ADR-024.)

1. Hash chaining on the AuditLog. Each AuditEvent gains a SHA-256 hash over its
   canonical content plus the previous event's hash. Append-only alone is a
   convention, not tamper-evidence. The full specification is in ADR-024; the
   load-bearing invariants are split below into enforced (the implementation
   must make them fail loudly) and assumed.

   Enforced:
   - Single writer. Not merely documented: an advisory file lock on the append
     path. A second writer (extra worker, retry thread, reprocessing or cron
     replay) must fail loudly on lock contention rather than append concurrently,
     because concurrent appends corrupt the chain deterministically (each event
     depends on the predecessor hash; this is not mere interleaving and is not
     repairable after the fact). A serialized append queue is deliberately not
     used: a file lock is the proportionate mechanism for a single-process
     pipeline.
   - Durable append before head advance. The in-memory chain-head advances only
     after the event bytes are durably written (fsync). A failed or partial write
     must not advance the head, so the head never references a hash that is not on
     disk.
   - Chain-head recovery on startup. The head lives in memory and replaces the
     store's O(n) duplicate-check. On start it is reconstructed from the JSONL:
     read to the last valid line, truncate a trailing partial line from a crashed
     write, and resume. verify_chain() detects and reports a trailing partial
     line rather than flaking on it.
   - Versioned canonical serialization. Each event persists a serialization
     version. The canonical byte form (sorted keys, fixed separators, stable
     numeric representation) is required because model_dump_json() guarantees
     none of these for payload: dict[str, Any]; without it verify_chain() flakes,
     the worst failure mode for a tamper proof. verify_chain() selects the
     canonicalization by event version, so adding a field later does not
     invalidate historical events. A golden-bytes regression test freezes the
     bytes of older events and runs them against the current verify logic in CI.
   - Genesis event. The first event has a defined prev_hash (all-zero hash) so
     verify_chain() has a rule, not a special case.
   - Timestamp inside the hash, so backdating breaks the chain.

   A guarding test mutates a past event and asserts verify_chain() breaks; a
   second asserts a concurrent second writer fails loudly rather than corrupts.

2. Retention. The audit chain itself is append-only and immutable, so its
   retention is conceptual: statutory periods are sector administrative law
   (VwVfG and domain rules) and are documented, not implemented. The two
   technically-deletable stores have concrete retention already: the raw store
   via erasure on request, the logs via rotation (Step 1 item 1). The mechanism
   that reconciles erasure with the immutable chain is in the Defensibility
   backbone section.

Design-model precondition (why Step 2 is not independent of Step 1): the
AuditEvent structure, including the serialization-version field and the
hash-field slot, must be laid out in Step 1 so Step 2 only populates them rather
than reshaping the on-disk JSONL format. This is a data-model coupling, not a
reason to merge the steps.

## Defensibility backbone: pseudonym in the chain, PII in the raw store

A tamper-evident, hash-chained trail and the GDPR storage-limitation principle
collide head-on: nothing may be deleted from a hash chain without breaking it.
The architecture resolves this:

- The chain contains only the pseudonymous document_id (a random uuid4 token)
  and operational metadata. It never contains PII.
- All PII lives in the separate, access-controlled raw store, keyed by
  document_id.
- An erasure request is satisfied by deleting the raw-store entry. This severs
  re-identifiability: the document_id in the chain and the logs becomes a token
  pointing to nothing. The chain is never mutated, so verify_chain() still passes
  and integrity is preserved.

Erasure operates on the raw store, tamper-evidence operates on the chain, and
the two never touch the same bytes. This is what lets append-only
tamper-evidence and GDPR erasure coexist.

Alternative pattern, noted but not needed here: crypto-shredding (encrypt PII
fields, discard the key) is the standard answer when PII must live inside the
chained events. Because PII is kept out of the chain entirely, raw-store deletion
suffices.

Legal caveat: the architecture provides the mechanism (separating the pseudonym
from the personal data so the link can be cut without mutating the proof).
Whether a specific statutory retention obligation applies, or whether an erasure
request must be honored given a legal-obligation exception under DSGVO, is a
legal determination for a qualified person, not a property asserted here.

## Completeness and the audit-write failure policy

Technical integrity (the chain cannot be altered undetectably) does not give
completeness (every step provably produced an entry). A trail that can silently
miss entries is not a reliable chain of custody, and a missing entry is worse
than a detectably broken chain: a broken chain fails verify_chain(), a
never-written event is invisible.

Current behavior is a completeness gap. Pipeline._emit catches every exception
from the audit publish and writes to stderr without re-raising. A failed publish
is swallowed: the briefing is returned and the pipeline reports success while an
event is silently absent. Combined with the in-memory chain-head, a degrading
audit store (disk full, lock contention, FS latency) would produce months of
successful briefings with missing links and an in-memory head that has advanced
past disk, surfacing only at the next restart or verify_chain(), with no alarm
because the swallow never reached a metric. The durable-append-before-head-
advance invariant (Step 2) and the audit_write_failures_total metric and an
ERROR span status (Step 1) together make the failure visible regardless of the
policy chosen.

Decided in ADR-027: the audit-write failure policy, completeness
versus availability.

- Fail-closed: a failed write for a chain-of-custody event aborts the run (or
  marks the result explicitly non-auditable), so the system never produces output
  claiming to be audited when it is not. This would change core pipeline
  behavior, the one deliberate exception noted in Scope.
- Fail-open (current): the pipeline continues so a citizen still receives a
  briefing on a transient hiccup. Accepts silent incompleteness.

Recommended direction, reinforced by the reliability review and ratified in
ADR-027 before go-live (not after): fail-closed for the six custody events
(EINGANG, TRIAGE, RETRIEVAL, BRIEFING_ERSTELLT, KEIN_TREFFER, PIPELINE_FEHLER),
because for a Behörde compliance trail completeness is the point. Best-effort
handling is acceptable only for operational telemetry outside the custody chain.
The decision is recorded in ADR-027 before _emit is changed.

## Out of scope

Deferred to production (a phase decision, added later, not a permanent boundary):

- Metrics and span backend exporters. This phase uses an in-process registry and
  tracing off by default. Production adds an OTLP exporter and a Prometheus
  scrape endpoint with alert rules wired against the thresholds defined in Step 1
  item 5.
- External data-versioning tooling (DVC). The corpus is nine static checked-in
  XMLs, addressed by standangabe plus content hash.

Out of scope for this project entirely (a permanent boundary):

- Full BSI TR-03125 / TR-ESOR archival middleware. Principles referenced
  (ADR-024); the system is not built. This is the line between technical
  integrity and court-grade evidentiary weight.
- External anchoring (RFC-3161 qualified timestamps, ledger). Noted in ADR-024 as
  the next step if insider-tamper resistance or qualified timestamping were
  required.