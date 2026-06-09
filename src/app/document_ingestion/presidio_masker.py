"""Presidio-based PII masker for German Einwendung text.

Concrete PiiMasker implementation (ADR-025). Layered detection:

1. Anchor-based zone extraction (zone_extractor): names in the structurally
   fixed submitter and representative zones are extracted deterministically.
   A flat NER misses these often, because a name right after a title in a
   header line is out-of-distribution for a model trained on running text.
2. Presidio analyze over the full text: spaCy German NER for PERSON plus a
   German phone-number regex, and the built-in email and IBAN recognizers.

The two sources are merged additively: the anchor names are masked at every
word-boundary occurrence (which also covers the signature, where the same
name recurs), and the NER/regex spans cover the running text. This is the
only module that imports Presidio.

Masking is one-way: no placeholder-to-original mapping is kept. The original
is recoverable only from the access-controlled raw store via the document_id
(ADR-010, ADR-025).

Scope (ADR-025, DATA_GOVERNANCE_STATEMENT): only identifying core attributes
are masked, namely names, phone numbers, email addresses, and IBAN. Locations,
postal codes, and case numbers are deliberately not masked. Under an
encapsulated-LLM deployment the masking serves internal data minimization, not
protection against a third-party processor.

Entity counts are derived from the anonymizer's applied items, after overlap
resolution, so overlapping detections are never double-counted.
"""

from __future__ import annotations

import re

from presidio_analyzer import (
    AnalyzerEngine,
    Pattern,
    PatternRecognizer,
    RecognizerResult,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from app.core.results import MaskingResult
from app.document_ingestion.zone_extractor import extract_names

_ENTITY_TO_PLACEHOLDER: dict[str, str] = {
    "PERSON": "[NAME]",
    "PHONE_NUMBER": "[TELEFON]",
    "EMAIL_ADDRESS": "[EMAIL]",
    "IBAN_CODE": "[IBAN]",
}

# Tokens inside an extracted name string that are not name parts and must not
# be masked on their own. Kept small and curated.
_NAME_STOPWORDS: frozenset[str] = frozenset(
    {"und", "c/o", "familie", "von", "der", "die", "das"}
)

_PHONE_PATTERN = Pattern(
    name="de_phone",
    regex=r"(?<![\d-])(?:\+49|0)[\s/-]?\d(?:[\s/-]?\d){6,}",
    score=0.6,
)


class PresidioMasker:
    """Masks PII in German text using anchor extraction plus Presidio.

    The spaCy model and the Presidio engines are built once at construction
    and reused across mask() calls. Instances are stateful and meant to be
    created once and reused.

    Attributes:
        _analyzer: Presidio analyzer engine configured for German.
        _anonymizer: Presidio anonymizer engine.
        _operators: Per-entity anonymizer operators mapping to placeholders.
    """

    _SPACY_MODEL = "de_core_news_md"
    _LANGUAGE = "de"

    def __init__(self) -> None:
        self._analyzer = self._build_analyzer()
        self._anonymizer = AnonymizerEngine()
        self._operators = {
            entity: OperatorConfig("replace", {"new_value": placeholder})
            for entity, placeholder in _ENTITY_TO_PLACEHOLDER.items()
        }

    def mask(self, text: str) -> MaskingResult:
        """Replace detected PII spans with German type placeholders.

        Args:
            text: Raw text that may contain PII.

        Returns:
            MaskingResult with the masked text and per-type masked-span counts,
            keyed by placeholder label without brackets (e.g. "NAME").
        """
        results = self._analyzer.analyze(
            text=text,
            language=self._LANGUAGE,
            entities=list(_ENTITY_TO_PLACEHOLDER.keys()),
        )
        anchor_results = self._anchor_person_spans(text)
        merged = list(results) + anchor_results

        anonymized = self._anonymizer.anonymize(
            text=text,
            analyzer_results=merged,
            operators=self._operators,
        )

        entity_counts: dict[str, int] = {}
        for item in anonymized.items:
            placeholder = _ENTITY_TO_PLACEHOLDER.get(item.entity_type)
            if placeholder is None:
                continue
            label = placeholder.strip("[]")
            entity_counts[label] = entity_counts.get(label, 0) + 1

        return MaskingResult(text=anonymized.text, entity_counts=entity_counts)

    def _anchor_person_spans(self, text: str) -> list[RecognizerResult]:
        """Build PERSON spans for anchor-extracted names at all occurrences.

        Extracts names from the fixed submitter and representative zones, then
        finds every word-boundary occurrence of each name token in the text
        (covering the signature). Name strings are split into tokens and
        stopwords (und, c/o, Familie, ...) are dropped, so connective words are
        not masked on their own.

        Args:
            text: The full document text.

        Returns:
            A list of RecognizerResult PERSON spans with global positions.
        """
        spans: list[RecognizerResult] = []
        seen: set[tuple[int, int]] = set()
        for name in extract_names(text):
            for token in name.split():
                if token.lower() in _NAME_STOPWORDS:
                    continue
                for match in re.finditer(r"\b" + re.escape(token) + r"\b", text):
                    key = (match.start(), match.end())
                    if key in seen:
                        continue
                    seen.add(key)
                    spans.append(
                        RecognizerResult(
                            entity_type="PERSON",
                            start=match.start(),
                            end=match.end(),
                            score=1.0,
                        )
                    )
        return spans

    def _build_analyzer(self) -> AnalyzerEngine:
        """Build the German analyzer with built-in and custom recognizers.

        Returns:
            Configured AnalyzerEngine with the German spaCy NLP engine and the
            custom German phone-number recognizer registered.
        """
        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "de", "model_name": self._SPACY_MODEL}],
            }
        )
        analyzer = AnalyzerEngine(
            nlp_engine=provider.create_engine(),
            supported_languages=[self._LANGUAGE],
        )
        # Presidio loads a default set of recognizers. Its built-in
        # PhoneRecognizer (backed by the phonenumbers library) matches some
        # German date formats (e.g. "08.11.2024") as phone numbers. Remove it
        # so only the controlled German phone regex below decides what is a
        # phone number. The analyze() call further restricts entities to the
        # masked scope; an explicit allow-list registry would be the more
        # thorough production approach (ADR-025).
        analyzer.registry.remove_recognizer("PhoneRecognizer")
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="PHONE_NUMBER",
                supported_language=self._LANGUAGE,
                patterns=[_PHONE_PATTERN],
            )
        )
        return analyzer
