"""Briefing assembly service for the ResponseDrafting bounded context.

Application-layer service that assembles a WuerdigungsBriefing from the
Triage output and the resolved norms supplied by the Retrieval context.
Deterministic: no LLM, no external calls. Pairs each extracted argument
with its resolved norm text and assigns a per-argument status.

The service receives already-resolved norms (mapped from the Retrieval
context by the Coordinator) rather than calling Retrieval itself, keeping
the bounded-context boundary clean.
"""

from __future__ import annotations

from app.response_drafting.domain.entities import (
    BriefingEntry,
    BriefingStatus,
    ResolvedNormEntry,
    WuerdigungsBriefing,
)


class BriefingService:
    """Assembles the per-argument briefing deterministically.

    Has no collaborators: it is pure assembly logic over the inputs.
    Norm resolution has already happened in the Retrieval context; this
    service only arranges the results into the briefing structure and
    derives each argument's status.
    """

    def assemble(
        self,
        document_id: str,
        einwendungs_typ: str,
        arguments: list[dict],
        norms_by_argument: dict[str, list[ResolvedNormEntry]],
    ) -> WuerdigungsBriefing:
        """Build the briefing for one objection document.

        Args:
            document_id: The ingestion-assigned document identifier.
            einwendungs_typ: The document-level classification (TYP_1 or
                TYP_2).
            arguments: The extracted arguments. Each dict carries
                argument_id, argument_text, original_zitat,
                einwendungs_typ, and catalog_id, mirroring the Triage
                ExtrahiertesArgument fields the briefing needs.
            norms_by_argument: Map from argument_id to its resolved norm
                entries. An argument with no catalog match maps to an
                empty list.

        Returns:
            The assembled WuerdigungsBriefing.
        """
        entries: list[BriefingEntry] = []
        for arg in arguments:
            argument_id = arg["argument_id"]
            norms = norms_by_argument.get(argument_id, [])
            status = self._derive_status(arg["catalog_id"], norms)
            entries.append(
                BriefingEntry(
                    argument_id=argument_id,
                    argument_text=arg["argument_text"],
                    original_zitat=arg["original_zitat"],
                    einwendungs_typ=arg["einwendungs_typ"],
                    catalog_id=arg["catalog_id"],
                    norms=norms,
                    status=status,
                    requires_case_context=(status == BriefingStatus.BRIEFING_READY),
                )
            )

        return WuerdigungsBriefing(
            document_id=document_id,
            einwendungs_typ=einwendungs_typ,
            entries=entries,
        )

    @staticmethod
    def _derive_status(
        catalog_id: str | None,
        norms: list[ResolvedNormEntry],
    ) -> BriefingStatus:
        """Derive the per-argument briefing status.

        Args:
            catalog_id: The matched catalog entry, or None.
            norms: The argument's resolved norm entries.

        Returns:
            KEIN_TREFFER if there was no catalog match; NORM_UNRESOLVED
            if any cited norm is unresolved; BRIEFING_READY otherwise.
        """
        if catalog_id is None:
            return BriefingStatus.KEIN_TREFFER
        if any(not n.resolved for n in norms):
            return BriefingStatus.NORM_UNRESOLVED
        return BriefingStatus.BRIEFING_READY
