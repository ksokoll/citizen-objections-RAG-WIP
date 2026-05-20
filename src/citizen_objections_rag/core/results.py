"""Bounded context output DTOs for cross-context communication."""

from __future__ import annotations

from dataclasses import dataclass

from .entities import CatalogMatch
from .statuses import EinwendungsTyp


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

    DTO for cross-context communication. Triage pipeline output for Coordinator
    visibility per Bounded Context Isolation rule. No match is a valid result
    per ADR-002; catalog_match can be None.
    """

    catalog_match: CatalogMatch | None
    einwendungs_typ: EinwendungsTyp
    extracted_arguments: list[str]
    triage_confidence: float
