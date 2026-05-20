from datetime import datetime
from typing import Protocol

from .entities import CatalogMatch, RetrievedChunk
from .events import AuditEvent, AuditEventType


class LLMClientProtocol(Protocol):
    """Provider-agnostic LLM text generation.

    Temperature and model selection are implementation details, not part of
    the interface. All LLM calls must go through this Protocol; no direct
    imports of anthropic, openai, or provider SDKs are allowed in domain or
    application code.
    """

    def generate(self, prompt: str, system_prompt: str = "") -> str: ...


class EmbedderProtocol(Protocol):
    """Text embedding generation.

    Returns a normalized embedding vector for the given text. Embedding
    dimension is implementation-dependent and should be documented by
    the concrete implementation (typically 384, 768, or 1536).
    """

    def embed(self, text: str) -> list[float]: ...


class RetrieverProtocol(Protocol):
    """Vector-based document retrieval.

    Returns up to `top_k` chunks ranked by relevance to the query embedding.
    Returns empty list when no chunks exceed the similarity threshold.
    """

    def retrieve(
        self, query_embedding: list[float], top_k: int = 5
    ) -> list[RetrievedChunk]: ...


class KatalogMatcherProtocol(Protocol):
    """Catalog matching for objection classification.

    Matches extracted argument text against the predefined catalog of
    objection types and handling rules. Returns None when no entry exceeds
    the confidence threshold. The Coordinator is responsible for emitting
    a KEIN_TREFFER event if needed; this Protocol only returns matching results.
    """

    def match(self, text: str) -> CatalogMatch | None: ...


class AuditEventPublisherProtocol(Protocol):
    """Append-only audit event store.

    All state changes must be audited via `publish`. The store must enforce
    immutability: duplicate event_ids raise AuditLogError. `query` is for
    retrieving historical events; it returns empty list on no match, never raises.
    """

    def publish(self, event: AuditEvent) -> None:
        """Append an event to the audit log.

        Args:
            event: The audit event to record.

        Raises:
            AuditLogError: If event_id already exists (duplicate prevention).
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
