"""Bounded context output DTOs for cross-context communication."""

from __future__ import annotations

from dataclasses import dataclass, field

from .entities import ExtrahiertesArgument


@dataclass(frozen=True)
class IngestionResult:
    """Output of Ingestion bounded context.

    DTO for cross-context communication. Represents the ingestion pipeline
    output before passing to downstream contexts. Lives in core for
    Coordinator visibility per Bounded Context Isolation rule.
    """

    document_id: str
    clean_text: str
    raw_document_path: str


@dataclass(frozen=True)
class TriageResult:
    """Output of Triage bounded context.

    DTO for cross-context communication. Carries the document-level
    EinwendungsTyp plus all discrete legal arguments extracted from the
    Einwendung, each classified against the predefined catalog (ADR-002,
    ADR-013). An empty extracted_arguments list is a valid result: TYP_1
    documents with no identifiable legal argument produce no entries. The
    Coordinator sets wuerdigungs_status=KEIN_TREFFER in that case.
    """

    extracted_arguments: list[ExtrahiertesArgument] = field(default_factory=list)
