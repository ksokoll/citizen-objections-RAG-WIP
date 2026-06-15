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
"""

from __future__ import annotations

import uuid

import structlog

from app.core.entities import ExtrahiertesArgument
from app.core.failures import LLMError, LLMParseError, TriageError
from app.core.protocols import LLMClientProtocol
from app.core.results import TriageResult
from app.observability.tracing import traced

from .catalog import KATALOG
from .classification import classify_einwendungs_typ
from .events import TRIAGE_CONTRADICTION_DETECTED, TRIAGE_SUBSTANCE_THRESHOLD
from .llm_schema import LLMArgument, LLMTriageOutput
from .norm_extractor import ExtractedNorm, extract_norms
from .prompts import ARGUMENT_EXTRACTION_PROMPT, neutralize_fence_markers

_log = structlog.get_logger()

#: Character count above which an empty argument list is a review signal in its
#: own right, regardless of cited norms (H2). Roughly a paragraph of prose: a
#: genuine substantive objection clears it easily, while a one-line "Ich bin
#: dagegen." stays below it. This is a review trigger, not a gate, so the exact
#: value is a tuning parameter (the threshold event records the tripping length
#: so it can be tuned against real data), not a correctness boundary.
SUBSTANCE_THRESHOLD_CHARS: int = 500


class TriageService:
    """Extracts and classifies legal arguments from Einwendung text.

    Attributes:
        _llm: LLM client for argument extraction (stubbed in skeleton).
    """

    def __init__(self, llm: LLMClientProtocol) -> None:
        self._llm = llm

    @traced(stage="triage")
    def triage(self, clean_text: str) -> TriageResult:
        """Extract legal arguments and classify against catalog.

        Pipeline:
            1. LLM extraction returns four-field LLMArgument objects.
            2. Single pass of deterministic norm extraction over clean_text.
            3. Contradiction check: norms present but no arguments (S3).
            4. Substance backstop: substantial length but no arguments (H2).
            5. Per argument: verify original_zitat and assign norms positionally.
            6. Derive document-level EinwendungsTyp from per-argument types.

        Args:
            clean_text: PII-masked Einwendung text from DocumentIngestion.

        Returns:
            TriageResult with extracted arguments, document-level
            EinwendungsTyp, the contradiction flag, and the substance-threshold
            flag. Empty argument list is valid for TYP_1 documents with no legal
            arguments.

        Raises:
            TriageError: If the LLM call fails or its output does not parse.
        """
        try:
            raw_arguments = self._extract_arguments(clean_text)
        except (LLMError, LLMParseError) as exc:
            # Context boundary translation (S1): the infrastructure failure
            # class never leaves this context. The TriageError message carries
            # the failure type only; the provider message may interpolate
            # input fragments and travels solely on the chained cause, where
            # the logging chain reduces it to type plus location (ADR-026).
            raise TriageError(
                f"LLM argument extraction failed: {type(exc).__name__}"
            ) from exc
        all_norms = extract_norms(clean_text)
        # Deterministic contradiction check (S3): a document that cites norms
        # has legal substance by the prompt's own Vorpruefung definition, so
        # an empty LLM argument list contradicts the deterministic evidence.
        # This is the observable signature of a prompt-injected suppression;
        # the document is not failed. The signal is logged here and carried to
        # the Coordinator on TriageResult, which owns the metric (it counts the
        # contradiction) and writes the TRIAGE audit payload: domain-metric
        # emission lives in exactly one layer (single-layer ownership).
        contradiction_detected = bool(all_norms) and not raw_arguments
        if contradiction_detected:
            _log.warning(TRIAGE_CONTRADICTION_DETECTED)
        # Length backstop (H2): the contradiction check above fires only when the
        # deterministic extractor found citable norms, so a substantive prose
        # objection without paragraph notation that the LLM returns as zero
        # arguments slips through it. A text over the configured character
        # threshold with no arguments is therefore a review signal in its own
        # right, independent of norms: deterministic and explainable, with no
        # lexical-density or juristic-marker heuristic (scheinpräzision in what is
        # only a review trigger). The two signals are independent and may both
        # fire on the same document; each carries distinct evidence (cited norms
        # vs. substantial length). The event carries the tripping length only, a
        # non-PII count; the Coordinator records the per-document flag in the
        # TRIAGE audit payload so the empty classification stays reviewable.
        substance_threshold_exceeded = (
            len(clean_text) >= SUBSTANCE_THRESHOLD_CHARS and not raw_arguments
        )
        if substance_threshold_exceeded:
            _log.warning(TRIAGE_SUBSTANCE_THRESHOLD, clean_text_length=len(clean_text))
        extracted_arguments = [
            self._build_extrahiertes_argument(raw, clean_text, all_norms)
            for raw in raw_arguments
        ]
        einwendungs_typ = classify_einwendungs_typ(extracted_arguments)
        return TriageResult(
            einwendungs_typ=einwendungs_typ,
            extracted_arguments=extracted_arguments,
            contradiction_detected=contradiction_detected,
            substance_threshold_exceeded=substance_threshold_exceeded,
        )

    def _extract_arguments(self, clean_text: str) -> list[LLMArgument]:
        """Extract legal arguments from the cleaned text via LLM.

        Uses constrained decoding to enforce the LLMTriageOutput schema.
        The catalog entries are inlined into the prompt so the LLM knows
        the allowed catalog_ids and their domain descriptions.

        Args:
            clean_text: Source text with personal headers stripped.

        Returns:
            List of LLMArgument objects. Empty list for TYP_1 documents
            where the LLM correctly identifies no legal arguments.

        Raises:
            LLMError: If the LLM provider call fails.
            LLMParseError: If the provider output does not parse into the
                schema. Both are translated into TriageError by triage(),
                the context boundary; they never leave this context.
        """
        catalog_entries = "\n".join(
            f"- {c.catalog_id}: {c.beschreibung}" for c in KATALOG.values()
        )
        # Neutralize any literal fence markers in the citizen text before it is
        # interpolated into the fence (H1): a planted end marker would otherwise
        # forge a boundary and read the text after it as instructions outside
        # the fence. A soft constraint, not a security boundary (ADR-028).
        prompt = ARGUMENT_EXTRACTION_PROMPT.prompt.format(
            catalog_entries=catalog_entries,
            einwendung_text=neutralize_fence_markers(clean_text),
        )
        output = self._llm.parse(
            prompt=prompt,
            response_format=LLMTriageOutput,
        )
        return output.argumente

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
        # str.find("") returns 0, not -1, so an empty or whitespace-only quote
        # would otherwise count as found at position 0 and pass verification
        # with no evidence at all: the cleanest fabrication case the verbatim
        # check exists to catch, and a non-adversarial one too (a model that
        # legitimately returns no quote for an argument was falsely verified).
        # ADR-006 Layer 1. Guarding find on a non-empty zitat keeps the empty
        # string from being "found"; the schema edge (llm_schema.py) rejects
        # the degenerate quote at construction and this is the backstop for any
        # path that bypasses the schema.
        start = clean_text.find(zitat) if zitat else -1
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
