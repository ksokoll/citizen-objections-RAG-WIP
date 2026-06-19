# ADR-032: Content-Free by Declaration, Read/Write Validation Split, O(K) Seeding, and the Custody Layer's Order

Status: Accepted
Date: 2026-06-15
Deciders: Kevin Sokoll

> Round 21 rollback note (2026-06-17). This ADR's content-free-by-declaration
> gate (Form B), the read/write validation split, the O(K) head seeding, and the
> custody-layer ordering all stay. What changed underneath it: ADR-030's
> single-writer lock and quarantine recovery were rolled back as out of demo
> scope (ADR-030 superseded). So read this ADR's references to "recover()
> acquires the single-writer lock" and "heals a damaged last line" (section 2)
> and the damaged-tail full-read fallback (section 5) as gone: the slim
> constructor and the explicit recover()/verify_open() steps remain, but recover()
> now seeds the head and raises loudly on a damaged tail rather than locking and
> healing. The O(K) seek-from-end seeding (section 5) is unchanged.

## Context

ADR-024/029/030/031 built a hash-chained, durable, recoverable audit trail and,
in 18a-c, a functionally correct one: all tests green, both verify paths
confirmed. An architecture and a security review of that result found no
correctness defect but a responsibility distribution that had grown rather than
been planned, and two real availability/governance defects that no green test
caught because they sat in the seams.

- store.py had become a god-module: the verification semantics and the chain
  value objects lived in the infrastructure file, separated from the hashing
  half they depend on (A1).
- The publisher protocol promised duplicate detection that ADR-030 removed: a
  false contract (A2).
- The composition root constructed and published a custody event itself, a
  second writer past AuditLogService with audit-schema knowledge in the wiring
  layer (A3).
- The constructor repaired, wrote, and aborted on a tampered tail, so opening
  was neither cheap nor side-effect-free and integrity checking was not optional
  (A5).
- The payload allowlist was a length heuristic sold as a content-free /
  erasure-safe guarantee: `{"namen": ["Max Mustermann", ...]}` validated as
  content-free because every string fit the 128-char bound (Sec-1). The policy
  lived in the shared kernel and bound every AuditEvent producer (A6).
- That validator ran on the read path, so one non-conforming line could make
  every future store-open fail: a latent self-DoS (Sec-3).
- The "fast" tail window was a label: open still read and parsed the whole file
  to seed the head, the O(n) scan ADR-030 claimed to remove, only moved into
  seeding (Sec-2).

These converge into one move: content-free becomes a property of the audit
context, enforced at write entry via a per-event payload schema; the read path
validates no content; and open seeds the head without a full parse. This runs
before the fail-closed work (Round 19) because that work touches the emit path
and the store, and ordering responsibilities and fixing the self-DoS first
avoids cementing the disorder.

## Decision

### 1. Custody layer ordering (A1, A2, A3)

- A1: verification semantics and chain value objects (ChainBreak,
  VerificationResult, verify_chain) move into audit_log/verification.py, beside
  the hashing functions in serialization.py; the chain head and its anchor form
  (ChainHead, head_anchor) move into audit_log/anchor.py. store.py keeps only
  the file-bound entry (verify_chain_file, the open-time tail-window call) and
  the storage mechanics. Pure relocation; verify still recomputes via the single
  canonical serializer.
- A2: the AuditEventPublisherProtocol docstring drops the duplicate-detection
  guarantee. The durable store keys the chain off an in-memory head and does not
  scan per append (ADR-030), so a re-published id is not rejected; the pipeline
  mints a fresh id per event, making that a deliberate trade. Append-only and
  failure-translation are the guarantees the protocol makes.
- A3: AuditLogService.record_startup_config(provenance) constructs and publishes
  the STARTKONFIGURATION custody event (event type, SYSTEM sentinel, fresh id).
  The composition root supplies provenance values and calls it; audit-schema
  knowledge leaves the wiring layer, consistent with the in-context recovery
  event.

### 2. Slim constructor, explicit recover() and verify_open() (A5)

Opening is cheap and side-effect-free: the constructor records the path, seeds
the genesis head, and ensures the file exists. It reads, writes, and verifies
nothing. A writing composition path continues the chain by calling two explicit
steps: recover() seeds the head from disk (and, after the Round 21 rollback,
raises loudly on a damaged tail rather than locking and healing it); verify_open()
runs the fast tail-window check and raises on a break. A read-only consumer
(query, an auditor) skips both, so opening a tampered file for a read never seeds
a head or aborts on a tail-verify. Integrity checking is opt-in, on the path that
writes.

### 3. Content-free by per-event key declaration (Sec-1, A6: Form B)

Each AuditEventType declares its allowed payload keys and each key's type (a
scalar, or a one-level FlatDict of a scalar) in the audit context
(audit_log/payload_schema.py). The store validates the payload against the
event's schema at write entry: only declared keys with declared types pass,
everything else is rejected loudly as a PayloadSchemaError. masked_entity_counts
is a declared dict[str, int] on the EINGANG event, not a generic flat-container
allowance. `{"namen": [...]}` cannot pass because `namen` is declared on no
event. The 128-char length heuristic and the generic flat-container rule are
gone; the guarantee is positive declaration, not a length bound.

The policy leaves the kernel: core/events.py keeps only the minimal AuditEvent
model contract and documents that the audit context governs the payload's shape
at write entry. The model no longer validates the payload at construction, so
there is no misleading construction invariant and no model_copy bypass to close.

### 4. Read/write validation split (Sec-3)

The schema is enforced only at write entry (_append_durably), never on the read
path. The read path reconstructs historical events tolerantly: the hash chain
checks integrity, and a content rule has no business failing a store open. A
pre-18d line, or one written before a later tightening of a schema, is read back
without a content check, so a non-conforming line never translates into an open
failure. The integrity guarantee is unchanged: a broken hash still surfaces, at
the tail window and in the full CLI walk.

### 5. O(K) head seeding (Sec-2)

Open seeds the head without a full parse. _read_last_lines seeks from the file
end and parses only the last tail_window+1 lines; recover() seeds the head from
that window, and verify_open() verifies it. A damaged tail makes recover() raise
loudly at open (Round 21, replacing ADR-030's full-read quarantine fallback), so
the common clean open is O(K) and a damaged one fails fast. The tail window's
documented promise that open does not scan the whole trail is now true. A break
index reported at open is within the verified window, the documented meaning of a
ChainBreak index for a windowed walk.

### 6. Anchor core logic under static analysis (A4, second part)

The load-bearing anchor logic (merging the chain head into an eval's
results.json under the reserved chain_anchor key) moves into
audit_log/anchor.py (results_with_anchor), where mypy and ruff check it. The
eval script under experiments/ keeps only the file-write glue and calls it. The
anchor that the external-witness argument rests on no longer escapes static
analysis. The head-into-the-protocol typing and the dedicated frequent anchor
command remain backlog, with the second-backend / operational trigger (A4 first
part).

## Threat model update

The chain remains tamper-evident, not tamper-proof, and content-free by
construction. Relative to ADR-031:

- The content-free property is now mechanical by positive declaration, not a
  string-length heuristic. A text fragment cannot enter the chain by accident
  because it would arrive under an undeclared key, which write entry rejects.
  This is what licenses the chain coexisting with the right to erasure (the
  chain is undeletable; erasure reaches only the raw store), and it now rests on
  key declaration rather than on string length.
- The read path can no longer be turned into a self-DoS: a single non-conforming
  line does not fail every future open, because content is not checked on read.
  Integrity is still checked there by the hash chain.
- The HMAC-with-a-managed-key, WORM-storage, and RFC 3161 deferrals from ADR-027
  are unchanged, each with its stated trigger.

## Alternatives Considered

1. Keep the length bound and raise it / tune it. Rejected: any length bound
   admits a short name. The defect is the heuristic itself, not its threshold.
2. Hash the payload to a single fingerprint instead of declaring its shape.
   Rejected (as in ADR-031): a fingerprint loses the counts and versions that
   make the chain operationally useful; positive declaration keeps the data and
   the content-free property.
3. Enforce the schema on both read and write. Rejected: it reintroduces the
   self-DoS (Sec-3); a content rule failing an open is exactly the latent defect
   this ADR removes. Integrity, not content, is the read path's concern.
4. Keep verifying / seeding from a full read and accept O(n) open. Rejected: it
   makes the tail-window promise a label, and startup cost grows with the trail.
   The seek-based tail read makes the common open O(K).
5. Hold the single-writer lock for the store's lifetime in the constructor.
   Rejected: it deadlocks the supported reopen-to-continue pattern (two store
   instances on one path); the lock stays per-critical-section, acquired in
   recover() and each append.
6. Leave recover()/verify_open() implicit in the constructor (status quo).
   Rejected: it makes a read-only open pay for recovery writes and abort on a
   tampered tail. The split makes the cost and the side effects land on the
   writing path only.

## Consequences

Positive:

- The content-free guarantee is mechanical and lives with the context that owns
  the chain; the kernel is the minimal model contract again.
- A read can never be turned into a self-DoS, and a reader never triggers a
  recovery write or a verify abort.
- The common open is O(K), so the tail-window promise is honest.
- The load-bearing anchor logic is under mypy and ruff.

Negative:

- A new custody event must declare its payload keys in payload_schema.py before
  it can carry a payload, and every event type must appear there or it fails
  closed. Intended: positive declaration is the guarantee.
- recover()/verify_open() are now explicit steps a writing path must call; a
  caller that forgets recover() before publishing a reopened chain would seed
  from genesis. The composition root calls them; the contract is documented on
  the constructor.

Neutral:

- The fail-closed raise in the emit path, the chain-based read-access audit
  event, and the dedicated anchor command remain Round 19 / backlog.

## References

- ADR-031 (predecessor): chain verification, the payload allowlist this ADR
  supersedes (section 2), head anchoring, and config-in-chain. The 18d honesty
  note there records what the length-heuristic wording overclaimed.
- ADR-030 (predecessor): durable append, single writer, tail recovery; this ADR
  splits its open-time recovery into the explicit recover()/verify_open() steps
  and keeps its damaged-tail handling as the O(K) seeding's fallback.
- ADR-029: the single canonical serializer the relocated verify path still
  reuses.
- ADR-027: the fail-closed policy and threat model that name the content-free
  payload by reference; this ADR makes it mechanical and updates the threat
  model's basis from string length to key declaration.
- ADR-026: the log-key allowlist whose default-deny, per-context-declaration
  shape the per-event payload schema mirrors.
