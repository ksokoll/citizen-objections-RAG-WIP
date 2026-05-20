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
