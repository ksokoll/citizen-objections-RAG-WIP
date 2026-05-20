from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal


# Enums
class WuerdigungsStatus(StrEnum):
    """Status of legal basis assessment (Würdigung)."""

    GENERIERT = "generiert"
    UNTERDRUECKT_UNVERIFIED = "unterdrueckt_unverified"
    NO_MATCH = "no_match"


class AbwaegungsStatus(StrEnum):
    """Status of objection statement in approval workflow."""

    DRAFT = "draft"
    APPROVED = "approved"


class EinwendungsTyp(StrEnum):
    """Classification of objection type."""

    TYP_1 = "typ_1"
    TYP_2 = "typ_2"


# Core Models
@dataclass(frozen=True)
class Rechtsgrundlage:
    """Atomic unit of legal basis assessment.

    Represents a single legal reference extracted from the law, linked to
    the original source chunk for reproducibility and verification tracking.
    The verified flag indicates whether this reference passed automated
    verification (e.g., paragraph existence check against law database).
    """

    paragraph: str
    gesetz: str
    chunk_id: str
    verified: bool = False


@dataclass(frozen=True)
class CatalogMatch:
    """Result of triage matching against catalog.

    Tracks which catalog entry was matched and via which method.
    match_stage indicates whether embedding similarity or LLM fallback
    was used to produce the match, which is critical for reproducibility.
    """

    catalog_id: str
    beschreibung: str
    konfidenz_score: float
    match_stage: Literal["embedding", "llm_fallback"]


@dataclass(frozen=True)
class RetrievalMetadata:
    """Documentation of RAG retrieval step.

    Captures the full trajectory of the retrieval process:
    - Which domain classifier routed the query
    - Which chunks were retrieved and their scores
    - Whether fallback to full corpus occurred

    This enables auditing retrieval decisions and debugging ranking failures.
    """

    chunk_ids: list[str] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    routed_domain: str = ""
    domain_classifier_confidence: float = 0.0
    fallback_to_corpus: bool = False


@dataclass(frozen=True)
class Freigabe:
    """Case worker approval for objection statement."""

    sachbearbeiter_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    kommentar: str | None = None


@dataclass(frozen=True)
class Abwaegungsstellungnahme:
    """Core model: objection statement with state machine.

    This is the central aggregate for the entire objection workflow. It tracks:
    - The original user input (for reproducibility)
    - Assessment against legal bases (Rechtsgrundlagen)
    - Triage classification and catalog matching
    - Retrieval metadata (for debugging and auditing)
    - State transitions via apply_freigabe() (state machine enforced)

    Immutable: all state transitions via apply_freigabe(), which returns a new
    instance. The original instance is never modified.
    """

    # Identification
    einwendungs_id: str
    einwendungs_typ: EinwendungsTyp

    # Assessment
    wuerdigungs_status: WuerdigungsStatus | None = None
    rechtsgrundlagen: list[Rechtsgrundlage] = field(default_factory=list)

    # Triage & Catalog
    catalog_match: CatalogMatch | None = None
    extracted_arguments: list[str] = field(default_factory=list)
    triage_confidence: float = 0.0

    # Retrieval
    retrieval_metadata: RetrievalMetadata | None = None

    # Reproducibility (non-negotiable per ADR-011: enables audit trail)
    raw_user_input: str = ""
    transformation_chain: list[str] = field(default_factory=list)
    model_version: str = ""
    prompt_version: str = ""
    retrieval_config_hash: str = ""

    # Legal Content (output of the system; the actual
    # Abwaegungsstellungnahme text)
    sachverhalt: str | None = None
    vorgebrachte_einwendung: str | None = None
    rechtliche_wuerdigung: str | None = None
    abwaegungsergebnis: str | None = None

    # State Machine & Approval
    status: AbwaegungsStatus = AbwaegungsStatus.DRAFT
    freigabe: Freigabe | None = None

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def apply_freigabe(self, freigabe: Freigabe) -> Abwaegungsstellungnahme:
        """Transition to APPROVED status via case worker approval.

        Returns a new Abwaegungsstellungnahme instance with status APPROVED
        and freigabe set. The original instance is not modified (frozen).

        Args:
            freigabe: The Freigabe record created by the Sachbearbeiter.

        Returns:
            New Abwaegungsstellungnahme instance with status APPROVED.

        Raises:
            ValueError: If status is not DRAFT (state machine violation).
        """
        if self.status != AbwaegungsStatus.DRAFT:
            raise ValueError(
                f"Cannot apply_freigabe when status={self.status.value}. "
                f"Only allowed when status={AbwaegungsStatus.DRAFT.value}"
            )
        return dataclasses.replace(
            self,
            status=AbwaegungsStatus.APPROVED,
            freigabe=freigabe,
            updated_at=datetime.now(UTC),
        )


# Output Models for BC Boundaries
@dataclass(frozen=True)
class IngestionResult:
    """Output of Ingestion bounded context.

    DTO for cross-context communication. Represents the ingestion pipeline
    output before passing to downstream contexts. Lives in core/data_structures.py
    for Coordinator visibility per Bounded Context Isolation rule.
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


# Retrieval Value Object
@dataclass(frozen=True)
class RetrievedChunk:
    """Retrieved document chunk from the RAG retriever.

    Represents a single chunk returned by the retriever ranked by relevance
    to a query embedding. The paragraph_id is the canonical form used for
    auditing and legal reference (e.g., baugb_§3_abs1). Score is the
    relevance score from the retriever (0-1, higher = more relevant).
    """

    chunk_id: str
    paragraph_id: str
    gesetz: str
    text: str
    score: float
