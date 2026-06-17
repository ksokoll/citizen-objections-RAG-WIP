from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Naming convention: German for domain events, English for code identifiers

#: einwendungs_id carried by a process-wide chain event that is not tied to a
#: citizen objection: the startup configuration event (ADR-031). A fixed
#: non-objection sentinel so such a custody record satisfies the required
#: non-empty id without claiming an Einwendung. (The store's recovery event also
#: carried this sentinel until Round 21 rolled the quarantine recovery back to a
#: loud failure at open, leaving STARTKONFIGURATION as the sole holder.)
SYSTEM_EINWENDUNGS_ID: Final[str] = "SYSTEM"


class AuditEventType(StrEnum):
    """Classification of an audit event.

    Most members name a pipeline stage. Two are not pipeline steps:
    STARTKONFIGURATION, recorded at process start to prove the active controls
    after the fact (ADR-031); and ROHDOKUMENT_ZUGRIFF, recorded when the
    show-document path reads a stored raw document (unmasked PII) back out
    (ADR-033). They live here because AuditEvent.event_type is typed against this
    enum and each is a custody record like any other. (A WIEDERHERSTELLUNG
    recovery event existed until Round 21 rolled the quarantine recovery back to
    a loud failure at open; the member was removed with it.)

    The sentinel differs by what the record is tied to. STARTKONFIGURATION is
    process-wide, tied to no objection, so it carries the SYSTEM_EINWENDUNGS_ID
    sentinel. ROHDOKUMENT_ZUGRIFF is not: a raw-document read is access to one
    specific objection's PII, so it carries that document's id as its
    einwendungs_id (the natural correlation), not the SYSTEM sentinel. A query
    for everything touching one objection then finds its read accesses alongside
    its pipeline events (ADR-033).

    Why this is a central enum while the structured-log event vocabulary is
    per-context (M1, Round 20). The asymmetry is deliberate, not an oversight.
    The audit event types are a closed contract between exactly two parties: the
    Coordinator that emits and the store that persists, and AuditEvent.event_type
    is typed against this one enum, so both parties must name the same fixed set.
    The log vocabulary is the opposite: open and growing, each context declaring
    the events it emits, so by the coupling-hub rule it is owned per context and
    unioned at the composition root (observability_registry, H2). A closed
    two-party contract belongs in one shared type; an open per-context-growing
    set belongs with its owners. The previously undocumented inconsistency
    between the two was the finding; this declaration closes it.
    """

    EINGANG = "eingang"
    TRIAGE = "triage"
    RETRIEVAL = "retrieval"
    BRIEFING_ERSTELLT = "briefing_erstellt"
    ENTWURF_UNTERDRUECKT = "entwurf_unterdrueckt"
    KEIN_TREFFER = "kein_treffer"
    FREIGABE = "freigabe"
    PIPELINE_FEHLER = "pipeline_fehler"
    STARTKONFIGURATION = "startkonfiguration"
    ROHDOKUMENT_ZUGRIFF = "rohdokument_zugriff"


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    """Append-only audit event for the objection workflow.

    All state changes in the objection workflow must emit an AuditEvent.
    Events are immutable and form a complete chain of custody for
    compliance and reproducibility. No event may be deleted; only new
    events are added (append-only semantics).

    The payload dict is context-specific and may contain arbitrary metadata
    (e.g., confidence scores, intermediate results, error details).
    """
    event_id: str = Field(..., description="UUID of event, unique across system")
    event_type: AuditEventType = Field(..., description="Type of audit event")
    einwendungs_id: str = Field(..., description="Reference to objection statement")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of event (UTC)",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Context-specific metadata (counts, scores, flags, ids). The kernel "
            "carries the payload without constraining its shape; the audit "
            "context governs what each event type may carry and enforces it at "
            "write entry, so the chain stays content-free (Form B, ADR-032). The "
            "kernel does not validate the payload at construction: the gate is "
            "the store's write entry, not the model, and the read path is "
            "deliberately tolerant so a historical line never fails an open."
        ),
    )
    serialization_version: int = Field(
        default=1,
        description=(
            "Version of the canonical serialization used for the hash chain. "
            "Laid out now (Round A); the chain that depends on it is populated "
            "in Round C (ADR-024). Versioning lets verify_chain() select the "
            "canonicalization per event so a later field addition does not "
            "invalidate historical events."
        ),
    )
    sequence_number: int | None = Field(
        default=None,
        description=(
            "Monotonic position in the hash chain, starting at 0 for the genesis "
            "event. Part of the canonical bytes (ADR-024), so reordering events "
            "changes their hashes. Assigned by the store on append from its "
            "in-memory head; None until then, like event_hash."
        ),
    )
    event_hash: str | None = Field(
        default=None,
        description=(
            "SHA-256 over the canonical content plus the predecessor hash. "
            "None until Round C computes the chain (ADR-024); None is honest "
            "for events written before the chain exists, not a placeholder to "
            "be masked."
        ),
    )

    @field_validator("event_id", "einwendungs_id", mode="before")
    @classmethod
    def validate_non_empty_ids(cls, v: str) -> str:
        """Enforce non-empty string IDs.

        Args:
            v: ID value to validate.

        Returns:
            Validated ID.

        Raises:
            ValueError: If ID is empty.
        """
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()
