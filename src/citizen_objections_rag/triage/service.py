"""Triage bounded context service.

Extracts discrete legal arguments from a masked Einwendung text and
classifies each against the predefined catalog (ADR-002, ADR-013).

Architecture (post Schritt 1):
- LLM produces LLMArgument with four semantic fields (argument_text,
  original_zitat, catalog_id, einwendungs_typ). The zitierte_normen field
  is NOT in the LLM-facing schema, eliminating hallucination risk on norm
  citations (cf. Magesh et al. 2025, Stanford RegLab; EVAL_RESULTS.md).
- After the LLM call, the service runs deterministic norm extraction over
  the full text once, then assigns norms to each argument via position
  overlap against original_zitat (Option Y).
- If original_zitat is not found in the source text, zitierte_normen is
  empty and argument_verified is False (Option K). The argument is NOT
  discarded here; downstream consumers decide based on argument_verified.
  The Coordinator emits the corresponding audit event.

In the skeleton, the LLM call is stubbed.
"""

from __future__ import annotations

import uuid

from citizen_objections_rag.core.entities import ExtrahiertesArgument
from citizen_objections_rag.core.protocols import LLMClientProtocol
from citizen_objections_rag.core.results import TriageResult
from citizen_objections_rag.core.statuses import EinwendungsTyp
from citizen_objections_rag.triage.catalog import CatalogId
from citizen_objections_rag.triage.llm_schema import LLMArgument
from citizen_objections_rag.triage.norm_extractor import ExtractedNorm, extract_norms


class TriageService:
    """Extracts and classifies legal arguments from Einwendung text.

    Attributes:
        _llm: LLM client for argument extraction (stubbed in skeleton).
    """

    def __init__(self, llm: LLMClientProtocol) -> None:
        self._llm = llm

    def triage(self, clean_text: str) -> TriageResult:
        """Extract legal arguments and classify against catalog.

        Pipeline:
            1. LLM extraction returns four-field LLMArgument objects.
            2. Single pass of deterministic norm extraction over clean_text.
            3. Per argument: verify original_zitat and assign norms positionally.

        Args:
            clean_text: PII-masked Einwendung text from DocumentIngestion.

        Returns:
            TriageResult with extracted arguments. Empty list is valid
            for TYP_1 documents with no legal arguments.

        Raises:
            TriageError: If the LLM call fails.
        """
        raw_arguments = self._extract_arguments(clean_text)
        all_norms = extract_norms(clean_text)
        extracted_arguments = [
            self._build_extrahiertes_argument(raw, clean_text, all_norms)
            for raw in raw_arguments
        ]
        return TriageResult(
            extracted_arguments=extracted_arguments,
        )

    def _extract_arguments(self, clean_text: str) -> list[LLMArgument]:
        """Extract legal arguments via LLM structured output.

        In the skeleton this returns hardcoded arguments. The real
        LLM call is introduced in feat/triage. The LLM-facing schema
        contains four semantic fields only; zitierte_normen is added
        deterministically afterwards.

        Args:
            clean_text: PII-masked Einwendung text.

        Returns:
            List of LLMArgument as produced by the LLM. Norm extraction
            and verification happen in the caller.
        """
        # TODO(feat/triage): replace stub with real LLM structured output call
        return [
            LLMArgument(
                argument_text=(
                    "Widerspruch zum Flächennutzungsplan: Bebauungsplan nicht "
                    "ordnungsgemäß aus dem Flächennutzungsplan entwickelt."
                ),
                original_zitat=(
                    "Ein vorhabenbezogener Bebauungsplan, der von dieser "
                    "Darstellung des Flächennutzungsplans abweicht"
                ),
                catalog_id=CatalogId.C_005,
                einwendungs_typ=EinwendungsTyp.TYP_2,
            ),
            LLMArgument(
                argument_text=(
                    "Fehlende Bürgerbeteiligung: Öffentlichkeit nicht "
                    "frühzeitig über wesentliche Planänderungen unterrichtet."
                ),
                original_zitat=(
                    "Die Öffentlichkeit wurde über grundlegende "
                    "Planänderungen nicht frühzeitig unterrichtet"
                ),
                catalog_id=CatalogId.C_002,
                einwendungs_typ=EinwendungsTyp.TYP_2,
            ),
        ]

    def _build_extrahiertes_argument(
        self,
        raw: LLMArgument,
        clean_text: str,
        all_norms: list[ExtractedNorm],
    ) -> ExtrahiertesArgument:
        """Convert LLM output to internal domain model with verification.

        Combines three responsibilities per argument:
            1. Verify original_zitat is a substring of clean_text (ADR-006 Layer 1).
            2. Assign norms whose position falls within the zitat range (Option Y).
            3. Generate a stable argument_id for audit-trail correlation.

        If the zitat is not found in clean_text (potential LLM hallucination),
        argument_verified is False and zitierte_normen is empty (Option K).
        The argument is still returned; downstream filtering is decoupled.

        Args:
            raw: LLM-produced argument with four semantic fields.
            clean_text: PII-masked source text for verification and lookup.
            all_norms: Precomputed norm extractions over clean_text.

        Returns:
            ExtrahiertesArgument with all six fields populated.
        """
        zitat = raw.original_zitat.strip()
        start = clean_text.find(zitat)
        is_verified = start != -1

        zitierte_normen: list[str] = []
        if is_verified:
            end = start + len(zitat)
            zitierte_normen = self._assign_norms_to_range(all_norms, start, end)

        return ExtrahiertesArgument(
            argument_id=str(uuid.uuid4()),
            argument_text=raw.argument_text,
            original_zitat=raw.original_zitat,
            catalog_id=raw.catalog_id,
            einwendungs_typ=raw.einwendungs_typ,
            zitierte_normen=zitierte_normen,
            argument_verified=is_verified,
        )

    @staticmethod
    def _assign_norms_to_range(
        all_norms: list[ExtractedNorm],
        start: int,
        end: int,
    ) -> list[str]:
        """Filter norms whose position falls within [start, end) and return canonical
        strings.

        Deduplicates by canonical form while preserving first-occurrence order.

        Args:
            all_norms: Norms extracted from the full source text.
            start: Inclusive start index of the zitat range.
            end: Exclusive end index of the zitat range.

        Returns:
            Deduplicated canonical norm strings in first-occurrence order.
        """
        seen: set[str] = set()
        result: list[str] = []
        for norm in all_norms:
            if start <= norm.start < end:
                canonical = norm.canonical()
                if canonical not in seen:
                    seen.add(canonical)
                    result.append(canonical)
        return result

    def _classify_typ(
        self,
        arguments: list[ExtrahiertesArgument],
    ) -> EinwendungsTyp:
        """Derive document-level EinwendungsTyp from per-argument types.

        TYP_2 if any argument is TYP_2, otherwise TYP_1.
        Empty argument list returns TYP_1.

        Args:
            arguments: Verified extracted arguments.

        Returns:
            Document-level EinwendungsTyp.
        """
        if any(a.einwendungs_typ == EinwendungsTyp.TYP_2 for a in arguments):
            return EinwendungsTyp.TYP_2
        return EinwendungsTyp.TYP_1
