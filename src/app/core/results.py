"""Cross-context result DTOs.

Holds only DTOs that cross a bounded-context boundary and are read or wired by
the Coordinator (pipeline.py): IngestionResult and TriageResult. Context types
that never cross a boundary do not belong here; for example MaskingResult lives
in document_ingestion/entities.py (ADR-025).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .entities import EinwendungsTyp, ExtrahiertesArgument


@dataclass(frozen=True)
class IngestionResult:
    """Output of Ingestion bounded context.

    DTO for cross-context communication. Represents the ingestion pipeline
    output before passing to downstream contexts. Lives in core for
    Coordinator visibility per Bounded Context Isolation rule.

    Attributes:
        document_id: UUID assigned at ingestion time.
        clean_text: The masked text handed to downstream contexts.
        raw_document_path: Path to the stored original in the raw store.
        entity_counts: Per-type counts of PII spans masked during ingestion
            (e.g. {"NAME": 2}). Carried so the Coordinator can record them in
            the EINGANG audit event. Contains no PII (type names and integers
            only). Empty when no PII was detected.
    """

    document_id: str
    clean_text: str
    raw_document_path: str
    entity_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class TriageResult:
    """Output of Triage bounded context.

    DTO for cross-context communication. Carries the document-level
    EinwendungsTyp plus all discrete legal arguments extracted from the
    Einwendung, each classified against the predefined catalog (ADR-002,
    ADR-013). An empty extracted_arguments list is a valid result: TYP_1
    documents with no identifiable legal argument produce no entries. The
    Coordinator sets the briefing status to KEIN_TREFFER in that case.

    contradiction_detected flags the norms-present-but-no-arguments
    contradiction (S3): the deterministic extractor found norm citations
    while the LLM returned an empty argument list. The signal travels here
    so the Coordinator can record it in the TRIAGE audit payload.
    """

    einwendungs_typ: EinwendungsTyp
    extracted_arguments: list[ExtrahiertesArgument] = field(default_factory=list)
    contradiction_detected: bool = False
