from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class AuditEventType(StrEnum):
    """Classification of audit event by pipeline stage."""
    INGESTION = "ingestion"
    TRIAGE = "triage"
    DRAFT_GENERATED = "draft_generated"
    DRAFT_SUPPRESSED = "draft_suppressed"
    NO_MATCH = "no_match"
    FREIGABE = "freigabe"
    PIPELINE_ERROR = "pipeline_error"


class AuditEvent(BaseModel):
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
        description="Timestamp of event (UTC)"
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Context-specific metadata (confidence scores, intermediate results, etc.)"
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
