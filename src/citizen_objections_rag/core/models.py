from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field, field_validator


# Enums
class WuerdigungsStatus(str, Enum):
    """Status of legal basis assessment (Würdigung)."""
    GENERIERT = "generiert"
    UNTERDRUECKT_UNVERIFIED = "unterdrueckt_unverified"
    NO_MATCH = "no_match"


class AbwaegungsStatus(str, Enum):
    """Status of objection statement in approval workflow."""
    DRAFT = "draft"
    APPROVED = "approved"


class EinwendungsTyp(str, Enum):
    """Classification of objection type."""
    TYP_1 = "typ_1"
    TYP_2 = "typ_2"


# Core Models
class Rechtsgrundlage(BaseModel):
    """Atomic unit of legal basis assessment.

    Represents a single legal reference extracted from the law, linked to
    the original source chunk for reproducibility and verification tracking.
    The verified flag indicates whether this reference passed automated
    verification (e.g., paragraph existence check against law database).
    """
    paragraph: str = Field(..., description="Paragraph reference")
    gesetz: str = Field(..., description="Law name or number")
    chunk_id: str = Field(..., description="Back-reference to source chunk in retrieval corpus")
    verified: bool = Field(default=False, description="Verified by verification logic")

    @field_validator("paragraph", "gesetz", "chunk_id", mode="before")
    @classmethod
    def validate_non_empty_strings(cls, v: str) -> str:
        """Enforce non-empty strings.

        Args:
            v: String value to validate.

        Returns:
            Validated string.

        Raises:
            ValueError: If string is empty.
        """
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()


class CatalogMatch(BaseModel):
    """Result of triage matching against catalog.

    Tracks which catalog entry was matched and via which method.
    match_stage indicates whether embedding similarity or LLM fallback
    was used to produce the match, which is critical for reproducibility.
    """
    catalog_id: str = Field(..., description="ID of matched catalog entry")
    beschreibung: str = Field(..., description="Description of the match")
    konfidenz_score: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0-1")
    match_stage: Literal["embedding", "llm_fallback"] = Field(
        ..., description="Matching method: embedding or LLM fallback"
    )


class RetrievalMetadata(BaseModel):
    """Documentation of RAG retrieval step.

    Captures the full trajectory of the retrieval process:
    - Which domain classifier routed the query
    - Which chunks were retrieved and their scores
    - Whether fallback to full corpus occurred

    This enables auditing retrieval decisions and debugging ranking failures.
    """
    chunk_ids: list[str] = Field(default_factory=list, description="List of retrieved chunk IDs")
    scores: list[float] = Field(default_factory=list, description="Relevance scores per chunk")
    routed_domain: str = Field(..., description="Domain the request was routed to")
    domain_classifier_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence of domain classifier"
    )
    fallback_to_corpus: bool = Field(
        default=False, description="Whether fallback to full corpus occurred"
    )


class Freigabe(BaseModel):
    """Case worker approval for objection statement."""
    sachbearbeiter_id: str = Field(..., description="ID of approving case worker")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of approval")
    kommentar: str | None = Field(default=None, description="Optional comment")


class Abwaegungsstellungnahme(BaseModel):
    """Core model: objection statement with state machine.

    This is the central aggregate for the entire objection workflow. It tracks:
    - The original user input (for reproducibility)
    - Assessment against legal bases (Rechtsgrundlagen)
    - Triage classification and catalog matching
    - Retrieval metadata (for debugging and auditing)
    - State transitions via apply_freigabe() (state machine enforced)

    Immutants: raw_user_input, model_version, created_at are never modified.
    Mutable: status and freigabe only change via apply_freigabe().
    """
    # Identification
    einwendungs_id: str = Field(..., description="Unique ID of objection statement")
    einwendungs_typ: EinwendungsTyp = Field(..., description="Objection type classification")

    # Assessment
    wuerdigungs_status: WuerdigungsStatus = Field(
        default=None, description="Status of legal basis assessment (set during verification)"
    )
    rechtsgrundlagen: list[Rechtsgrundlage] = Field(
        default_factory=list, description="Collection of atomic legal bases"
    )

    # Triage & Catalog
    catalog_match: CatalogMatch | None = Field(
        default=None, description="Catalog match if available"
    )
    extracted_arguments: list[str] = Field(
        default_factory=list, description="Arguments extracted from objection"
    )
    triage_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Confidence of triage classification"
    )

    # Retrieval
    retrieval_metadata: RetrievalMetadata | None = Field(
        default=None, description="Metadata of retrieval process"
    )

    # Reproducibility (non-negotiable per ADR-011: enables audit trail)
    raw_user_input: str = Field(..., description="Original user objection")
    transformation_chain: list[str] = Field(
        default_factory=list, description="Processing steps for reproducibility"
    )
    model_version: str = Field(..., description="Version of LLM model used")
    prompt_version: str = Field(..., description="Version of system prompt and instructions used")
    retrieval_config_hash: str = Field(..., description="Hash of retrieval config (domain routing, top_k, filters)")

    # Legal Content (output of the system; the actual Abwaegungsstellungnahme text)
    sachverhalt: str | None = Field(
        default=None, description="Factual representation of the case (extracted from raw_user_input)"
    )
    vorgebrachte_einwendung: str | None = Field(
        default=None, description="The objection as raised by the user"
    )
    rechtliche_wuerdigung: str | None = Field(
        default=None, description="Legal assessment against applicable law"
    )
    abwaegungsergebnis: str | None = Field(
        default=None, description="Final decision and reasoning (filled by approval workflow)"
    )

    # State Machine & Approval
    status: AbwaegungsStatus = Field(default=AbwaegungsStatus.DRAFT)
    freigabe: Freigabe | None = Field(default=None, description="Case worker approval")

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("raw_user_input", "model_version", "prompt_version", "retrieval_config_hash", mode="before")
    @classmethod
    def validate_required_strings(cls, v: str) -> str:
        """Enforce non-empty strings for reproducibility fields.

        Args:
            v: String value to validate.

        Returns:
            Validated string.

        Raises:
            ValueError: If string is empty.
        """
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    def apply_freigabe(self, sachbearbeiter_id: str, kommentar: str | None = None) -> None:
        """Transition to APPROVED status via case worker approval.

        This is the only public method that moves the state from DRAFT to APPROVED.
        It creates an immutable Freigabe record with timestamp, ensuring approval
        is traceable and auditable. Enforces state machine: can only be called when
        status == DRAFT per ADR-008.

        Args:
            sachbearbeiter_id: ID of the case worker performing the approval.
            kommentar: Optional comment explaining the approval decision.

        Returns:
            None. Modifies object state in place (status, freigabe, updated_at).

        Raises:
            ValueError: If status is not DRAFT (state machine violation).
        """
        if self.status != AbwaegungsStatus.DRAFT:
            raise ValueError(
                f"Cannot apply_freigabe when status={self.status.value}. "
                f"Only allowed when status={AbwaegungsStatus.DRAFT.value}"
            )
        self.freigabe = Freigabe(
            sachbearbeiter_id=sachbearbeiter_id,
            kommentar=kommentar
        )
        self.status = AbwaegungsStatus.APPROVED
        self.updated_at = datetime.now(timezone.utc)


# Output Models for BC Boundaries
class IngestionResult(BaseModel):
    """Output of Ingestion bounded context.

    DTO for cross-context communication. Represents the ingestion pipeline output
    before passing to downstream contexts. Lives in core/models.py for Coordinator
    visibility, not in ingestion/domain/ to avoid circular imports.
    """
    document_id: str = Field(..., description="ID of ingested document")
    clean_text: str = Field(..., description="Cleaned document text")
    raw_document_path: str = Field(..., description="Path to original document")


class TriageResult(BaseModel):
    """Output of Triage bounded context.

    DTO for cross-context communication. Represents the triage pipeline output.
    Lives in core/models.py for Coordinator visibility per Bounded Context Isolation rule.
    No match is a valid result per ADR-002; catalog_match can be None.
    """
    catalog_match: CatalogMatch | None = Field(
        default=None, description="Result of catalog matching, or None if no match found"
    )
    einwendungs_typ: EinwendungsTyp = Field(..., description="Classified objection type")
    extracted_arguments: list[str] = Field(..., description="Extracted arguments")
    triage_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence of triage classification"
    )
