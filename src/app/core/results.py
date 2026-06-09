# core.results.py - Data classes representing results from core processing stages.

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
    """

    einwendungs_typ: EinwendungsTyp
    extracted_arguments: list[ExtrahiertesArgument] = field(default_factory=list)


@dataclass(frozen=True)
class MaskingResult:
    """Output of the PII masking step in DocumentIngestion.

    DTO carrying the masked text plus per-type counts of how many spans of
    each entity type were masked. The counts feed the masking Fitness
    Function and the audit record; they contain no PII themselves (only type
    names and integers). An empty entity_counts dict is a valid result: text
    with no detectable PII produces no masked spans.

    Attributes:
        text: The masked text, with detected PII spans replaced by speaking
            German type placeholders ([NAME], [TELEFON], [EMAIL], [IBAN]).
        entity_counts: Mapping of entity type to the number of spans masked
            for that type (e.g. {"NAME": 2, "TELEFON": 1}).
    """

    text: str
    entity_counts: dict[str, int] = field(default_factory=dict)
