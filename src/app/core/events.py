from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Naming convention: German for domain events, English for code identifiers


class AuditEventType(StrEnum):
    """Classification of audit event by pipeline stage."""

    EINGANG = "eingang"
    TRIAGE = "triage"
    RETRIEVAL = "retrieval"
    BRIEFING_ERSTELLT = "briefing_erstellt"
    ENTWURF_UNTERDRUECKT = "entwurf_unterdrueckt"
    KEIN_TREFFER = "kein_treffer"
    FREIGABE = "freigabe"
    PIPELINE_FEHLER = "pipeline_fehler"


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
            "Context-specific metadata (confidence scores, intermediate results, etc.)"
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
