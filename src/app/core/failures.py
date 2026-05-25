"""Custom exception types per bounded context."""


class IngestionError(Exception):
    """Raised by DocumentIngestion on empty input or store write failure."""


class TriageError(Exception):
    """Raised by Triage on embedding model failure or empty catalog."""


class RetrievalError(Exception):
    """Raised by ResponseDrafting on FAISS index query failure."""


class GenerationError(Exception):
    """Raised by ResponseDrafting on LLM call failure after retries."""


class AuditLogError(Exception):
    """Raised by AuditLog on store write failure or duplicate event_id."""


class LLMParseError(Exception):
    """LLM structured parsing failed.

    Raised when the provider call fails or returns no parsed content.
    Concrete implementations of the LLMClient protocol convert
    provider-specific exceptions to this domain-level error.
    """


class LLMError(Exception):
    """LLM provider call failed.

    Raised when a provider call fails, times out, or returns no content.
    Concrete implementations of the LLMClient protocol convert
    provider-specific exceptions (RateLimitError, APIConnectionError, etc.)
    to this domain-level error so the application layer does not depend
    on provider SDK types.
    """
