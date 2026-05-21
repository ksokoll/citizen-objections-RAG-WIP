"""Domain entities (frozen dataclasses) for the objection workflow."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .statuses import AbwaegungsStatus, EinwendungsTyp, WuerdigungsStatus


@dataclass(frozen=True)
class Rechtsgrundlage:
    """Atomic unit of legal basis assessment.

    Represents a single legal reference extracted from the law, linked to
    the original source chunk for reproducibility and verification tracking.
    The verified flag indicates whether this reference passed automated
    verification against the retrieved corpus (ADR-006 Layer 2).
    """

    paragraph: str
    gesetz: str
    chunk_id: str
    verified: bool = False


@dataclass(frozen=True)
class RetrievalMetadata:
    """Documentation of a single RAG retrieval step.

    Captures the full trajectory of one per-argument retrieval call:
    which domain was routed to, which chunks were retrieved, and whether
    the confidence fallback to full corpus was triggered (ADR-005).
    """

    chunk_ids: list[str] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    routed_domain: str = ""
    domain_classifier_confidence: float = 0.0
    fallback_to_corpus: bool = False


@dataclass(frozen=True)
class ExtrahiertesArgument:
    """A single discrete legal argument extracted from an Einwendung.

    Lifecycle: fields in the first block are set by Triage. Fields in
    the second block are set by ResponseDrafting via dataclasses.replace().
    The frozen contract is preserved: ResponseDrafting always produces a
    new instance, never mutates the Triage output.

    argument_verified reflects ADR-006 Layer 1: whether original_zitat
    is a substring of the masked source document.
    wuerdigungs_status reflects ADR-006 Layer 2: whether all
    Rechtsgrundlagen for this argument passed §-reference verification.
    """

    # --- Set by Triage ---
    argument_id: str
    argument_text: str  # normalized for vector search
    original_zitat: str  # verbatim quote for ADR-006 Layer 1 check
    catalog_id: str | None  # predefined domain enum; None = NoMatchEvent
    einwendungs_typ: EinwendungsTyp

    # --- Set by ResponseDrafting via dataclasses.replace() ---
    argument_verified: bool = False
    retrieval_metadata: RetrievalMetadata | None = None
    rechtsgrundlagen: list[Rechtsgrundlage] = field(default_factory=list)
    rechtliche_wuerdigung: str | None = None
    wuerdigungs_status: WuerdigungsStatus = WuerdigungsStatus.KEIN_TREFFER


@dataclass(frozen=True)
class Freigabe:
    """Case worker approval record for an Abwaegungsstellungnahme.

    Immutable after creation. Set exclusively via apply_freigabe() on
    Abwaegungsstellungnahme to enforce the state machine (ADR-008).
    """

    sachbearbeiter_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    kommentar: str | None = None


@dataclass(frozen=True)
class RetrievedChunk:
    """A single document chunk returned by the RAG retriever.

    paragraph_id is the canonical form used for §-reference verification
    (e.g. baugb_§3_abs1). Score is the relevance score from the retriever,
    normalised to [0, 1] via L2-normalised inner product (ADR-003).
    """

    chunk_id: str
    paragraph_id: str
    gesetz: str
    text: str
    score: float


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


@dataclass(frozen=True)
class Abwaegungsstellungnahme:
    """Central aggregate of the objection workflow.

    Holds the per-argument results produced by Triage and ResponseDrafting,
    plus document-level fields for the final Abwägungsstellungnahme text.
    wuerdigungs_status is a computed property derived from the per-argument
    statuses: the aggregate reflects the worst-case outcome across all
    arguments (ADR-006).

    State transitions happen exclusively via apply_freigabe(), which returns
    a new APPROVED instance. Direct construction of APPROVED instances is
    blocked by __post_init__ (ADR-008).
    """

    # Required reproducibility fields (ADR-009)
    einwendungs_id: str
    einwendungs_typ: EinwendungsTyp
    model_version: str
    prompt_version: str
    retrieval_config_hash: str

    # Per-argument results (core of the argumentwise model, ADR-013)
    argumente: list[ExtrahiertesArgument] = field(default_factory=list)

    # Document-level legal content
    sachverhalt: str | None = None
    vorgebrachte_einwendung: str | None = None
    abwaegungsergebnis: str | None = None

    # State machine (ADR-008)
    status: AbwaegungsStatus = AbwaegungsStatus.DRAFT
    freigabe: Freigabe | None = None

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def wuerdigungs_status(self) -> WuerdigungsStatus:
        """Aggregate Würdigungs-Status derived from per-argument statuses.

        Returns:
            KEIN_TREFFER if no arguments are present.
            UNTERDRUECKT_UNVERIFIED if any argument has that status.
            GENERIERT if all arguments are verified and generated.
        """
        if not self.argumente:
            return WuerdigungsStatus.KEIN_TREFFER
        statuses = {a.wuerdigungs_status for a in self.argumente}
        if WuerdigungsStatus.UNTERDRUECKT_UNVERIFIED in statuses:
            return WuerdigungsStatus.UNTERDRUECKT_UNVERIFIED
        return WuerdigungsStatus.GENERIERT

    def __post_init__(self) -> None:
        """Enforce state machine invariant at construction time.

        Raises:
            ValueError: If status is APPROVED but freigabe is None.
        """
        if self.status == AbwaegungsStatus.APPROVED and self.freigabe is None:
            raise ValueError(
                "Abwaegungsstellungnahme with status=APPROVED must have freigabe "
                "set. Use apply_freigabe() instead of constructing directly."
            )

    def apply_freigabe(self, freigabe: Freigabe) -> Abwaegungsstellungnahme:
        """Transition from DRAFT to APPROVED via case worker approval.

        Returns a new instance with status APPROVED and freigabe set.
        The original instance is not modified (frozen).

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
