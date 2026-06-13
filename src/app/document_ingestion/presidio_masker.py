"""Presidio-based PII masker for German Einwendung text.

Concrete PiiMasker implementation (ADR-025). Layered detection:

1. Anchor-based zone extraction (zone_extractor): names in the structurally
   fixed submitter and representative zones are extracted deterministically.
   A flat NER misses these often, because a name right after a title in a
   header line is out-of-distribution for a model trained on running text.
2. Presidio analyze over the full text: spaCy German NER for PERSON plus a
   German phone-number regex, and the built-in email and IBAN recognizers.

The two sources are merged additively: the anchor names are masked only within
the anchor zone (the header) and the signature zone (the document tail), not at
every word-boundary occurrence across the whole text, while the NER/regex spans
cover the running text. Zone restriction closes an analysis-integrity vector: a
crafted submitter line ("Einreicher: Lärmschutz Bebauungsplan") can no longer
redact those substantive words throughout the legal reasoning. This is the only
module that imports Presidio.

Masking is one-way: no placeholder-to-original mapping is kept. The original
is recoverable only from the raw store via the document_id; that store is
created owner-restricted on POSIX (0o700 / 0o600), best-effort on Windows
(ADR-010, ADR-025).

Scope (ADR-025, DATA_GOVERNANCE_STATEMENT): only identifying core attributes
are masked, namely names, phone numbers, email addresses, and IBAN. Locations,
postal codes, and case numbers are deliberately not masked. Under an
encapsulated-LLM deployment the masking serves internal data minimization, not
protection against a third-party processor.

Entity counts follow an explicit contract owned by this module: the anchor and
analyzer spans are merged into regions (overlapping spans, and whitespace
adjacent spans of the same type, join one region), and the regions are counted
per type. The count is defined by our region resolution, not by the
anonymizer's internal merge of `anonymized.items`. The same region set is then
handed to the anonymizer, so the masked text and the counts rest on one
resolution step, and the count is independent of whether the NER happened to
span a multi-token name.
"""

from __future__ import annotations

import re

import structlog
from presidio_analyzer import (
    AnalyzerEngine,
    Pattern,
    PatternRecognizer,
    RecognizerResult,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from app.document_ingestion.entities import MaskingResult
from app.document_ingestion.events import INGESTION_PII_COVERAGE_ANOMALY
from app.document_ingestion.zone_extractor import ZoneExtraction, extract_zones

_log = structlog.get_logger()

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


def _is_present_as_word(token: str, text: str) -> bool:
    """Return whether token appears as a standalone word in text.

    Mirrors the recall check in experiments/pii_evaluation/evaluate.py: word
    boundaries so a token is not matched as a substring of an unrelated longer
    word ("Stein" inside "Steinbruch" does not count), with a plain-substring
    fallback for tokens that cannot form a regex boundary (e.g. a trailing
    hyphen). Duplicated rather than imported because production code must not
    depend on the offline experiment package.

    Args:
        token: The name token to look for.
        text: The text to search.

    Returns:
        True if the token is present as a word, False otherwise.
    """
    pattern = r"\b" + re.escape(token) + r"\b"
    try:
        return re.search(pattern, text) is not None
    except re.error:
        return token in text


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

        Merges the analyzer and anchor spans into regions with our own rule
        (overlapping or whitespace-adjacent same-type spans join one region),
        counts the regions per type, then anonymizes that same region set. The
        counts are therefore defined by this method, not by the anonymizer's
        internal merge.

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
        zones = extract_zones(text)
        merged = list(results) + self._anchor_person_spans(text, zones)
        resolved = self._resolve_overlaps(merged, text)

        entity_counts: dict[str, int] = {}
        for span in resolved:
            placeholder = _ENTITY_TO_PLACEHOLDER.get(span.entity_type)
            if placeholder is None:
                continue
            label = placeholder.strip("[]")
            entity_counts[label] = entity_counts.get(label, 0) + 1

        anonymized = self._anonymizer.anonymize(
            text=text,
            analyzer_results=resolved,
            operators=self._operators,
        )

        self._verify_anchor_coverage(text, resolved, zones, entity_counts)

        return MaskingResult(text=anonymized.text, entity_counts=entity_counts)

    @staticmethod
    def _verify_anchor_coverage(
        text: str,
        resolved: list[RecognizerResult],
        zones: ZoneExtraction,
        entity_counts: dict[str, int],
    ) -> None:
        """Self-check that the anchor layer cleared the names from its zones.

        The anchor layer is deterministic: every non-stopword token of every
        extracted name is masked at every occurrence within the anchor and
        signature zones. So after masking no such token should survive as a word
        inside those zones. A survivor there is an internal contradiction (an
        offset, overlap-resolution, or anonymizer bug), not the documented
        probabilistic NER residual.

        The check is scoped to the zones, not the whole output, on purpose: with
        zone-restricted masking a name token may legitimately remain in the
        running text (a submitter common noun the masker deliberately leaves;
        ADR-025). Checking the whole output would flag that by-design survival as
        an anomaly. So the masked renderings of just the zones are inspected.

        Records nothing on its own: the NAME count in entity_counts is the
        positive coverage evidence carried into the audit. On a survivor it logs
        a stderr warning naming the masked-name and survivor counts, then
        returns. It never raises and never blocks: under the encapsulated-LLM
        model a slipped name stays inside the trust boundary, so a leak must be
        provable in the log, not fatal.

        Args:
            text: The original (unmasked) document text.
            resolved: The position-ordered, non-overlapping masking spans.
            zones: The extracted names and the anchor/signature zone spans.
            entity_counts: The per-type masked-region counts (NAME is the
                positive coverage evidence).
        """
        zone_spans = [
            z for z in (zones.anchor_zone, zones.signature_zone) if z is not None
        ]
        masked_zones = [
            PresidioMasker._render_zone(text, resolved, start, end)
            for start, end in zone_spans
        ]
        survivors = sorted(
            {
                token
                for name in zones.names
                for token in name.split()
                if token.lower() not in _NAME_STOPWORDS
                and any(_is_present_as_word(token, mz) for mz in masked_zones)
            }
        )
        if survivors:
            # Governed anomaly signal (ADR-026): the former stderr print
            # interpolated the surviving NAME tokens, leaking PII through the
            # one channel logging cannot govern. Only counts are logged now;
            # the tokens never leave the trust boundary. Processing continues
            # (encapsulated-LLM model: a slipped name stays inside it).
            _log.error(
                INGESTION_PII_COVERAGE_ANOMALY,
                survivor_count=len(survivors),
                name_regions_masked=entity_counts.get("NAME", 0),
            )

    @staticmethod
    def _render_zone(
        text: str,
        resolved: list[RecognizerResult],
        start: int,
        end: int,
    ) -> str:
        """Render text[start:end] with the resolved masking spans applied.

        Reproduces the anonymizer output for one zone so the coverage check sees
        what actually survived in the region the anchor layer owns. Works in
        original offsets: resolved is non-overlapping and position-ordered, so a
        single left-to-right pass suffices. A span straddling a zone boundary
        still contributes its placeholder (it masks part of the zone).

        Args:
            text: The original document text.
            resolved: The position-ordered, non-overlapping masking spans.
            start: Inclusive zone start offset.
            end: Exclusive zone end offset.

        Returns:
            The masked rendering of the zone substring.
        """
        pieces: list[str] = []
        cursor = start
        for span in resolved:
            if span.end <= start or span.start >= end:
                continue
            seg_start = max(span.start, start)
            if seg_start > cursor:
                pieces.append(text[cursor:seg_start])
            pieces.append(_ENTITY_TO_PLACEHOLDER.get(span.entity_type, ""))
            cursor = min(span.end, end)
        if cursor < end:
            pieces.append(text[cursor:end])
        return "".join(pieces)

    @staticmethod
    def _resolve_overlaps(
        spans: list[RecognizerResult],
        text: str,
    ) -> list[RecognizerResult]:
        """Merge spans into one span per masked region (the count unit).

        Defines the count contract: one span (hence one count) per masked
        region, where a region is a maximal run of masked characters in the
        output. Two spans join the same region when they overlap, or when the
        gap between them is whitespace only and they share an entity type. The
        merged span spans [min start, max end] and takes the entity type of its
        highest-scoring member (anchors carry 1.0, so an anchor wins the type
        against the lower-scored NER span it overlaps).

        Merging the union, rather than dropping the lower-scored span, keeps
        coverage intact: an NER span "Vorname Nachname" overlapping only the
        "Nachname" anchor still contributes its "Vorname" extent, so no name
        token leaks. Bridging a whitespace gap makes the count deterministic and
        independent of whether the NER happened to span both name tokens: a
        two-token name is one region either way, counted once per occurrence.
        The same name in a header and a signature is two regions, hence two
        counts; these regions are separated by running text, not whitespace, so
        they never merge.

        Args:
            spans: The merged analyzer and anchor spans, possibly overlapping.
            text: The full document text, used to test whether the gap between
                two spans is whitespace only.

        Returns:
            One non-overlapping span per region, ordered by position.
        """
        ordered = sorted(spans, key=lambda s: (s.start, s.end))
        regions: list[RecognizerResult] = []
        for span in ordered:
            current = regions[-1] if regions else None
            if current is not None and PresidioMasker._joins_region(
                current, span, text
            ):
                merged_type = (
                    span.entity_type
                    if span.score > current.score
                    else current.entity_type
                )
                regions[-1] = RecognizerResult(
                    entity_type=merged_type,
                    start=current.start,
                    end=max(current.end, span.end),
                    score=max(current.score, span.score),
                )
            else:
                regions.append(span)
        return regions

    @staticmethod
    def _joins_region(
        current: RecognizerResult, span: RecognizerResult, text: str
    ) -> bool:
        """Return whether span belongs to the same masked region as current.

        True when span overlaps current, or when the gap between them is
        whitespace only and both carry the same entity type (so a name split
        into two tokens by a space stays one region, but two different masked
        types side by side do not merge).

        Args:
            current: The region built so far (ordered before span by start).
            span: The next span in start order.
            text: The full document text, for inspecting the gap.

        Returns:
            True if span should extend current, False if it starts a new region.
        """
        if span.start < current.end:
            return True
        return (
            span.entity_type == current.entity_type
            and text[current.end : span.start].strip() == ""
        )

    def _anchor_person_spans(
        self, text: str, zones: ZoneExtraction
    ) -> list[RecognizerResult]:
        """Build PERSON spans for anchor names within the anchor/signature zones.

        Finds each name token's word-boundary occurrences, but keeps only those
        that fall inside the anchor zone (the header) or the signature zone (the
        document tail). Occurrences in the running text are left to NER, so a
        crafted submitter line cannot redact substantive words throughout the
        legal reasoning. Name strings are split into tokens and stopwords (und,
        c/o, Familie, ...) are dropped, so connective words are not masked.

        Args:
            text: The full document text.
            zones: The extracted names and the anchor/signature zone spans.

        Returns:
            A list of RecognizerResult PERSON spans with global positions.
        """
        zone_spans = [
            z for z in (zones.anchor_zone, zones.signature_zone) if z is not None
        ]
        if not zone_spans:
            return []
        spans: list[RecognizerResult] = []
        seen: set[tuple[int, int]] = set()
        for name in zones.names:
            for token in name.split():
                if token.lower() in _NAME_STOPWORDS:
                    continue
                for match in re.finditer(r"\b" + re.escape(token) + r"\b", text):
                    if not any(
                        start <= match.start() and match.end() <= end
                        for start, end in zone_spans
                    ):
                        continue
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
