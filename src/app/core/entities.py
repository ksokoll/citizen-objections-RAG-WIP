"""Domain entities (frozen dataclasses) for the objection workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class EinwendungsTyp(StrEnum):
    """Classification of an objection: informal (TYP_1) or legal (TYP_2)."""

    TYP_1 = "TYP_1"
    TYP_2 = "TYP_2"


@dataclass(frozen=True)
class ExtrahiertesArgument:
    """A single discrete legal argument extracted from an Einwendung.

    All fields are set by the Triage context. argument_verified reflects
    ADR-006 Layer 1: whether original_zitat is a substring of the masked
    source document. zitierte_normen carries the canonical norm citations
    that the Retrieval context later resolves to source Gesetzestext.
    """

    argument_id: str
    argument_text: str  # normalized for vector search
    original_zitat: str  # verbatim quote for ADR-006 Layer 1 check
    catalog_id: str | None  # predefined domain enum; None = NoMatchEvent
    einwendungs_typ: EinwendungsTyp
    zitierte_normen: list[str] = field(default_factory=list)
    argument_verified: bool = False


@dataclass(frozen=True)
class Einwendung:
    """Raw citizen objection as received at the system boundary.

    Carries the original input text and document identity. Referenced
    by einwendungs_id throughout the pipeline. Immutable after ingestion.
    PII is present here; downstream contexts receive only masked text.
    """

    einwendungs_id: str
    document_id: str
    raw_text: str
