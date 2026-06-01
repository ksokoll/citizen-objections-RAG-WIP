from datetime import datetime
from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.retrieval.entities import NormWithSource

from .entities import RetrievedChunk
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


class Retriever(Protocol):
    """Resolves canonical norm citations to their source Gesetzestext.

    Implemented by the Retrieval context's NormRetrievalService. The
    Coordinator depends on this Protocol rather than the concrete service,
    so tests can substitute a fake without a statute corpus.
    """

    def resolve(self, citations: list[str]) -> list[NormWithSource]:
        """Resolve canonical norm citations to their source Gesetzestext."""
        ...


class EmbedderProtocol(Protocol):
    """Text embedding generation.

    Returns a normalized embedding vector for the given text. Embedding
    dimension is implementation-dependent and should be documented by
    the concrete implementation (typically 384, 768, or 1536).
    """

    def embed(self, text: str) -> list[float]: ...


class RetrieverProtocol(Protocol):
    """Per-corpus retrieval for legal norm chunks.

    Implementations are responsible for any internal embedding step.
    The query string is passed verbatim to allow both sparse (BM25)
    and dense (FAISS) ranking inside the implementation.
    """

    def retrieve(
        self,
        query: str,
        partition: str,
        top_k: int = 5,
    ) -> list[RetrievedChunk]: ...


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
