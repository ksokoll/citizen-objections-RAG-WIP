"""DocumentIngestion bounded context service.

Accepts raw Einwendung text, stores the original in the access-controlled
raw store, masks PII, and returns an IngestionResult carrying the masked
text and the per-type masked-span counts (ADR-025).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.failures import IngestionError
from app.core.results import IngestionResult
from app.document_ingestion.protocols import PiiMasker


class DocumentIngestionService:
    """Ingests raw Einwendung text and prepares it for downstream processing.

    Stores the unmasked original first, then masks, so the original is in the
    access-controlled store before any masking happens (ADR-010, ADR-025).

    Attributes:
        _raw_store_path: Directory for access-controlled raw document storage.
        _masker: PII masker applied to the raw text before handoff.
    """

    def __init__(self, raw_store_path: Path, masker: PiiMasker) -> None:
        self._raw_store_path = raw_store_path
        self._masker = masker

    def ingest(self, raw_text: str) -> IngestionResult:
        """Accept raw text, store the original, mask PII, and return a result.

        Args:
            raw_text: Raw Einwendung text as received at system boundary.

        Returns:
            IngestionResult with document_id, the masked clean_text, the raw
            document path, and the per-type masked-span counts.

        Raises:
            IngestionError: If raw_text is empty or the store write fails.
        """
        if not raw_text or not raw_text.strip():
            raise IngestionError("raw_text must not be empty")

        document_id = str(uuid.uuid4())
        raw_document_path = self._store_raw(document_id, raw_text)

        masking_result = self._masker.mask(raw_text)

        return IngestionResult(
            document_id=document_id,
            clean_text=masking_result.text,
            raw_document_path=str(raw_document_path),
            entity_counts=masking_result.entity_counts,
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
