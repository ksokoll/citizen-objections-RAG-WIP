from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Naming convention: German for domain events, English for code identifiers

#: einwendungs_id carried by process-wide chain events that are not tied to a
#: citizen objection: the store's recovery event (ADR-030) and the startup
#: configuration event (ADR-031). A fixed non-objection sentinel so such a
#: custody record satisfies the required non-empty id without claiming an
#: Einwendung. Both process-wide events share this one sentinel, so a query for
#: system events finds them together regardless of which wrote them.
SYSTEM_EINWENDUNGS_ID: Final[str] = "SYSTEM"

#: Maximum length of any string in an AuditEvent payload. The hash chain must
#: stay content-free so it can coexist with the right to erasure: the chain is
#: undeletable, erasure hits only the raw store, and that holds only if the
#: chain carries nothing erasure-bound (ADR-031). The bound is a heuristic
#: against text snippets, not a guarantee: it admits SHA-256 hashes (64 hex),
#: short ids, version strings, and entity labels, while rejecting a pasted
#: objection fragment. A 30-char string can still be a name; the validator
#: enforces types and length mechanically, and the residual is named, exactly
#: like the log-key allowlist (mechanism plus policy plus residual).
MAX_PAYLOAD_STR_LEN: Final[int] = 128


def _payload_leaf_is_allowed(value: object) -> bool:
    """Whether a scalar payload value is within the content-free allowlist.

    Allowed leaves are int, float, bool (an int subclass), and strings up to
    MAX_PAYLOAD_STR_LEN. Everything else (None, bytes, nested containers, any
    object) is rejected.
    """
    if isinstance(value, int | float):
        return True
    if isinstance(value, str):
        return len(value) <= MAX_PAYLOAD_STR_LEN
    return False


def _payload_value_is_allowed(value: object) -> bool:
    """Whether one payload value is allowed: a leaf, or a flat list/dict of them.

    A single level of list or dict is permitted so counts maps (entity counts)
    and version maps pass while staying content-free: every element must itself
    be an allowed leaf, so a dict of dicts or a list of lists is rejected. Dict
    keys must be strings within the same length bound.
    """
    if isinstance(value, list):
        return all(_payload_leaf_is_allowed(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str)
            and len(key) <= MAX_PAYLOAD_STR_LEN
            and _payload_leaf_is_allowed(item)
            for key, item in value.items()
        )
    return _payload_leaf_is_allowed(value)


class AuditEventType(StrEnum):
    """Classification of an audit event.

    Most members name a pipeline stage. Two are exceptions, both process-wide
    custody records rather than pipeline steps: WIEDERHERSTELLUNG, recorded when
    the store quarantines a damaged tail at open (ADR-030), and STARTKONFIGURATION,
    recorded at process start to prove the active controls after the fact
    (ADR-031). Both carry the SYSTEM_EINWENDUNGS_ID sentinel. They live here
    because AuditEvent.event_type is typed against this enum and each is a
    custody record like any other.
    """

    EINGANG = "eingang"
    TRIAGE = "triage"
    RETRIEVAL = "retrieval"
    BRIEFING_ERSTELLT = "briefing_erstellt"
    ENTWURF_UNTERDRUECKT = "entwurf_unterdrueckt"
    KEIN_TREFFER = "kein_treffer"
    FREIGABE = "freigabe"
    PIPELINE_FEHLER = "pipeline_fehler"
    WIEDERHERSTELLUNG = "wiederherstellung"
    STARTKONFIGURATION = "startkonfiguration"


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

    @field_validator("payload")
    @classmethod
    def validate_payload_is_content_free(
        cls, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Reject payload values that are not content-free simple types (ADR-031).

        The chain is the precondition for erasure coexistence: it is undeletable,
        and the right to erasure reaches only the raw store, which holds only if
        the chain carries nothing erasure-bound. So a payload value may be int,
        float, bool, a string up to MAX_PAYLOAD_STR_LEN, or a flat list/dict of
        those (counts and version maps). A value outside that set, a deeper
        nesting, or an over-length string is rejected at construction, loudly,
        so a text fragment cannot enter the chain by accident. This is a
        mechanism with a named residual, not a guarantee: a short string can
        still be a name (see MAX_PAYLOAD_STR_LEN).

        Args:
            payload: The event payload to validate.

        Returns:
            The validated payload, unchanged.

        Raises:
            ValueError: If any value (or nested element) is outside the
                allowlist, naming the offending key.
        """
        for key, value in payload.items():
            if not _payload_value_is_allowed(value):
                raise ValueError(
                    f"payload[{key!r}] is not content-free: an audit payload "
                    "value must be int, float, bool, a string no longer than "
                    f"{MAX_PAYLOAD_STR_LEN} characters, or a flat list/dict of "
                    "those, so the tamper-evident chain carries no erasure-bound "
                    "text (ADR-031)"
                )
        return payload
