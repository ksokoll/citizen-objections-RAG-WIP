# core.protocols.py - Protocol definitions for core services and interfaces.
from datetime import datetime
from typing import Protocol, TypeVar

from pydantic import BaseModel

from .events import AuditEvent, AuditEventType

T = TypeVar("T", bound=BaseModel)


class LLMClientProtocol(Protocol):
    """Provider-agnostic LLM text generation and structured parsing.

    Temperature and model selection are implementation details, not part of
    the interface. All LLM calls must go through this Protocol; no direct
    imports of anthropic, openai, or provider SDKs are allowed in domain or
    application code.
    """

    def generate(self, prompt: str, system_prompt: str = "") -> str: ...

    def parse(
        self,
        prompt: str,
        response_format: type[T],
        system_prompt: str = "",
    ) -> T: ...


class AuditEventPublisherProtocol(Protocol):
    """Append-only audit event store.

    All state changes must be audited via `publish`, which appends; no event is
    ever rewritten or deleted. `query` is for retrieving historical events; it
    returns empty list on no match, never raises.

    Duplicate detection is deliberately not part of this contract. The durable
    store keys the chain off an in-memory head and does not scan the file per
    append (ADR-030), so a re-published event_id is not rejected here; the
    pipeline mints a fresh id per event, making that a deliberate trade, not a
    gap. Append-only and failure-translation are the guarantees this protocol
    makes.

    Failure contract: implementations translate every I/O failure on the
    publish path into AuditLogError. No raw OSError may escape publish, so
    callers (Pipeline._emit, ADR-027) can route the recoverable store-failure
    class on exactly one exception type without depending on stdlib exception
    types. A contract test against each store implementation proves the
    translation.
    """

    def publish(self, event: AuditEvent) -> None:
        """Append an event to the audit log.

        Args:
            event: The audit event to record.

        Raises:
            AuditLogError: If an I/O failure prevents the append. Raw OSErrors
                are translated, never propagated.
        """
        ...

    def query(
        self,
        einwendungs_id: str | None = None,
        event_type: AuditEventType | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
    ) -> list[AuditEvent]:
        """Query historical audit events.

        Args:
            einwendungs_id: Filter by objection ID.
            event_type: Filter by event type.
            after: Return events with timestamp >= this value.
            before: Return events with timestamp <= this value.

        Returns:
            List of matching events, or empty list if no match.
        """
        ...
