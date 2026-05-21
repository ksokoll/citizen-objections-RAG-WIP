"""Triage bounded context service.

Extracts discrete legal arguments from a masked Einwendung text and
classifies each against the predefined catalog (ADR-002, ADR-013).
In the skeleton, the LLM call is stubbed.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from citizen_objections_rag.core.entities import ExtrahiertesArgument
from citizen_objections_rag.core.protocols import LLMClientProtocol
from citizen_objections_rag.core.results import TriageResult
from citizen_objections_rag.core.statuses import EinwendungsTyp
from citizen_objections_rag.triage.catalog import CatalogId
from citizen_objections_rag.triage.classification import classify_einwendungs_typ


class TriageService:
    """Extracts and classifies legal arguments from Einwendung text.

    Attributes:
        _llm: LLM client for argument extraction (stubbed in skeleton).
    """

    def __init__(self, llm: LLMClientProtocol) -> None:
        self._llm = llm

    def triage(self, clean_text: str) -> TriageResult:
        """Extract legal arguments and classify against catalog.

        Args:
            clean_text: PII-masked Einwendung text from DocumentIngestion.

        Returns:
            TriageResult with extracted arguments. Empty list is valid
            for TYP_1 documents with no legal arguments.

        Raises:
            TriageError: If the LLM call fails.
        """
        raw_arguments = self._extract_arguments(clean_text)
        verified_arguments = [
            self._verify_argument(arg, clean_text) for arg in raw_arguments
        ]
        einwendungs_typ = classify_einwendungs_typ(verified_arguments)
        return TriageResult(
            einwendungs_typ=einwendungs_typ,
            extracted_arguments=verified_arguments,
        )

    def _extract_arguments(self, clean_text: str) -> list[ExtrahiertesArgument]:
        """Extract legal arguments via LLM structured output.

        In the skeleton this returns hardcoded arguments. The real
        LLM call is introduced in feat/triage.

        Args:
            clean_text: PII-masked Einwendung text.

        Returns:
            List of ExtrahiertesArgument with Triage-side fields populated.
            ResponseDrafting fills remaining fields via dataclasses.replace().
        """
        # TODO(feat/triage): replace stub with real LLM structured output call
        return [
            ExtrahiertesArgument(
                argument_id=str(uuid.uuid4()),
                argument_text=(
                    "Widerspruch zum Flächennutzungsplan: Bebauungsplan nicht "
                    "ordnungsgemäß aus dem Flächennutzungsplan entwickelt."
                ),
                original_zitat=(
                    "Ein vorhabenbezogener Bebauungsplan, der von dieser "
                    "Darstellung des Flächennutzungsplans abweicht"
                ),
                catalog_id=CatalogId.C_005.value,
                einwendungs_typ=EinwendungsTyp.TYP_2,
            ),
            ExtrahiertesArgument(
                argument_id=str(uuid.uuid4()),
                argument_text=(
                    "Fehlende Bürgerbeteiligung: Öffentlichkeit nicht "
                    "frühzeitig über wesentliche Planänderungen unterrichtet."
                ),
                original_zitat=(
                    "Die Öffentlichkeit wurde über grundlegende "
                    "Planänderungen nicht frühzeitig unterrichtet"
                ),
                catalog_id=CatalogId.C_002.value,
                einwendungs_typ=EinwendungsTyp.TYP_2,
            ),
        ]

    def _verify_argument(
        self,
        argument: ExtrahiertesArgument,
        clean_text: str,
    ) -> ExtrahiertesArgument:
        """Verify original_zitat exists in source text (ADR-006 Layer 1).

        Performs a substring check to detect hallucinated arguments.
        A verified=False argument is excluded from retrieval and generation.

        Args:
            argument: Extracted argument with original_zitat to verify.
            clean_text: PII-masked source text to check against.

        Returns:
            New ExtrahiertesArgument instance with argument_verified set.
        """
        is_verified = argument.original_zitat.strip() in clean_text
        return replace(argument, argument_verified=is_verified)

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
