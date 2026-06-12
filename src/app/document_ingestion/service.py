"""DocumentIngestion bounded context service.

Accepts raw Einwendung text, stores the original in the raw store, masks PII,
and returns an IngestionResult carrying the masked text and the per-type
masked-span counts (ADR-025).

The raw store holds the unmasked original. On POSIX the store directory and
files are created with restrictive modes (0o700 / 0o600) and a startup check
warns if the store is world-readable. These modes do not map cleanly to
Windows ACLs, so the enforcement is best-effort there; the access guarantee is
only as strong as the platform's POSIX-mode support (ADR-010, ADR-025).
"""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

import structlog

from app.core.failures import IngestionError
from app.core.results import IngestionResult
from app.document_ingestion.protocols import PiiMasker
from app.observability.events import INGESTION_RAW_STORE_WORLD_READABLE
from app.observability.tracing import traced

_log = structlog.get_logger()

# Upper bound on raw_text length, enforced at the ingest boundary. This is
# input validation at the edge, not a masking decision: a single oversized
# document would otherwise drive the spaCy NER and the per-token finditer in
# the masker unboundedly (resource exhaustion). The largest real Einwendung in
# the evaluation corpus is ~9,800 characters; 100,000 leaves an order of
# magnitude of headroom for a long multi-submitter objection while still
# bounding the work. Characters, not bytes: it is the unit the downstream
# regex and NER actually scan.
_MAX_RAW_TEXT_CHARS = 100_000


class DocumentIngestionService:
    """Ingests raw Einwendung text and prepares it for downstream processing.

    Stores the unmasked original first, then masks, so the original is in the
    raw store before any masking happens (ADR-010, ADR-025).

    Attributes:
        _raw_store_path: Directory for raw document storage. Created with
            restrictive permissions on POSIX (see module docstring).
        _masker: PII masker applied to the raw text before handoff.
    """

    def __init__(self, raw_store_path: Path, masker: PiiMasker) -> None:
        self._raw_store_path = raw_store_path
        self._masker = masker
        self._warn_if_world_readable()

    @traced(stage="document_ingestion")
    def ingest(self, raw_text: str) -> IngestionResult:
        """Accept raw text, store the original, mask PII, and return a result.

        Args:
            raw_text: Raw Einwendung text as received at system boundary.

        Returns:
            IngestionResult with document_id, the masked clean_text, the raw
            document path, and the per-type masked-span counts.

        Raises:
            IngestionError: If raw_text is empty, exceeds the length bound, or
                the store write fails.
        """
        if not raw_text or not raw_text.strip():
            raise IngestionError("raw_text must not be empty")
        if len(raw_text) > _MAX_RAW_TEXT_CHARS:
            raise IngestionError(
                f"raw_text exceeds the {_MAX_RAW_TEXT_CHARS}-character limit "
                f"({len(raw_text)} characters); reject at the boundary rather "
                "than drive the masker unboundedly"
            )

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
        """Store raw document in the raw store with restrictive permissions.

        On POSIX the directory is created mode 0o700 and the file is chmod'd to
        0o600 after the write, so the unmasked original is owner-only. On
        Windows POSIX modes do not map to ACLs, so the chmod is a best-effort
        no-op there and the store relies on the inherited NTFS permissions.

        Args:
            document_id: UUID assigned at ingestion time.
            raw_text: Original unmasked text.

        Returns:
            Path to stored raw document.

        Raises:
            IngestionError: If store write fails.
        """
        self._raw_store_path.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            # mkdir's mode is masked by umask, so set it explicitly afterwards.
            os.chmod(self._raw_store_path, 0o700)
        path = self._raw_store_path / f"{document_id}.txt"
        try:
            path.write_text(raw_text, encoding="utf-8")
        except OSError as e:
            raise IngestionError(f"Failed to write raw document: {e}") from e
        if os.name == "posix":
            os.chmod(path, 0o600)
        return path

    def _warn_if_world_readable(self) -> None:
        """Warn on POSIX if an existing raw store is world-readable.

        Verifies the design's access claim against what the filesystem
        actually enforces. Only meaningful on POSIX and only when the store
        already exists; a store created by _store_raw is 0o700. World-readable
        is a misconfiguration, not a masking outcome, so it is logged, not
        raised: the pipeline still runs, and the operator gets a clear signal.
        On Windows POSIX mode bits do not apply, so the check is skipped.
        """
        if os.name != "posix" or not self._raw_store_path.exists():
            return
        mode = self._raw_store_path.stat().st_mode
        if mode & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH):
            # Governed warning (ADR-026): the former stderr print bypassed the
            # logging controls. The store path is not logged (it could be
            # sensitive); the mode is enough to act on.
            _log.warning(
                INGESTION_RAW_STORE_WORLD_READABLE,
                store_mode=f"{stat.S_IMODE(mode):#o}",
            )
