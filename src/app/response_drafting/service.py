"""ResponseDrafting bounded context service.

Retrieves relevant legal norms per extracted argument and generates
a structured Abwägungsstellungnahme draft. LLM and retriever are
stubbed in the skeleton.
"""

from __future__ import annotations

from dataclasses import replace

from app.core.entities import (
    Abwaegungsstellungnahme,
    ExtrahiertesArgument,
    Rechtsgrundlage,
    RetrievedChunk,
)
from app.core.failures import GenerationError, RetrievalError
from app.core.protocols import LLMClientProtocol, RetrieverProtocol
from app.core.results import TriageResult
from app.core.statuses import (
    EinwendungsTyp,
    WuerdigungsStatus,
)
from app.response_drafting.prompts import (
    ABWAEGUNG_PROMPT,
    format_rechtsgrundlagen,
)


class ResponseDraftingService:
    """Retrieves legal norms and generates Abwägungsstellungnahme drafts.

    Attributes:
        _llm: LLM client for Würdigung generation (stubbed in skeleton).
        _retriever: Retriever for legal norm chunks (stubbed in skeleton).
        _model_version: LLM model identifier for reproducibility (ADR-009).
    """

    def __init__(
        self,
        llm: LLMClientProtocol,
        retriever: RetrieverProtocol,
        model_version: str,
    ) -> None:
        self._llm = llm
        self._retriever = retriever
        self._model_version = model_version

    def draft(
        self,
        triage_result: TriageResult,
        clean_text: str,
        document_id: str,
    ) -> Abwaegungsstellungnahme:
        """Retrieve and generate per argument, aggregate into one draft.

        Args:
            triage_result: Output from TriageService with extracted arguments.
            clean_text: PII-masked Einwendung text.
            document_id: Document ID from DocumentIngestion.

        Returns:
            Abwaegungsstellungnahme in DRAFT status with all
            reproducibility fields set.

        Raises:
            RetrievalError: If the retriever fails.
            GenerationError: If the LLM call fails.
        """
        processed = [
            self._process_argument(arg, arg.einwendungs_typ)
            for arg in triage_result.extracted_arguments
            if arg.catalog_id is not None and arg.argument_verified
        ]

        return Abwaegungsstellungnahme(
            einwendungs_id=document_id,
            einwendungs_typ=triage_result.einwendungs_typ,
            model_version=self._model_version,
            prompt_version=ABWAEGUNG_PROMPT.version,
            retrieval_config_hash="skeleton-stub",
            argumente=processed,
            sachverhalt=clean_text[:500] if clean_text else None,
        )

    def _process_argument(
        self,
        argument: ExtrahiertesArgument,
        einwendungs_typ: EinwendungsTyp,
    ) -> ExtrahiertesArgument:
        """Retrieve norms and generate Würdigung for a single argument.

        Args:
            argument: Verified extracted argument with catalog_id set.
            einwendungs_typ: Document-level type for generation strategy.

        Returns:
            New ExtrahiertesArgument with retrieval and generation fields set.

        Raises:
            RetrievalError: If the retriever fails.
            GenerationError: If the LLM call fails.
        """
        chunks = self._retrieve(argument)
        rechtsgrundlagen = self._build_rechtsgrundlagen(chunks)
        wuerdigung = self._generate(argument, einwendungs_typ, chunks)

        return replace(
            argument,
            retrieval_metadata=None,  # TODO(feat/retrieval): populate
            rechtsgrundlagen=rechtsgrundlagen,
            rechtliche_wuerdigung=wuerdigung,
            wuerdigungs_status=WuerdigungsStatus.GENERIERT,
        )

    def _retrieve(self, argument: ExtrahiertesArgument) -> list[RetrievedChunk]:
        """Retrieve relevant norm chunks for an argument.

        Args:
            argument: Argument to retrieve norms for.

        Returns:
            List of retrieved chunks. Empty list in skeleton.

        Raises:
            RetrievalError: If the retriever fails.
        """
        if argument.catalog_id is None:
            raise RetrievalError(
                f"Cannot retrieve for argument {argument.argument_id} without "
                "catalog_id. Arguments with catalog_id=None should be filtered "
                "upstream."
            )
        try:
            return self._retriever.retrieve(
                query=argument.argument_text,
                partition=argument.catalog_id,
                top_k=5,
            )
        except Exception as e:
            raise RetrievalError(f"Retrieval failed: {e}") from e

    def _build_rechtsgrundlagen(
        self, chunks: list[RetrievedChunk]
    ) -> list[Rechtsgrundlage]:
        """Build Rechtsgrundlage list from retrieved chunks.

        verified is set to False by default. ADR-006 Layer 2 verification
        is introduced in feat/verification.

        Args:
            chunks: Retrieved norm chunks.

        Returns:
            List of unverified Rechtsgrundlage entries.
        """
        return [
            Rechtsgrundlage(
                paragraph=chunk.paragraph_id,
                gesetz=chunk.gesetz,
                chunk_id=chunk.chunk_id,
                verified=False,
            )
            for chunk in chunks
        ]

    def _generate(
        self,
        argument: ExtrahiertesArgument,
        einwendungs_typ: EinwendungsTyp,
        chunks: list[RetrievedChunk],
    ) -> str:
        """Generate Würdigung text via LLM.

        Args:
            argument: Argument to generate Würdigung for.
            einwendungs_typ: Document-level type for generation strategy.
            chunks: Retrieved norm chunks as context.

        Returns:
            Generated Würdigung text.

        Raises:
            GenerationError: If the LLM call fails.
        """
        prompt = ABWAEGUNG_PROMPT.prompt.format(
            einwendungs_typ=einwendungs_typ.value,
            original_zitat=argument.original_zitat,
            argument_text=argument.argument_text,
            rechtsgrundlagen=format_rechtsgrundlagen(chunks),
        )
        try:
            return self._llm.generate(
                prompt=prompt,
                system_prompt="",
            )
        except Exception as e:
            raise GenerationError(f"Generation failed: {e}") from e
