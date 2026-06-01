"""DocumentIngestion bounded context service.

Accepts raw Einwendung text and returns a masked IngestionResult.
In the skeleton this is a pass-through: clean_text == raw_text.
PII masking is introduced in feat/pii-masking.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.failures import IngestionError
from app.core.results import IngestionResult


class DocumentIngestionService:
    """Ingests raw Einwendung text and prepares it for downstream processing.

    Attributes:
        _raw_store_path: Directory for access-controlled raw document storage.
    """

    def __init__(self, raw_store_path: Path) -> None:
        self._raw_store_path = raw_store_path

    def ingest(self, raw_text: str) -> IngestionResult:
        """Accept raw text and return masked IngestionResult.

        Args:
            raw_text: Raw Einwendung text as received at system boundary.

        Returns:
            IngestionResult with document_id, clean_text, raw_document_path.

        Raises:
            IngestionError: If raw_text is empty.
        """
        if not raw_text or not raw_text.strip():
            raise IngestionError("raw_text must not be empty")

        document_id = str(uuid.uuid4())
        raw_document_path = self._store_raw(document_id, raw_text)

        # TODO(feat/pii-masking): apply PII masking before handoff
        clean_text = raw_text

        return IngestionResult(
            document_id=document_id,
            clean_text=clean_text,
            raw_document_path=str(raw_document_path),
        )

    def _store_raw(self, document_id: str, raw_text: str) -> Path:
        """Store raw document in access-controlled store.

        Args:
            document_id: UUID assigned at ingestion time.
            raw_text: Original unmasked text.

        Returns:
            Path to stored raw document.

        Raises:
            IngestionError: If store write fails.
        """
        self._raw_store_path.mkdir(parents=True, exist_ok=True)
        path = self._raw_store_path / f"{document_id}.txt"
        try:
            path.write_text(raw_text, encoding="utf-8")
        except OSError as e:
            raise IngestionError(f"Failed to write raw document: {e}") from e
        return path
