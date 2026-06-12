"""Assembly service for the Briefing bounded context.

Application-layer service that assembles a WuerdigungsBriefing from the
Triage output and the resolved norms supplied by the Retrieval context.
Deterministic: no LLM, no external calls. Pairs each extracted argument
with its resolved norm text and assigns a per-argument status.

The service receives already-resolved norms (mapped from the Retrieval
context by the Coordinator) rather than calling Retrieval itself, keeping
the bounded-context boundary clean.
"""

from __future__ import annotations

from datetime import datetime

from app.briefing.entities import (
    BriefingEntry,
    BriefingStatus,
    ResolvedNormEntry,
    WuerdigungsBriefing,
)
from app.observability.tracing import traced


class BriefingService:
    """Assembles the per-argument briefing deterministically.

    Has no collaborators: it is pure assembly logic over the inputs.
    Norm resolution has already happened in the Retrieval context; this
    service only arranges the results into the briefing structure and
    derives each argument's status.
    """

    @traced(stage="briefing")
    def assemble(
        self,
        document_id: str,
        einwendungs_typ: str,
        arguments: list[dict],
        norms_by_argument: dict[str, list[ResolvedNormEntry]],
        corpus_id: str,
        created_at: datetime,
    ) -> WuerdigungsBriefing:
        """Build the briefing for one objection document.

        Args:
            document_id: The ingestion-assigned document identifier.
            einwendungs_typ: The document-level classification (TYP_1 or
                TYP_2).
            arguments: The extracted arguments. Each dict carries
                argument_id, argument_text, original_zitat,
                einwendungs_typ, catalog_id, and argument_verified,
                mirroring the Triage ExtrahiertesArgument fields the
                briefing needs.
            norms_by_argument: Map from argument_id to its resolved norm
                entries. An argument with no catalog match maps to an
                empty list.
            corpus_id: Content-based identifier of the statute corpus the
                norms were resolved against, supplied by the Coordinator
                (ADR-028, provenance).
            created_at: Creation time of the briefing, timezone-aware UTC,
                supplied by the Coordinator.

        Returns:
            The assembled WuerdigungsBriefing.
        """
        entries: list[BriefingEntry] = []
        for arg in arguments:
            argument_id = arg["argument_id"]
            norms = norms_by_argument.get(argument_id, [])
            status = self._derive_status(
                arg["catalog_id"], arg["argument_verified"], norms
            )
            entries.append(
                BriefingEntry(
                    argument_id=argument_id,
                    argument_text=arg["argument_text"],
                    original_zitat=arg["original_zitat"],
                    einwendungs_typ=arg["einwendungs_typ"],
                    catalog_id=arg["catalog_id"],
                    argument_verified=arg["argument_verified"],
                    norms=norms,
                    status=status,
                    requires_case_context=(status == BriefingStatus.BRIEFING_READY),
                )
            )

        return WuerdigungsBriefing(
            document_id=document_id,
            einwendungs_typ=einwendungs_typ,
            corpus_id=corpus_id,
            created_at=created_at,
            entries=entries,
        )

    @staticmethod
    def _derive_status(
        catalog_id: str | None,
        argument_verified: bool,
        norms: list[ResolvedNormEntry],
    ) -> BriefingStatus:
        """Derive the per-argument briefing status.

        The verification verdict dominates (S2, ADR-028): a quote that
        failed the deterministic substring check is potentially fabricated,
        which outranks every other signal. An unverified argument therefore
        never yields BRIEFING_READY, regardless of catalog match or norm
        resolution.

        Args:
            catalog_id: The matched catalog entry, or None.
            argument_verified: The ADR-006 Layer 1 verification verdict.
            norms: The argument's resolved norm entries.

        Returns:
            ZITAT_NICHT_VERIFIZIERT if the quote check failed;
            KEIN_TREFFER if there was no catalog match; NORM_UNRESOLVED
            if any cited norm is unresolved; BRIEFING_READY otherwise.
        """
        if not argument_verified:
            return BriefingStatus.ZITAT_NICHT_VERIFIZIERT
        if catalog_id is None:
            return BriefingStatus.KEIN_TREFFER
        if any(not n.resolved for n in norms):
            return BriefingStatus.NORM_UNRESOLVED
        return BriefingStatus.BRIEFING_READY
