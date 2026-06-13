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
from datetime import UTC, datetime
from typing import Any

import structlog

from app.audit_log.service import AuditLogService
from app.briefing.entities import ResolvedNormEntry, WuerdigungsBriefing
from app.briefing.service import BriefingService
from app.core.entities import ExtrahiertesArgument
from app.core.events import AuditEvent, AuditEventType
from app.core.failures import (
    AuditLogError,
    IngestionError,
    RetrievalError,
    TriageError,
)
from app.core.protocols import Retriever
from app.core.results import TriageResult
from app.document_ingestion.service import DocumentIngestionService
from app.observability import correlation_scope
from app.observability.events import AUDIT_APPEND_FAILED
from app.observability.metrics import (
    inc_argument_verification_failures,
    inc_audit_write_failure,
    inc_norm_resolutions,
    inc_objection_processed,
    inc_triage_contradiction,
    observe_arguments_per_objection,
)
from app.observability.tracing import clear_finished_spans, traced
from app.triage.service import TriageService

_log = structlog.get_logger()


class Pipeline:
    """Coordinates the five BCs for end-to-end objection processing.

    Attributes:
        _ingestion: DocumentIngestion BC.
        _triage: Triage BC.
        _retrieval: Retrieval BC (via the Retriever Protocol). Owns the
            corpus identity: the corpus_id stamped into every briefing is
            read from the retriever, so the provenance is structurally that
            of the corpus actually resolved against; no separate id
            parameter exists to lie with (ADR-028).
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

    @traced(stage="pipeline.run")
    def run(self, raw_text: str) -> WuerdigungsBriefing:
        """Process a raw Einwendung through the full pipeline.

        Reads as a stage sequence. Ingestion mints the document id (no
        correlation id exists before it), then the run binds that id as the
        correlation scope for every subsequent event and walks the stages,
        aggregating the domain metrics and emitting one audit event per stage.
        The cross-cutting bookkeeping sits behind named seams: the correlation
        lifecycle in a context manager, the metric aggregation in the
        _record_triage_metrics and _norm_resolution_counts mappers.

        Args:
            raw_text: Raw Einwendung text as received at system boundary.

        Returns:
            The assembled WuerdigungsBriefing for the Sachbearbeiter.

        Raises:
            IngestionError: If ingestion fails.
            TriageError: If argument extraction fails.
            RetrievalError: If norm retrieval fails.
        """
        # The run owner defines the run (M5): discard the previous run's
        # finished spans here at run start, not via a parentage heuristic in
        # the instrumentation layer. Single-run assumption: the global exporter
        # is cleared, not run-scoped (ADR-026, declared assumption).
        clear_finished_spans()

        try:
            ingestion_result = self._ingestion.ingest(raw_text)
        except IngestionError:
            # Ingestion failure precedes the correlation id: there is no
            # document id to anchor events on yet, so only the terminal metric
            # is recorded before the failure propagates.
            inc_objection_processed(status=AuditEventType.PIPELINE_FEHLER.value)
            raise

        einwendungs_id = ingestion_result.document_id
        # Bind the document id as the correlation scope for every subsequent
        # event of this run (ADR-026). The scope opens here, after ingestion
        # mints the id, and the ContextVar stays the transport: the structlog
        # correlation processor reaches it ambiently, not through any object
        # threaded into the chain.
        with correlation_scope(einwendungs_id):
            try:
                self._emit(
                    einwendungs_id,
                    AuditEventType.EINGANG,
                    {
                        "document_id": ingestion_result.document_id,
                        "masked_entity_counts": ingestion_result.entity_counts,
                    },
                )

                triage_result = self._triage.triage(ingestion_result.clean_text)
                self._record_triage_metrics(triage_result)
                self._emit(
                    einwendungs_id,
                    AuditEventType.TRIAGE,
                    {
                        "argument_count": len(triage_result.extracted_arguments),
                        "contradiction_detected": triage_result.contradiction_detected,
                    },
                )

                arguments = self._map_arguments(triage_result.extracted_arguments)
                norms_by_argument = self._resolve_norms(
                    triage_result.extracted_arguments
                )
                resolved_total, unresolved_total = self._norm_resolution_counts(
                    norms_by_argument
                )
                inc_norm_resolutions(
                    resolved=resolved_total,
                    unresolved=unresolved_total,
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
                    corpus_id=self._retrieval.corpus_id,
                    created_at=datetime.now(UTC),
                )

                event_type = (
                    AuditEventType.KEIN_TREFFER
                    if not triage_result.extracted_arguments
                    else AuditEventType.BRIEFING_ERSTELLT
                )
                inc_objection_processed(status=event_type.value)
                self._emit(
                    einwendungs_id,
                    event_type,
                    {"entry_count": len(briefing.entries)},
                )

                return briefing

            except (TriageError, RetrievalError):
                inc_objection_processed(status=AuditEventType.PIPELINE_FEHLER.value)
                self._emit(
                    einwendungs_id,
                    AuditEventType.PIPELINE_FEHLER,
                    {"reason": "pipeline error"},
                )
                raise

    def _record_triage_metrics(self, triage_result: TriageResult) -> None:
        """Aggregate and emit the Coordinator's triage-stage domain metrics.

        Named seam for the per-argument aggregation that was inline in run().
        The Coordinator owns domain-metric emission (single-layer ownership):
        the arguments-per-objection histogram, the verification-failure count
        (ADR-006 Layer 1), and the contradiction counter (counted from the flag
        Triage carries on its result). Each helper is contained and cannot
        abort the run.

        Args:
            triage_result: The Triage stage output for this run.
        """
        arguments = triage_result.extracted_arguments
        observe_arguments_per_objection(len(arguments))
        inc_argument_verification_failures(
            sum(1 for arg in arguments if not arg.argument_verified)
        )
        if triage_result.contradiction_detected:
            inc_triage_contradiction()

    @staticmethod
    def _norm_resolution_counts(
        norms_by_argument: dict[str, list[ResolvedNormEntry]],
    ) -> tuple[int, int]:
        """Return the (resolved, unresolved) norm counts across all arguments.

        Named seam for the norm aggregation that was inline in run(). The
        quality signal is the unresolved ratio, so both counts are produced
        here and fed to the norm-resolution metric; resolved_total also feeds
        the RETRIEVAL audit payload.

        Args:
            norms_by_argument: Resolved norms keyed by argument id.

        Returns:
            A (resolved, unresolved) pair over every argument's norms.
        """
        resolved = sum(
            sum(1 for n in norms if n.resolved) for norms in norms_by_argument.values()
        )
        total = sum(len(norms) for norms in norms_by_argument.values())
        return resolved, total - resolved

    @staticmethod
    def _map_arguments(
        extracted_arguments: list[ExtrahiertesArgument],
    ) -> list[dict[str, Any]]:
        """Map Triage arguments into the plain dicts the Briefing consumes.

        Cross-context mapping owned by the Coordinator: the Briefing service
        receives plain dicts so it does not depend on the Triage domain model.
        This seam is a contract site (ADR-028): a field dropped here would
        silently vanish from the delivery contract, so a mapping-seam test
        asserts this field list against BriefingEntry.

        Args:
            extracted_arguments: The Triage ExtrahiertesArgument objects.

        Returns:
            One dict per argument with the six fields the Briefing reads.
        """
        return [
            {
                "argument_id": arg.argument_id,
                "argument_text": arg.argument_text,
                "original_zitat": arg.original_zitat,
                "einwendungs_typ": arg.einwendungs_typ.value,
                "catalog_id": arg.catalog_id,
                "argument_verified": arg.argument_verified,
            }
            for arg in extracted_arguments
        ]

    def _resolve_norms(
        self,
        extracted_arguments: list[ExtrahiertesArgument],
    ) -> dict[str, list[ResolvedNormEntry]]:
        """Resolve each argument's citations and map across the BC boundary.

        For each extracted argument, resolves its canonical citations via
        the Retrieval context and maps the returned NormWithSource values
        into Briefing-context ResolvedNormEntry objects, so neither context
        imports the other's domain model.

        Args:
            extracted_arguments: The Triage ExtrahiertesArgument objects.

        Returns:
            A map from each argument_id to its resolved norms.
        """
        norms_by_argument: dict[str, list[ResolvedNormEntry]] = {}
        for arg in extracted_arguments:
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
        return norms_by_argument

    def _emit(
        self,
        einwendungs_id: str,
        event_type: AuditEventType,
        payload: dict[str, Any],
    ) -> None:
        """Emit an audit event. Interim: log a governed ERROR on store failure.

        Interim policy (ADR-027): a failed publish is logged as a registered
        ERROR event (AUDIT_APPEND_FAILED) and swallowed, not raised. The
        fail-closed abort specified in ADR-027 for the six custody events lands
        in Round C behind this same log line, once the chain invariants that
        make an abort diagnosable exist (ADR-024).

        Only the recoverable store-failure class is swallowed: AuditLogError.
        The publisher contract (core/protocols.py) obliges every store
        implementation to translate raw I/O failures into AuditLogError, so a
        raw OSError arriving here is a contract violation, not an expected
        store failure. It propagates, like every programming error (TypeError,
        ValueError, a bug in the publish path), so the violation surfaces in
        development instead of being silently treated like a transient I/O
        hiccup (failure-routing rule, ADR-027).

        This replaces the previous stderr print, which bypassed every logging
        control and interpolated the raw exception text, itself a violation of
        the exception policy (ADR-026). The exception is attached via exc_info
        and reduced to type plus location by the logging chain; its message is
        never written. The _log.error call is itself guarded: by the
        never-raises contract of this interim emit, a failure in the logging
        path (a sabotaged or degraded sink) must not turn into a raise either.
        The audit_write_failures_total increment before it is the
        sink-independent visibility for that double failure (ADR-027 interim
        section): it counts even when the log sink is also down, and the
        contained metrics helper cannot raise.

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
        except AuditLogError:
            inc_audit_write_failure()
            try:
                _log.error(
                    AUDIT_APPEND_FAILED,
                    audit_event_type=event_type.value,
                    exc_info=True,
                )
            except Exception:
                # Never-raises contract: even a failing sink must not abort the
                # run from inside the interim emit. The metric increment above
                # already counted the failure, sink-independently.
                pass
