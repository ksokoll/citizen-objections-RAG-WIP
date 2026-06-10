"""Pipeline coordinator for the citizen objections RAG system.

Orchestrates the five bounded contexts sequentially:
DocumentIngestion -> Triage -> Retrieval -> Briefing -> AuditLog.

No BC calls another BC directly. All dependencies are injected at
construction time. The Coordinator is the composition root: it depends on
the concrete Ingestion, Triage, Briefing, and AuditLog services, and on
the Retriever Protocol for norm resolution (so a fake retriever can be
substituted in tests without a vector index or embedding model).

The Coordinator owns the cross-context mapping: it collects the canonical
citations from each Triage argument, resolves them via Retrieval, and maps
the resulting NormWithSource values into the Briefing context's
ResolvedNormEntry, so neither context imports the other's domain model.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.audit_log.service import AuditLogService
from app.briefing.entities import ResolvedNormEntry, WuerdigungsBriefing
from app.briefing.service import BriefingService
from app.core.events import AuditEvent, AuditEventType
from app.core.failures import (
    IngestionError,
    RetrievalError,
    TriageError,
)
from app.core.protocols import Retriever
from app.document_ingestion.service import DocumentIngestionService
from app.observability import reset_correlation_id, set_correlation_id
from app.observability.events import AUDIT_APPEND_FAILED
from app.triage.service import TriageService

_log = structlog.get_logger()


class Pipeline:
    """Coordinates the five BCs for end-to-end objection processing.

    Attributes:
        _ingestion: DocumentIngestion BC.
        _triage: Triage BC.
        _retrieval: Retrieval BC (via the Retriever Protocol).
        _briefing: Briefing BC.
        _audit: AuditLog BC.
    """

    def __init__(
        self,
        ingestion: DocumentIngestionService,
        triage: TriageService,
        retrieval: Retriever,
        briefing: BriefingService,
        audit: AuditLogService,
    ) -> None:
        self._ingestion = ingestion
        self._triage = triage
        self._retrieval = retrieval
        self._briefing = briefing
        self._audit = audit

    def run(self, raw_text: str) -> WuerdigungsBriefing:
        """Process a raw Einwendung through the full pipeline.

        Args:
            raw_text: Raw Einwendung text as received at system boundary.

        Returns:
            The assembled WuerdigungsBriefing for the Sachbearbeiter.

        Raises:
            IngestionError: If ingestion fails.
            TriageError: If argument extraction fails.
            RetrievalError: If norm retrieval fails.
        """
        einwendungs_id: str | None = None
        correlation_token = None

        try:
            ingestion_result = self._ingestion.ingest(raw_text)
            einwendungs_id = ingestion_result.document_id
            # Anchor every subsequent log event of this run on the document_id,
            # the pseudonymous correlation id (ADR-026). It is set here rather
            # than at run() entry because the id does not exist until ingestion
            # mints it; all emitting code runs after this point.
            correlation_token = set_correlation_id(einwendungs_id)
            self._emit(
                einwendungs_id,
                AuditEventType.EINGANG,
                {
                    "document_id": ingestion_result.document_id,
                    "masked_entity_counts": ingestion_result.entity_counts,
                },
            )

            triage_result = self._triage.triage(ingestion_result.clean_text)
            self._emit(
                einwendungs_id,
                AuditEventType.TRIAGE,
                {"argument_count": len(triage_result.extracted_arguments)},
            )

            arguments, norms_by_argument = self._resolve_norms(
                triage_result.extracted_arguments
            )
            resolved_total = sum(
                sum(1 for n in norms if n.resolved)
                for norms in norms_by_argument.values()
            )
            self._emit(
                einwendungs_id,
                AuditEventType.RETRIEVAL,
                {"resolved_norm_count": resolved_total},
            )

            briefing = self._briefing.assemble(
                document_id=einwendungs_id,
                einwendungs_typ=triage_result.einwendungs_typ.value,
                arguments=arguments,
                norms_by_argument=norms_by_argument,
            )

            event_type = (
                AuditEventType.KEIN_TREFFER
                if not triage_result.extracted_arguments
                else AuditEventType.BRIEFING_ERSTELLT
            )
            self._emit(
                einwendungs_id,
                event_type,
                {"entry_count": len(briefing.entries)},
            )

            return briefing

        except (IngestionError, TriageError, RetrievalError):
            if einwendungs_id:
                self._emit(
                    einwendungs_id,
                    AuditEventType.PIPELINE_FEHLER,
                    {"reason": "pipeline error"},
                )
            raise
        finally:
            if correlation_token is not None:
                reset_correlation_id(correlation_token)

    def _resolve_norms(
        self,
        extracted_arguments: list,
    ) -> tuple[list[dict[str, Any]], dict[str, list[ResolvedNormEntry]]]:
        """Resolve each argument's citations and map across the BC boundary.

        For each extracted argument, resolves its canonical citations via
        the Retrieval context and maps the returned NormWithSource values
        into Briefing-context ResolvedNormEntry objects. Also builds the
        plain-dict argument representation the Briefing service consumes,
        so the Briefing context does not depend on the Triage domain model.

        Args:
            extracted_arguments: The Triage ExtrahiertesArgument objects.

        Returns:
            A tuple of (arguments_as_dicts, norms_by_argument), where
            norms_by_argument maps each argument_id to its resolved norms.
        """
        arguments: list[dict[str, Any]] = []
        norms_by_argument: dict[str, list[ResolvedNormEntry]] = {}

        for arg in extracted_arguments:
            arguments.append(
                {
                    "argument_id": arg.argument_id,
                    "argument_text": arg.argument_text,
                    "original_zitat": arg.original_zitat,
                    "einwendungs_typ": arg.einwendungs_typ.value,
                    "catalog_id": arg.catalog_id,
                }
            )

            resolved = self._retrieval.resolve(arg.zitierte_normen)
            norms_by_argument[arg.argument_id] = [
                ResolvedNormEntry(
                    canonical_citation=n.canonical_citation,
                    paragraph_key=n.paragraph_key,
                    source_text=n.source_text,
                    resolved=n.resolved,
                )
                for n in resolved
            ]

        return arguments, norms_by_argument

    def _emit(
        self,
        einwendungs_id: str,
        event_type: AuditEventType,
        payload: dict[str, Any],
    ) -> None:
        """Emit an audit event. Interim: log a governed ERROR on failure.

        Interim policy (ADR-027): a failed publish is logged as a registered
        ERROR event (AUDIT_APPEND_FAILED) and swallowed, not raised. The
        fail-closed abort specified in ADR-027 for the six custody events lands
        in Round C behind this same log line, once the chain invariants that
        make an abort diagnosable exist (ADR-024).

        This replaces the previous stderr print, which bypassed every logging
        control and interpolated the raw exception text, itself a violation of
        the exception policy (ADR-026). The exception is attached via exc_info
        and reduced to type plus location by the logging chain; its message is
        never written.

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
        except Exception:
            _log.error(
                AUDIT_APPEND_FAILED,
                audit_event_type=event_type.value,
                exc_info=True,
            )
