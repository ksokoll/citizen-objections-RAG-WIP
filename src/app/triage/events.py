"""Log event vocabulary owned by the Triage context.

Each context declares the event constants it emits, rather than a central
observability registry naming foreign owners (H2). The composition root unions
these per-context declarations into the registry the logging chain enforces
against, so observability keeps the mechanism while domain vocabulary lives
with the context that emits it (ADR-026).
"""

from __future__ import annotations

from typing import Final

#: The deterministic extractor found norm citations but the LLM returned an
#: empty argument list (Triage). An internal contradiction and the observable
#: signature of a prompt-injected suppression: a document that cites norms has
#: legal substance by the prompt's own definition. Logged as the event only,
#: no fields; the document text never travels.
TRIAGE_CONTRADICTION_DETECTED: Final[str] = "triage.contradiction_detected"

#: A substantial text (length over the configured threshold) produced an empty
#: argument list (Triage). The length backstop that does not depend on citable
#: norms: a substantive prose objection without paragraph notation that the LLM
#: returns as zero arguments would otherwise ship silently as KEIN_TREFFER. The
#: sibling of the contradiction event, deterministic and explainable. Carries
#: the character length that tripped the threshold (clean_text_length, a count,
#: not PII); the document text itself never travels.
TRIAGE_SUBSTANCE_THRESHOLD: Final[str] = "triage.substance_threshold_exceeded"

#: Event constants this context emits, unioned into the registry at the
#: composition root.
TRIAGE_EVENTS: Final[frozenset[str]] = frozenset(
    {TRIAGE_CONTRADICTION_DETECTED, TRIAGE_SUBSTANCE_THRESHOLD}
)

#: Allowlisted log field names this context emits, unioned into ALLOWED_KEYS at
#: the composition root (ADR-026, default-deny). The threshold event carries the
#: character length that tripped it: a non-PII count, the explainable evidence
#: for why the empty extraction was flagged and the value an operator tunes the
#: threshold against. The document text never travels under it (contrast the
#: contradiction event, which carries no field at all). The reviewable
#: per-document flag (substance_threshold_exceeded) rides the TRIAGE audit
#: payload, not a log field, exactly as contradiction_detected does, so it is
#: not declared here.
TRIAGE_KEYS: Final[frozenset[str]] = frozenset({"clean_text_length"})
