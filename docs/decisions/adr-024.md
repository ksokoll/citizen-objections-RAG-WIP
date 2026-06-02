# ADR-024: Tamper-Evident Audit Trail via Hash Chaining

Status: Accepted
Date: 2026-06-02
Deciders: Kevin Sokoll

## Context

The AuditLog records every processing step of every objection. In a
public-administration context this trail is not just operational telemetry;
it is potential legal evidence. An authority must be able to show, after the
fact, that a given objection was processed in a given way, and that the
record of that processing has not been altered.

The current AuditLog is append-only. But append-only is only a convention:
nothing prevents a privileged actor or a compromised credential from editing
a past record. Append-only reduces casual change; it does not make change
detectable. For evidence, detectability is the property that matters.

The German public-administration standard for legally defensible long-term
preservation is BSI TR-03125 (TR-ESOR): cryptographic evidence preservation
via hashes, signatures, timestamps, and ETSI Evidence Records, with a
profiling for the federal administration (TR-ESOR-B). The question is how
much of this to implement for a learning project without over-engineering.

## Decision

Make the audit trail tamper-evident via hash chaining, not via a full
TR-ESOR archival system.

Each AuditEvent gains a hash computed over its own canonical content combined
with the previous event's hash, forming a chain. The hash function is
SHA-256. The store computes and sets the hash at append time. A verify_chain()
function walks the chain, recomputes each hash, and reports the first break.
A test guards the property: a deliberately mutated past event must break
verification.

The AuditEvent structure is laid out so the hash is one additional field, not
a structural change, so this can build on the structure introduced during the
observability work (ADR-023) without reshaping it.

## Rationale

- Hash chaining is the established technique for tamper-evident logs, the
  same primitive used by git commits and certificate-transparency logs. Any
  single-bit change to a past event changes its hash and breaks the chain for
  every subsequent event, so tampering becomes detectable even by an actor
  who can write to the store.
- SHA-256 is a standard, collision-resistant choice. The exact function
  matters less than using a vetted one rather than an ad-hoc scheme.
- The verification path is independent of the write path: re-read the chain,
  recompute, compare. This is cheap (linear) for a pipeline of this volume.
- TR-ESOR is the correct reference standard to know and name, but it targets
  long-term preservation of signed documents through a middleware with a
  crypto module and Evidence Records. Implementing it would be
  over-engineering for a demo; referencing its principles (integrity via
  hashes, unchangeable metadata linkage for reconstruction) is the right
  level.
- Hash chaining keeps the trail self-contained: no external service, no
  signing infrastructure, no broker. It is enforceable in the store itself.

## Alternatives Considered

1. Append-only only (no hashing). Rejected: it is a convention, not a
   security property, and provides no tamper detection.
2. Hash chaining (chosen). Detects any alteration, simple, self-contained,
   linear verification cost.
3. Merkle trees. More efficient batch verification for very large logs, at
   the cost of more machinery. Rejected as unnecessary at this volume; the
   linear cost of a plain chain is negligible here.
4. External anchoring (RFC-3161 timestamps or a ledger). Defends against an
   attacker who controls both the log and the verification keys. Rejected as
   out of scope for a demo, though noted as the next step if insider-tamper
   resistance were required.
5. Full BSI TR-03125 / TR-ESOR middleware. The mandated standard for
   federal long-term preservation. Rejected as far beyond a learning
   project's scope; referenced as the production-grade target.

## Consequences

Positive:
- The trail is tamper-evident: alteration of any past event is detectable.
- Legal defensibility is materially stronger than append-only alone, and the
  integrity claim is mathematically verifiable.
- The approach is self-contained and testable in isolation.

Negative:
- The hash must be computed over a canonical serialization of each event; the
  canonical form must be stable, or verification produces false breaks. This
  is the main implementation risk and is covered by the guarding test.
- Hash chaining detects tampering but does not prevent it, and does not
  defend against an attacker who can rewrite the whole chain consistently.
  External anchoring would be required for that and is deferred.

Neutral:
- created_at on the records (added during ADR-023 reproducibility work)
  supports retention reasoning; the concrete statutory retention periods are
  sector administrative law and are documented rather than implemented.