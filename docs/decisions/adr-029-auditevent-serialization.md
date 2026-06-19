# ADR-029: Canonical AuditEvent Serialization and Chain Mechanics

Status: Accepted
Date: 2026-06-13
Deciders: Kevin Sokoll

## Context

ADR-024 decided to make the audit trail tamper-evident by hash-chaining each
AuditEvent: a SHA-256 over the event's canonical content combined with the
predecessor's hash, computed by the store at append time. It named the main
implementation risk in its own Consequences: the hash must be computed over a
canonical serialization of each event, and the canonical form must be stable or
verification produces false breaks. It did not fix how that canonical form is
produced, what exactly is hashed, or what the first event chains from. Those are
the decisions here, made before the chain they govern is populated end to end
(durable append is phase 18b, verify_chain is phase 18c).

The risk is specific and silent. If the bytes the verify path feeds to SHA-256
differ in any way from the bytes the write path fed in (a reordered key, a
changed separator, a unicode escape, a datetime rendered differently), verify
computes a different digest and reports a break that no tampering caused. The
failure is invisible to ordinary tests: every event still serializes, roundtrips,
and stores. Only the recomputed hash disagrees, and only once someone verifies.

## Decision

### 1. Canonical serialization, not model_dump_json

The hashed bytes are produced by a single function (audit_log/serialization.py,
canonical_bytes) shared by the write path and the future verify path:
json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
encoded utf-8, where content is the event as a JSON-mode dict with event_hash
excluded.

Pydantic's model_dump_json() is deliberately not used for the hash input. Its
byte form is a serializer implementation detail: it does not promise sorted keys,
and separator whitespace and unicode escaping can shift across Pydantic or Python
versions. A shift would re-hash every historical event differently and break the
chain with no tampering. The storage line on disk stays model_dump_json (it only
has to roundtrip the field values, and it does); the hash input is the canonical
bytes. These are two serializations with two jobs, and only the canonical one is
load-bearing for the proof.

One function, not two copies: write and verify hashing the same event must agree
by construction, so the canonicalization has exactly one definition.

Version dispatch: the serializer is selected per event by serialization_version
(a field present since Round 16). v1 is the form above. A dispatch table keys
serializers by version, so a future v2 format can be added without invalidating
v1 events: each event records the version that produced its bytes, and that
version reproduces them. An unregistered version raises rather than silently
hashing a default shape.

### 2. The sequence number is part of the hashed content

Each event carries a monotonic sequence_number, 0 for genesis, included in the
canonical bytes. Binding the position into the hash means events cannot be
reordered without changing their hashes: order is part of what the chain
attests, not only the prev-hash links. The field sits on the AuditEvent model in
core, alongside serialization_version and event_hash, because it is custody
content the consumer of an event reads, not an observability log key. It is
therefore not registered through the 17.1 per-context log registration API
(AUDIT_EVENTS / AUDIT_KEYS), which governs structured-log event names and field
keys: a different mechanism for a different concern.

The field is declared together with the serializer (one commit), not with the
store's assignment logic (the next commit), so the canonical format and the
golden bytes that pin it are defined exactly once. Splitting the field out would
have changed the golden bytes between commits inside this round, the precise
silent-drift the round exists to prevent.

The store owns the value: it assigns sequence_number and event_hash on append
from its in-memory head and overwrites anything a caller set, so neither
position nor hash is forgeable by the caller.

### 3. Genesis chains from an all-zero sentinel

The first event has no predecessor, so its prev_hash is a fixed sentinel:
GENESIS_PREV_HASH, 64 zero hex chars, the width of a SHA-256 hex digest. It is a
named constant, not a literal scattered across call sites, so write and verify
anchor the chain identically.

## Rationale

- Determinism is the whole property. sort_keys removes dict order as a variable,
  tight separators and ensure_ascii=False remove whitespace and escaping as
  variables, utf-8 fixes the encoding. The result depends on content alone.
- Excluding event_hash from its own input is necessary, not stylistic: the hash
  is the output of hashing the content, so it cannot also be an input.
- A golden-byte regression test freezes the exact v1 bytes. An accidental format
  change becomes a failing test here instead of an undetectable broken proof
  elsewhere, which is the concrete guard ADR-024 promised against its named risk.
- The byte form was verified to survive a model_dump_json then
  model_validate_json roundtrip for both whole-second and microsecond UTC
  timestamps, so the verify path recomputes the write path's bytes from a stored
  line rather than a fresh in-memory object.

## Alternatives Considered

1. model_dump_json() as the hash input. Rejected: non-guaranteed key order and a
   version-dependent byte form make the digest unstable, defeating the proof.
2. A hand-built field concatenation (f-strings joined by a separator). Rejected:
   it reinvents JSON escaping badly, and a value containing the separator would
   be ambiguous. json.dumps already solves escaping correctly.
3. A third-party canonical-JSON library (JCS, RFC 8785). Rejected as an
   unnecessary dependency: the payload is controlled (the 18c allowlist restricts
   it to simple types), so stdlib json with fixed options is sufficient and adds
   no supply-chain or version surface to manage.
4. Sequence number outside the hash (ordering carried only by prev-hash links).
   Rejected: without the position in the hash, a reordering that preserved the
   link structure would not register as a content change.
5. A random or timestamp-based genesis anchor. Rejected: it would have to be
   stored and agreed by the verifier anyway; an all-zero constant is the simplest
   agreed anchor and the conventional choice.

## Consequences

Positive:

- The canonical form is defined in exactly one place and pinned by a golden test,
  so the proof's foundation is both single-sourced and regression-guarded.
- Version dispatch lets the format evolve without invalidating historical events.
- Order and content are both bound into each hash, so both reordering and
  in-place edits are detectable.

Negative:

- The v1 canonical bytes are now a frozen contract: any change to them breaks
  every hash computed under v1. This is intended (it is what the golden test
  enforces), but it means format evolution must go through a new version, not an
  edit to v1.
- Floats in the payload are hashed via their JSON repr. This is deterministic on
  CPython, but a payload type whose JSON form is not stable across platforms
  would reintroduce the divergence risk; the 18c payload allowlist is what bounds
  this by restricting payload value types.

Neutral:

- The store still re-reads the file for the duplicate check and to seed the head
  at open (ADR-027 accepted the quadratic append as interim). Replacing both with
  the in-memory head as the sole mechanism, plus durable append and the
  single-writer lock, is 18b. verify_chain as a named function, the payload
  allowlist, and head anchoring are 18c.

## References

- ADR-024 (predecessor): the decision to hash-chain the trail, whose named
  canonical-serialization risk this ADR resolves.
- ADR-027: the fail-closed write policy and the chain's threat model that this
  serialization underpins. Timestamps and content inside the hash are what make
  in-place edits detectable; the threat model's rewrite-from-N and truncation
  limits are unchanged by this ADR.
