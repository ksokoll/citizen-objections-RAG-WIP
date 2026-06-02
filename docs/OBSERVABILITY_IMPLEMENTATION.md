# Observability Implementation Plan

Status: Planned
Date: 2026-06-02
Scope: Non-functional layer for the citizen-objections pipeline. Adds
operational observability and legal defensibility without changing the core
pipeline behavior (DocumentIngestion -> Triage -> Retrieval -> Briefing ->
AuditLog).

This is a living roadmap: it records the order of work and its status. The
decisions behind it live in ADRs and are not repeated here:
- ADR-023: Observability as an Architecture Characteristic (the stack and its
  adaptation to a deterministic pipeline).
- ADR-024: Tamper-Evident Audit Trail via Hash Chaining.

## Guiding distinction

The stack is adapted from a prior project that was a stochastic LLM agent
(coordinator loop, variable iterations, tool calls). This project is a
deterministic linear pipeline with an LLM call only in Triage. The full tool
stack is retained; only the metric definitions and the failure mechanics are
re-specified to fit a deterministic pipeline. Rationale in ADR-023.

## Two-step sequencing

The split is driven by data-model coupling, not by "known tools first, new
concepts second". Anything that touches the logging path or the briefing data
model belongs in Step 1. Anything that builds on the audit store as an
independent block belongs in Step 2.

### Step 1: Operational observability plus the two coupled concerns

Done together because all of it touches the logging path or the briefing data
model. (Decision: ADR-023.)

1. Structured logging (structlog). JSON vs console via OBSERVABILITY_FORMAT;
   ISO-8601 UTC timestamp, level, message, correlation id per event.
2. Correlation id via contextvars.ContextVar, anchored on document_id.
3. Distributed tracing (OpenTelemetry). Flat span hierarchy: pipeline.run
   root, one child span per bounded context.
4. Span instrumentation via the @traced decorator (Level 1) on the BC methods
   and the run method. Also tidies _resolve_norms (review point 3).
5. Metrics (prometheus_client), rewritten for the pipeline: objections
   processed (by type or status), processing duration, resolved-vs-unresolved
   norms (the quality signal), arguments per objection. Dropped: iterations,
   tool calls, verification failures. Discipline kept: one purpose per metric,
   cardinality under 100, in-process registry, no backend.
6. PII discipline in logs. Coupled to logging, so done here, not retrofitted:
   once unsafe logs exist the problem is already created. Only the
   pseudonymous document_id is logged; a structlog processor enforces that no
   PII fields pass through. Note: a derivable id is pseudonymization (GDPR
   still applies), the intended trade-off for an auditable trail.
7. Corpus reproducibility. Coupled to the briefing data model: add created_at
   and a corpus identifier (hash over loaded canonical_keys, or the XML
   builddate) to WuerdigungsBriefing, recording which statute version was
   resolved against. Lightweight content-based addressing; no DVC for nine
   static checked-in XMLs. Addresses review point 6.

### Step 2: Tamper-evident audit trail plus retention

Builds on the existing append-only AuditLog as an independent block; does not
depend on Step 1. (Decision: ADR-024.)

1. Hash chaining on the AuditLog. Each AuditEvent gains a SHA-256 hash over
   its canonical content plus the previous event's hash. Add verify_chain()
   and a guarding test (a mutated past event must break verification).
   Append-only alone is a convention, not tamper-evidence.
2. Retention. Mostly conceptual: created_at exists after Step 1; concrete
   statutory periods are sector administrative law (VwVfG and domain rules)
   and are documented, not implemented. GDPR sets only the storage-limitation
   principle.

## Design note spanning both steps

The AuditLog is where operational observability (Step 1, the trace as
operational view) and legal proof obligation (Step 2, the trail as evidence)
meet. Lay out the AuditEvent structure in Step 1 so the hash chaining in
Step 2 only adds a field rather than reshaping it. Not a reason to merge the
steps, only to design the data model in Step 1 with Step 2 in mind.

## Out of scope

- No metrics or span backend exporter in this phase (in-process registry,
  in-memory tracer provider). Production would add an OTLP exporter and a
  Prometheus scrape endpoint.
- No DVC or external data-versioning tool; the corpus is nine static
  checked-in XMLs.
- No full BSI TR-03125 / TR-ESOR archival middleware; principles are
  referenced (ADR-024), the system is not built.
- No external anchoring (RFC-3161 timestamps, ledger) for the audit trail;
  noted in ADR-024 as the next step if insider-tamper resistance were needed.