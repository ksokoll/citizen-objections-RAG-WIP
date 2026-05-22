"""Pipeline coordinator for the citizen objections RAG system.

Orchestrates the four bounded contexts sequentially:
DocumentIngestion → Triage → ResponseDrafting → AuditLog.

No BC calls another BC directly. All dependencies are injected
at construction time via Protocols.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from citizen_objections_rag.audit_log.service import AuditLogService
from citizen_objections_rag.core.entities import Abwaegungsstellungnahme
from citizen_objections_rag.core.events import AuditEvent, AuditEventType
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
        einwendungs_id = str(uuid.uuid4())

        ingestion_result = self._ingestion.ingest(raw_text)
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
                "einwendungs_typ": triage_result.einwendungs_typ.value,
            },
        )

        if not triage_result.extracted_arguments:
            stellungnahme = self._drafting.draft(
                triage_result,
                ingestion_result.clean_text,
                einwendungs_id,
            )
            self._emit(einwendungs_id, AuditEventType.KEIN_TREFFER, {})
            return stellungnahme

        stellungnahme = self._drafting.draft(
            triage_result,
            ingestion_result.clean_text,
            einwendungs_id,
        )
        self._emit(
            einwendungs_id,
            AuditEventType.ENTWURF_GENERIERT,
            {
                "wuerdigungs_status": stellungnahme.wuerdigungs_status.value,
            },
        )

        return stellungnahme

    def _emit(
        self,
        einwendungs_id: str,
        event_type: AuditEventType,
        payload: dict,
    ) -> None:
        """Emit an audit event. Logs error on failure, never raises.

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
                    timestamp=datetime.now(UTC),
                    payload=payload,
                )
            )
        except Exception:
            pass
