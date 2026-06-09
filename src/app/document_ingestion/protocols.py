"""Protocol definitions for the DocumentIngestion bounded context.

PiiMasker is consumed only by the DocumentIngestionService and implemented
only by PresidioMasker (production) and FakePiiMasker (tests), all inside
this context. It never crosses a context boundary, so it belongs here, not in
the shared kernel (ADR-025).
"""

from __future__ import annotations

from typing import Protocol

from app.document_ingestion.entities import MaskingResult


class PiiMasker(Protocol):
    """Masks personally identifiable information in free text.

    Implemented by the DocumentIngestion context's PresidioMasker. The
    DocumentIngestionService depends on this Protocol rather than the concrete
    masker, so unit tests can substitute a fake that does not load the spaCy
    model. Masking is one-way: implementations must not retain a
    placeholder-to-original mapping. The original is recoverable only from the
    raw store via the document_id; that store is created owner-restricted on
    POSIX (0o700 / 0o600), best-effort on Windows (ADR-010, ADR-025).
    """

    def mask(self, text: str) -> MaskingResult:
        """Replace detected PII spans with speaking German type placeholders.

        Args:
            text: Raw text that may contain PII (names, addresses, phone
                numbers, email, IBAN, case numbers).

        Returns:
            MaskingResult with the masked text and per-type masked-span counts.
        """
        ...
