# ADR-028: Briefing is the delivery contract, presentation is out of scope

Status: Accepted
Date: 2026-06-12

## Context

The pipeline produces WuerdigungsBriefing objects as its final output. A
consuming frontend, outside this repository, renders them for the
Sachbearbeiter. Until now this boundary was implicit: the briefing looked like
an internal domain object, and nothing stated who is responsible for
presentation, access control at display time, or the stability of the
briefing's shape.

Two Round 16 changes force the boundary into the open. The provenance fields
(corpus_id, created_at, ADR pending in Round 16) only serve their purpose if
the consumer can read them, which means they must be part of the delivered
object, not of the telemetry. And the new CLI needs a defined output format
for `process`, which raises the question whether the backend formats
human-readable text.

## Decision

The system boundary is the WuerdigungsBriefing. The pipeline delivers
structured domain objects; everything human-readable happens beyond the
boundary, in the frontend.

1. **The briefing's fields are a public interface.** Adding, removing, or
   re-typing a field is a contract change for every consumer, not an internal
   refactor. Changes are made deliberately and recorded.
2. **The serialization is part of the contract.** Briefings are delivered as
   JSON; datetimes are ISO-8601 UTC. The serialized form is what the consumer
   parses, so its stability matters as much as the field set.
3. **No presentation logic in the backend.** The CLI `process` command emits
   the serialized briefing and nothing prettier. A second rendering path in
   the backend would inevitably drift from the frontend's; there is exactly
   one place that formats for humans, and it is not this repository.
4. **Provenance travels inside the artifact.** corpus_id and created_at are
   briefing fields precisely because rendering happens elsewhere: a frontend
   can only display what the contract carries. Logs cannot serve this purpose;
   they are retention-bound (ADR-026) while the briefing outlives them.

## Trust boundary assumption

The briefing is pseudonymous but substantive: it carries weighed arguments and
norm references, so whoever renders it sees case content. Delivery is assumed
to be to an authenticated consumer; authorization, display-time access
control, and any further data handling are the frontend's responsibility and
are enforced beyond the system boundary. This is an assumed property, not an
enforced one, recorded here in the same spirit as the threat-model
delimitations in ADR-027.

## Contract changes (Round 16.1)

Per decision point 1, contract changes are recorded here.

- **argument_verified enters the contract (S2).** BriefingEntry carries the
  deterministic verification verdict (ADR-006 Layer 1), and the status
  derivation gains ZITAT_NICHT_VERIFIZIERT: an argument whose quote failed
  the substring check is never BRIEFING_READY, regardless of catalog match
  or norm resolution. Before this change the verdict was computed and then
  dropped at the Coordinator's mapping seam, so a potentially fabricated
  quote shipped as ready. Consumers gain one boolean field and one status
  value; no existing field changed shape.
- **Serialization lives in the Briefing context.** The contract's JSON form
  (ISO-8601 UTC, ensure_ascii=False) is produced by
  briefing/serialization.py (to_json); the CLI delegates (H1). A transport
  adapter owning its own copy of the contract serialization could drift from
  the contract silently; the context that owns the fields owns their form.
- **Mapping-seam test.** The Coordinator's _map_arguments field list is
  pinned against BriefingEntry, so a field dropped at the seam (the S2
  failure shape) fails a test instead of vanishing from the contract.

## Contract changes (Round 17.1)

Per decision point 1, contract changes are recorded here.

- **The retriever provenance term generalizes to source_revision (M2).** The
  Retriever protocol moved to retrieval/protocols.py (it carries
  retrieval-specific semantics, so it is context-owned, K1) and its provenance
  member was renamed from corpus_id to the neutral source_revision: str at the
  contract level. The reason is that corpus_id cemented a corpus-hash semantics
  a future non-corpus retriever (a database snapshot, an API revision) could
  not honor. The value semantics are unchanged: the current corpus-based
  NormRetrievalService returns its SHA-256 corpus hash as its source_revision,
  computed exactly as before. The briefing field that carries provenance to the
  consumer keeps its name corpus_id (decision point 4), as does the
  startup_config corpus_id field (ADR-026); only the protocol term generalized.
  This is a producer-internal contract change: the briefing JSON the consumer
  parses is byte-for-byte the same.

## Consequence: the renderer violation and its resolution (Round 16.1)

briefing/renderer.py was a Markdown renderer for the Sachbearbeiter inside
the backend: a pre-existing violation of decision point 3 that survived this
ADR's ratification because no inventory of presentation code was taken at
the time. Resolution per the decision rule: referenced by nothing under
scripts/ or eval tooling, so the renderer and its test suite were deleted
(M3). Process lesson, recorded for future boundary ADRs: when ratifying a
boundary, grep the codebase for existing violations and list them as known
debts in the ADR instead of discovering them in review.

## Consequences

- Consumers can rely on the briefing being self-describing: result plus
  provenance in one object, independent of any log or side channel.
- The backend stays free of formatting concerns; review can reject
  presentation logic in src/app by pointing at this ADR.
- Field evolution becomes visible work. A schema_version field on the
  briefing (analogous to serialization_version on AuditEvent) is the expected
  next step; decided in Round C when the briefing fields are touched for the
  chain work, leaning toward yes for the same reason as there: a consumer
  must know which shape it is reading.
- Tests may assert the serialized shape (golden test on the briefing JSON) so
  contract changes surface in diffs; candidate for Round C alongside
  schema_version.

## Alternatives considered

Rendering human-readable output in the CLI was rejected: it creates a second
presentation path that drifts from the frontend and pulls formatting concerns
across the boundary. Keeping provenance only in logs was rejected: the logs
are deleted after the retention period while briefings persist, and the
consumer cannot read the telemetry anyway.

## Notes on field level trust within the contract

Not all briefing fields carry the same provenance. Fields derived from the static statute corpus (norm references and their resolved text) are trustworthy. Fields generated by the LLM from citizen-submitted document content are citizen-influenced and untrusted: specifically argument_text
and original_zitat. The argument_verified flag covers only whether
original_zitat occurs verbatim in the source; it makes no statement about
argument_text. A consumer must treat these untrusted fields as such and
context-escape them at render time (HTML, Markdown, or otherwise). The
backend delivers syntactically safe JSON and deliberately does not sanitize
these values, because escaping is presentation logic and belongs beyond the
system boundary per this ADR. This concretizes the general trust-boundary
assumption above to the field level.
The triage prompt fences citizen text with delimiters and a precedence rule,
but this is a soft constraint: the delimiters are code-resident and
therefore predictable, so a crafted document can forge the fence boundary.
The deterministic checks downstream (contradiction detection, verbatim quote
verification, schema validation of catalog ids) are the hard constraints
that bound the impact; the residual lever is free-text classification, which
triggers no automated action. Hardening the fence with a per-request nonce
delimiter is a documented backlog item, deferred because the demo has no
rendering consumer and the deterministic layers carry the load.

## Input-path hardening notes (Round 17.2)

The security review found three input-path issues that reach the domain
function, not the frontend boundary; two of them are frontend-boundary findings
recorded here as text plus backlog rather than code, per the demo scope.

- **Fence-token neutralization closes trivial forgery (H1, code).** The
  delimiters stay code-resident and predictable, but TriageService now rewrites
  any literal fence token planted in the citizen text to a non-fence form before
  interpolation (triage/prompts.py neutralize_fence_markers). A citizen who
  writes the exact end marker can therefore no longer forge a boundary and have
  the text after it read as instructions outside the fence. This closes the
  trivial case only; case and whitespace variants are deliberately not chased
  here, because the fence remains a soft constraint and chasing variants in a
  code-resident scheme is scheinpräzision. The robust fix, a per-request nonce
  delimiter (and the separate-user-message prompt structure), stays backlog.
  Trigger: the fence becomes load-bearing rather than orienting, e.g. a
  non-encapsulated deployment where the deterministic downstream layers no
  longer carry the load.
- **argument_text faithfulness is a feature, not a fix (M1, production list).**
  The field-level trust note above already classifies argument_text as
  citizen-influenced and untrusted: the consumer context-escapes it and the
  backend does not sanitize it. What it does not do is verify that argument_text
  faithfully summarizes the source (the way argument_verified verifies
  original_zitat is a verbatim substring). An unfaithful summary is a content
  risk the verbatim check does not cover. A faithfulness check is added work, a
  feature on the production list, not a fix in this round; recorded so the gap
  is named rather than assumed away. Trigger: a deployment that acts on
  argument_text automatically, rather than presenting it to a Sachbearbeiter who
  reads the original alongside it.

## Round 19 note: the fence's containment rests on the verification layer

The triage fence is a soft constraint, acceptable only because the
deterministic downstream layers carry the load: contradiction detection, schema
validation of catalog ids, and the verbatim quote check (ADR-006 Layer 1) are
the hard constraints that bound a forged fence. That argument assumes those
layers actually hold. The whole-system security review found one place where the
verbatim quote check did not: an empty or whitespace-only original_zitat was
marked verified, because str.find("") returns 0 (ADR-006 Layer 1 robustness
note). With that bypass open, the fence's soft-constraint character rested on a
check that passed the cleanest fabrication case.

The fix closes the bypass at both the verification site and the schema edge,
restoring the containment the fence delegates to. This note records the
dependency: fence containment is only as complete as the verification layer it
relies on, and that layer is now sound on the empty-quote case. The per-request
nonce delimiter remains the named backlog for hardening the fence itself; a
partial literal widening is deliberately not done, because it would suggest a
false security the soft fence does not provide.