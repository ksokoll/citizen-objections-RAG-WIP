# ADR-036: Endpoint Allowlist for the Triage LLM

Status: Accepted. Decision made in Round 17.2 (finding K1); extracted from
ADR-027 into its own record, because it
is a separate decision (the Triage LLM destination) from ADR-027's audit-write
failure policy and the canon is one decision per record.
Date: Round 17.2 (decision); extracted in the consolidation.
Deciders: Kevin Sokoll

## Context

The narrow PII-masking scope (ADR-025) is licensed by the assumption that the
Triage LLM is reached at an encapsulated endpoint: a destination with no outbound
network and no prompt retention, so pseudonymized text that still carries the
masking residual is not exposed to an uncontrolled third party. That assumption
was prose only. Nothing checked it, and the wired default reached the public
Mistral cloud, so the deployed reality contradicted the assumption the masking
scope rests on. An assumption that controls what may be masked has to be an
enforced fact, not a comment.

## Decision

The Triage endpoint becomes configuration (--mistral-endpoint), and the
composition root verifies the resolved endpoint against a configured allowlist
(--mistral-endpoint-allowlist) at startup, before any client is built. An off-list
endpoint is a fail-loud bootstrap abort: exit 2, a stderr line naming the resolved
endpoint, no traceback, consistent with the other startup aborts.

The default allowlist admits the public Mistral cloud so the demo runs
unconfigured; a Behoerde narrows it to its encapsulated endpoint and excludes the
rest. The admitted endpoint is recorded in the startup_config event
(mistral_endpoint), so a run's output is attributable to the destination it
actually reached. The masking-scope justification in presidio_masker is rebound
from the encapsulation prose to this checked fact.

## What it enforces, and what it does not

A control that names an objective must name what it does not cover, or the
overclaim is itself a finding.

What it enforces: which host the prompt may be sent to. A misconfiguration or a
typo that would otherwise send pseudonymized text to an unvetted destination fails
the bootstrap instead of leaking silently.

What it does not protect against:

- It constrains the destination, not the confidentiality of the prompt in transit.
  Transport security beyond the provider's own TLS is not added here.
- It verifies that the configured destination matches the declared allowlist; it
  does not prove the destination is genuinely encapsulated (no outbound network,
  no prompt retention). That remains a deployment property of the endpoint a
  Behoerde allowlists, asserted by configuration, not measured by the system. The
  allowlist raises the cost of an accidental off-box call to a fail-loud abort; it
  does not attest to what the allowlisted host then does with the prompt.
- Only the resolved endpoint string is checked. It is not a network egress
  control: it does not prevent a process with network access from reaching other
  hosts by other means, which is the operating environment's responsibility.

## Consequences

Positive: the assumption that licenses the masking scope (ADR-025) is now a
checked fact verified at startup, not prose; a run is attributable to the endpoint
it reached (startup_config); an accidental off-box destination fails loudly rather
than leaking.

Negative: the allowlist is a configuration point a Behoerde must set correctly for
its deployment; an empty or wrong allowlist aborts the run (the intended fail-loud
cost).

Neutral: the control is destination-scoping only; genuine endpoint encapsulation
and transport confidentiality remain deployment properties outside the system's
measurement, recorded here so the control is not described as proving more than it
does.

## Relationships

- Licenses ADR-025 (PII masking scope): the masking scope assumes an encapsulated
  endpoint; this ADR turns that assumption into a checked startup fact.
- Extracted from ADR-027 (audit-write failure policy), where it was recorded
  historically before the consolidation separated the two decisions.