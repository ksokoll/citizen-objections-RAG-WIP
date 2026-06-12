"""Domain entities for the Briefing bounded context.

The Briefing context assembles a per-argument briefing for the Sachbearbeiter.
It does not generate a finished Würdigung: the case-specific facts of the
building project (the Akte) are outside the system boundary, so a binding
legal assessment cannot be produced here. Instead each argument is paired
with its resolved norm text and presented as a structured briefing entry,
ready for the Sachbearbeiter to perform the actual Abwägung against the
case file.

This is a deliberate scope decision (ADR-022): no LLM generation, no
hallucination surface, fully deterministic and auditable output. The
limitation (missing case context) is represented explicitly in the data
model, not only in rendered prose.

Pure domain: no I/O, no external dependencies beyond the standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class BriefingStatus(StrEnum):
    """Per-argument status in the briefing.

    Values:
        BRIEFING_READY: The argument has a catalog match and at least one
            resolved norm with source text. Ready for the Sachbearbeiter
            to assess against the case file.
        NORM_UNRESOLVED: The argument has a catalog match but one or more
            of its cited norms could not be resolved to source text.
            The entry is included with the unresolved norms flagged.
        KEIN_TREFFER: The argument had no catalog match (catalog_id was
            None). It was skipped for norm resolution and carries no
            source text.
    """

    BRIEFING_READY = "BRIEFING_READY"
    NORM_UNRESOLVED = "NORM_UNRESOLVED"
    KEIN_TREFFER = "KEIN_TREFFER"


@dataclass(frozen=True)
class ResolvedNormEntry:
    """One cited norm paired with its resolved source text.

    Mirrors the relevant fields of the Retrieval context's NormWithSource
    in the Briefing domain, so this context does not import the
    Retrieval domain model directly. The Coordinator maps across the
    boundary.

    Attributes:
        canonical_citation: The citation as extracted by Triage.
        paragraph_key: The resolved paragraph-level key, empty if
            unresolved.
        source_text: The Gesetzestext of the resolved paragraph, empty
            if unresolved.
        resolved: True if source text is present.
    """

    canonical_citation: str
    paragraph_key: str
    source_text: str
    resolved: bool


@dataclass(frozen=True)
class BriefingEntry:
    """A single argument's briefing block for the Sachbearbeiter.

    Carries the citizen's argument, its classification, the resolved
    norms with their source text, and the status. The limitation that a
    case-file-grounded assessment cannot be produced here is structural:
    requires_case_context is True for every entry that reaches
    BRIEFING_READY, signalling that the Sachbearbeiter must still perform
    the Abwägung against the Akte.

    Attributes:
        argument_id: The Triage argument identifier.
        argument_text: The normalised argument text.
        original_zitat: The verbatim citizen quote.
        einwendungs_typ: TYP_1 or TYP_2, as classified by Triage.
        catalog_id: The matched catalog entry, None if no match.
        norms: The cited norms with their resolved source text.
        status: The per-argument briefing status.
        requires_case_context: True when a case-file-grounded assessment
            is still required (always True for a ready briefing entry).
    """

    argument_id: str
    argument_text: str
    original_zitat: str
    einwendungs_typ: str
    catalog_id: str | None
    norms: list[ResolvedNormEntry]
    status: BriefingStatus
    requires_case_context: bool


@dataclass(frozen=True)
class WuerdigungsBriefing:
    """The assembled briefing for one citizen objection document.

    Aggregates the per-argument entries plus a document-level limitation
    note. The limitation is recorded both as structured data (on each
    entry via requires_case_context) and as a human-readable note here,
    so it is auditable and visible.

    The briefing is the system's delivery contract (ADR-028): its fields
    are a public interface for the consuming frontend, and provenance
    travels inside the artifact because rendering happens beyond the
    boundary and logs are retention-bound.

    Attributes:
        document_id: The ingestion-assigned document identifier.
        einwendungs_typ: The document-level classification from Triage.
        corpus_id: Content-based identifier of the statute corpus the
            briefing was resolved against (ADR-028, provenance).
        created_at: Creation time of the briefing, timezone-aware UTC.
        entries: One BriefingEntry per extracted argument.
        limitation_note: Human-readable statement of the scope boundary
            (no case-file-grounded assessment produced here).
    """

    document_id: str
    einwendungs_typ: str
    corpus_id: str
    created_at: datetime
    entries: list[BriefingEntry] = field(default_factory=list)
    limitation_note: str = (
        "Dieses Briefing ordnet jedem Argument die einschlägige Norm und "
        "deren Gesetzestext zu. Die fallbezogene Abwägung gegen die "
        "Projektakte (Planunterlagen, Gutachten, Festsetzungen) ist nicht "
        "Teil dieses Systems und muss durch die Sachbearbeitung erfolgen."
    )
