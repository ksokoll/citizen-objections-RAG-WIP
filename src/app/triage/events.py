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

#: Event constants this context emits, unioned into the registry at the
#: composition root.
TRIAGE_EVENTS: Final[frozenset[str]] = frozenset({TRIAGE_CONTRADICTION_DETECTED})
