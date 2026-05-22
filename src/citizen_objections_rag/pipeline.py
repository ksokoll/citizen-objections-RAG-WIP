"""Pipeline coordinator for the citizen objections RAG system.

Orchestrates the four bounded contexts sequentially:
DocumentIngestion → Triage → ResponseDrafting → AuditLog.

No BC calls another BC directly. All dependencies are injected
at construction time. The Pipeline uses concrete BC service classes,
not Protocols, because the Coordinator is the composition root.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

from citizen_objections_rag.audit_log.service import AuditLogService
from citizen_objections_rag.core.entities import Abwaegungsstellungnahme
from citizen_objections_rag.core.events import AuditEvent, AuditEventType
from citizen_objections_rag.core.failures import (
    GenerationError,
    IngestionError,
    RetrievalError,
    TriageError,
)
from citizen_objections_rag.document_ingestion.service import DocumentIngestionService
from citizen_objections_rag.response_drafting.service import ResponseDraftingService
from citizen_objections_rag.triage.service import TriageService


class Pipeline:
    """Coordinates the four BCs for end-to-end objection processing.

    Attributes:
        _ingestion: DocumentIngestion BC.
        _triage: Triage BC.
        _drafting: ResponseDrafting BC.
        _audit: AuditLog BC.
    """

    def __init__(
        self,
        ingestion: DocumentIngestionService,
        triage: TriageService,
        drafting: ResponseDraftingService,
        audit: AuditLogService,
    ) -> None:
        self._ingestion = ingestion
        self._triage = triage
        self._drafting = drafting
        self._audit = audit

    def run(self, raw_text: str) -> Abwaegungsstellungnahme:
        """Process a raw Einwendung through the full pipeline.

        Args:
            raw_text: Raw Einwendung text as received at system boundary.

        Returns:
            Abwaegungsstellungnahme in DRAFT status.

        Raises:
            IngestionError: If ingestion fails.
            TriageError: If argument extraction fails.
            RetrievalError: If norm retrieval fails.
            GenerationError: If LLM generation fails.
        """
        einwendungs_id: str | None = None

        try:
            ingestion_result = self._ingestion.ingest(raw_text)
            einwendungs_id = ingestion_result.document_id
            self._emit(
                einwendungs_id,
                AuditEventType.EINGANG,
                {
                    "document_id": ingestion_result.document_id,
                },
            )

            triage_result = self._triage.triage(ingestion_result.clean_text)
            self._emit(
                einwendungs_id,
                AuditEventType.TRIAGE,
                {
                    "argument_count": len(triage_result.extracted_arguments),
                },
            )

            stellungnahme = self._drafting.draft(
                triage_result,
                ingestion_result.clean_text,
                einwendungs_id,
            )

            event_type = (
                AuditEventType.KEIN_TREFFER
                if not triage_result.extracted_arguments
                else AuditEventType.ENTWURF_GENERIERT
            )
            self._emit(
                einwendungs_id,
                event_type,
                {
                    "wuerdigungs_status": stellungnahme.wuerdigungs_status.value,
                },
            )

            return stellungnahme

        except (IngestionError, TriageError, RetrievalError, GenerationError):
            if einwendungs_id:
                self._emit(
                    einwendungs_id,
                    AuditEventType.PIPELINE_FEHLER,
                    {
                        "reason": "pipeline error",
                    },
                )
            raise

    def _emit(
        self,
        einwendungs_id: str,
        event_type: AuditEventType,
        payload: dict[str, Any],
    ) -> None:
        """Emit an audit event. Logs to stderr on failure, never raises.

        Args:
            einwendungs_id: ID of the objection being processed.
            event_type: Type of audit event.
            payload: Event-specific detail.
        """
        try:
            self._audit.publish(
                AuditEvent(
                    event_id=str(uuid.uuid4()),
                    event_type=event_type,
                    einwendungs_id=einwendungs_id,
                    payload=payload,
                )
            )
        except Exception as e:
            print(
                f"AUDIT ERROR: failed to emit {event_type.value} "
                f"for {einwendungs_id}: {e}",
                file=sys.stderr,
            )
