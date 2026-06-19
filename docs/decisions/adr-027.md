# ADR-027: Audit-Write Failure Policy: Fail-Closed for Custody Events

Status: Accepted. Policy ratified in Round A; the fail-closed mechanism in
Pipeline._emit was implemented in Round C (Round 19). The endpoint-allowlist decision that was
recorded here historically (Round 17.2, K1) was extracted to ADR-036 in the same
consolidation, since it concerns the Triage LLM destination rather than audit-write
completeness.
Date: 2026-06-09
Deciders: Kevin Sokoll

## Context

Technical integrity of the audit trail (ADR-024) does not give completeness: the
chain proving no recorded event was altered says nothing about events never
recorded. For a Behoerde compliance trail a silently missing entry is worse than
a detectably broken chain: a broken chain fails verify_chain(), a never-written
event is invisible.

The original Pipeline._emit caught every audit-publish exception, wrote to stderr,
and never re-raised, so a failed publish was swallowed and the pipeline reported
success while an event was silently absent. Combined with the in-memory chain-head
(ADR-024), a degrading store (disk full, FS latency) would produce months of
successful briefings with missing links, surfacing only at the next
verify_chain(), with no alarm because the swallow never reached a metric.

The trade-off is completeness versus availability: aborting on a failed audit
write delays a citizen's objection during a transient storage hiccup; continuing
lets the system claim auditedness it does not have. This policy was ratified
before the code depending on it was written (pre-registration discipline applied
to an architecture decision).

## Decision

Fail-closed for the six custody events; best-effort is acceptable only for
operational telemetry outside the custody chain.

1. The custody events are EINGANG, TRIAGE, RETRIEVAL, BRIEFING_ERSTELLT,
   KEIN_TREFFER, PIPELINE_FEHLER. For these a failed append raises and the run
   aborts: no briefing is returned that implicitly claims to be audited when it
   is not.
2. End-to-end ordering: the completion event (BRIEFING_ERSTELLT or KEIN_TREFFER)
   is durably appended before run() returns the briefing. The return value is the
   system's claim that the objection was processed and recorded; that claim must
   not precede its own evidence.
3. Failure during failure handling: if emitting PIPELINE_FEHLER itself fails, the
   error is raised with the original pipeline exception chained (raise ... from),
   so neither failure masks the other.
4. Known residual gap, accepted: if ingestion fails before a document_id exists,
   no event can be keyed and none is emitted. The entry point is the place to log
   such pre-identity failures operationally.
5. Visibility regardless of policy: every failed publish increments
   audit_write_failures_total and produces an ERROR status in the timing log, so
   even a future best-effort path cannot degrade invisibly.
6. Operational telemetry (logs, metrics, spans) is explicitly not custody data; a
   logging or metrics failure never aborts a run.

Exception type (settled in ADR-033): the abort is carried by the existing
AuditLogError, not a separate AuditWriteError, because ADR-030 removed duplicate
detection (nothing to distinguish a write failure from) and the single caller
routes the whole recoverable class identically.

## Threat model

A policy that names a control objective must name what it does not protect
against, or the overclaim is itself an audit finding. The keyless hash chain
(ADR-024) this policy keeps complete protects against accidental corruption (a
truncated or reordered event breaks verify_chain at that event and every
successor) and naive single-event tampering (content and timestamp are inside the
hash, each event binds the predecessor). It does not protect against:
rewrite-from-N (an actor with write access who recomputes every hash from the edit
point produces a clean-verifying chain, since a keyless chain has no secret the
rewriter lacks); tail truncation (deleting the last k events plus the head leaves
a shorter chain that still verifies, which the sparse external head anchor in
results.json raises the cost of rather than closes); and a deliberate out-of-store
append (the rolled-back single-writer lock guarded only accidental concurrency
anyway). What closes these is recorded in Deferrals (HMAC defeats rewrite-from-N,
WORM defeats truncation, RFC 3161 defeats backdating). The honest scope is
integrity against accident and naive tampering plus completeness against silent
write failure, not insider-tamper resistance.

## Timestamp assumption (NTP)

Timestamps are inside the hash, so backdating after the fact breaks the chain, but
the evidentiary value of the absolute timestamp rests on the host clock being
correct at write time (assumed maintained by NTP, not enforced). A skewed clock
writes an internally consistent but absolutely wrong chain. RFC 3161 qualified
timestamping is the deferral that would replace the assumption with evidence.

## Raw-store read path (resolved in ADR-033)

The CLI show-document command reads the unmasked original for a document_id,
crossing the trust boundary in the other direction: a read returns PII to whoever
holds store access and originally left no trace (finding H4/S4). The interim was an
operational-only raw_document_accessed log event (document_id, never content), but
the logs are retention-bound and outside the chain. The chain-level read audit
event landed in Round 19 (ADR-033), so raw-store reads now participate in the hash
chain and survive retention. Store access control remains the filesystem posture
of ADR-025.

## Endpoint allowlist (extracted to ADR-036)

The Triage LLM endpoint allowlist (Round 17.2, K1) was recorded here historically.
It is a separate decision, the destination the prompt may be sent to rather than
audit-write completeness, and was extracted to ADR-036 in the consolidation. See
ADR-036 for the decision and its bounded scope.

## Alternatives Considered

1. Fail-open (original behavior). Rejected: accepts silent incompleteness, the
   failure mode this layer exists to eliminate.
2. Fail-open with a non-auditable marker (auditable=False on the briefing).
   Rejected: pushes a compliance decision onto the Sachbearbeiter at read time and
   creates a second class of output needing its own handling rules. More
   machinery, weaker guarantee.
3. Queue-and-retry (buffer failed events, replay later). Rejected: a replay writer
   is a second writer (forbidden by the single-writer design at the time), and
   buffered events break the timestamp-inside-hash ordering. Disproportionate for
   a single-process pipeline.
4. Fail-closed for all events including telemetry. Rejected: couples operational
   noise to domain availability. The custody set is closed and named instead.

## Deferrals (recorded as decisions, not omissions)

Each closes a gap named in the threat model; each is a deliberate scope cut with a
trigger. HMAC with a managed key over each event (defeats rewrite-from-N; trigger:
an insider-tamper threat). WORM storage for the JSONL (defeats truncation and
storage-layer rewrite; trigger: append-only object storage available). RFC 3161
qualified timestamps (replaces the NTP assumption; trigger: a legal requirement
for trusted time). SIEM export of custody and ERROR events (trigger: a central
monitoring obligation). Periodic log-sink scan, now defense-in-depth rather than
primary control since ADR-026 closes the main leak path at the sink.

## Consequences

Positive: the system cannot produce output that falsely implies a complete trail;
audit-store degradation surfaces immediately (failed run, metric, ERROR log)
instead of at the next verify_chain(); the custody event set is an explicit named
contract bounded by an explicit threat model.

Negative: a transient storage failure aborts processing for that objection,
requiring operator intervention or a re-run (the accepted cost); pipeline behavior
changes in Round C, the one deliberate exception to the "observability layer does
not change core pipeline behavior" scope rule.

Neutral: the custody set must be kept in sync if AuditEventType grows, guarded by
a test asserting every member is classified custody or telemetry.

Resolution (ADR-033, Round 19): the metric (Round B) and the fail-closed raise
(this round) are both in place, so the interim is over on the write side. A double
failure (store down and sink unable to persist) now degrades visibility only,
never correctness: the run aborts whether or not the ERROR line could be written,
so no unaudited briefing is produced; the sink-independent metric and the abort
itself are the durable signals, while the located ERROR record is lost for that
event.