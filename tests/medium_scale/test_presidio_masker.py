"""Medium-scale tests for the real PresidioMasker (loads spaCy de_core_news_md).

These tests pin the entity_counts contract owned by PresidioMasker: one count
per masked span after the masker's own overlap resolution. They run against the
real analyzer and anonymizer, not a fake, so they validate the contract the
Fake stands in for and catch a silent shift in Presidio or model behaviour.
The masker is built once per module because constructing it loads the spaCy
model.
"""

from __future__ import annotations

import pytest
from presidio_analyzer import RecognizerResult

from app.document_ingestion.presidio_masker import PresidioMasker
from app.document_ingestion.zone_extractor import ZoneExtraction


def _person_span(text: str, substring: str) -> RecognizerResult:
    """Build a PERSON masking span over the first occurrence of substring."""
    start = text.index(substring)
    return RecognizerResult(
        entity_type="PERSON", start=start, end=start + len(substring), score=1.0
    )


@pytest.fixture(scope="module")
def masker() -> PresidioMasker:
    """Build the real PresidioMasker once (loads the spaCy model)."""
    return PresidioMasker()


class TestEntityCountContract:
    def test_should_count_one_per_span_for_name_in_header_and_signature(
        self, masker: PresidioMasker
    ) -> None:
        # Given a document with the same two-token name in the submitter
        # header and again in the signature
        text = (
            "Einreicher: Hildegard Schumacher\n"
            "\n"
            "Ich wende mich gegen den geplanten Ausbau der Bundesstraße, "
            "weil der zusaetzliche Verkehr die Anwohner unzumutbar belastet "
            "und der Hochwasserschutz am Fluss nicht beruecksichtigt wurde.\n"
            "\n"
            "Mit freundlichen Gruessen\n"
            "Hildegard Schumacher\n"
        )

        # When the real masker masks the text
        result = masker.mask(text)

        # Then the name is fully removed and counted once per masked region:
        # the header occurrence is one region and the signature occurrence is
        # another, so the count is two. The two tokens of each occurrence
        # (Hildegard Schumacher) collapse into one region (overlap or a
        # whitespace gap), so the count is independent of whether the NER span
        # happened to cover both tokens. The two occurrences never merge because
        # running text, not whitespace, separates them.
        assert "Hildegard" not in result.text
        assert "Schumacher" not in result.text
        assert result.entity_counts["NAME"] == 2


class TestCoverageSelfCheck:
    """Pins the zone-scoped coverage self-check (no spaCy; pure staticmethod).

    The check logs but never raises: under the encapsulated-LLM model a slipped
    name is tolerable, so a leak must be provable in the audit, not fatal. It is
    scoped to the anchor and signature zones, so a name token left in the
    running text by design is not flagged.
    """

    def test_should_warn_when_anchor_token_survives_in_zone(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given an anchor zone where only the first name was masked, the
        # surname surviving inside the zone
        text = "Einreicher: Klaus Bertram"
        zones = ZoneExtraction(
            names=["Klaus Bertram"],
            anchor_zone=(0, len(text)),
            signature_zone=None,
        )
        resolved = [_person_span(text, "Klaus")]

        PresidioMasker._verify_anchor_coverage(text, resolved, zones, {"NAME": 1})

        # Then a stderr anomaly names the survivor and the masked-name count
        err = capsys.readouterr().err
        assert "PII coverage anomaly" in err
        assert "Bertram" in err
        assert "1 NAME region" in err

    def test_should_not_warn_when_zone_name_fully_masked(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given a zone where the whole name region was masked
        text = "Einreicher: Klaus Bertram"
        zones = ZoneExtraction(
            names=["Klaus Bertram"],
            anchor_zone=(0, len(text)),
            signature_zone=None,
        )
        resolved = [
            RecognizerResult(
                entity_type="PERSON",
                start=text.index("Klaus"),
                end=len(text),
                score=1.0,
            )
        ]

        PresidioMasker._verify_anchor_coverage(text, resolved, zones, {"NAME": 1})

        # Then nothing is logged
        assert capsys.readouterr().err == ""

    def test_should_not_warn_for_token_left_in_running_text(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given a submitter common noun masked in the header but deliberately
        # left in the running text (outside both zones)
        text = (
            "Einreicher: Lärmschutz Müller\n\n"
            "Der Lärmschutz ist unzureichend gewürdigt."
        )
        header_end = text.index("\n")
        zones = ZoneExtraction(
            names=["Lärmschutz Müller"],
            anchor_zone=(0, header_end),
            signature_zone=None,
        )
        # Only the header occurrences are masked; the body "Lärmschutz" remains.
        resolved = [
            _person_span(text, "Lärmschutz"),
            _person_span(text, "Müller"),
        ]

        PresidioMasker._verify_anchor_coverage(text, resolved, zones, {"NAME": 1})

        # Then the running-text survival is not flagged (check is zone-scoped)
        assert capsys.readouterr().err == ""

    def test_should_ignore_stopword_tokens_in_coverage_check(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given a couple whose connective "und" is left unmasked by design
        text = "Einreichende Person: Ralf und Brigitte Kessler"
        zones = ZoneExtraction(
            names=["Ralf und Brigitte Kessler"],
            anchor_zone=(0, len(text)),
            signature_zone=None,
        )
        resolved = [
            _person_span(text, "Ralf"),
            _person_span(text, "Brigitte"),
            _person_span(text, "Kessler"),
        ]

        PresidioMasker._verify_anchor_coverage(text, resolved, zones, {"NAME": 3})

        # Then the stopword "und" is not treated as a surviving name token
        assert capsys.readouterr().err == ""


class TestZoneRestrictedAnchorMasking:
    """The anchor layer masks names in the header and signature zones only.

    A crafted submitter line must not redact substantive words from the legal
    reasoning (analysis-integrity, not privacy: the encapsulated LLM does not
    protect against it). Runs against the real masker.
    """

    def test_should_not_redact_submitter_noun_from_running_text(
        self, masker: PresidioMasker
    ) -> None:
        # Given a crafted submitter line whose "name" is two common nouns that
        # also carry the legal argument in the running text
        text = (
            "Einreicher: Lärmschutz Bebauungsplan, Musterweg 1, 12345 Ort\n"
            "\n"
            "Der vorgelegte Bebauungsplan missachtet den gebotenen Lärmschutz; "
            "der Lärmschutz der Anwohner ist unzureichend gewürdigt.\n"
            "\n"
            "Mit freundlichen Grüßen\n"
            "Anke Vogt\n"
        )

        # When the real masker masks the text
        result = masker.mask(text)

        # Then the substantive words survive in the running-text reasoning: the
        # header occurrence is masked (anchor zone), the body occurrences are
        # not (left to NER, which does not tag common nouns as persons)
        assert result.text.count("Lärmschutz") == 2
        assert result.text.count("Bebauungsplan") == 1

    def test_should_still_mask_recurring_name_in_signature(
        self, masker: PresidioMasker
    ) -> None:
        # Given a real submitter name that recurs in the signature zone
        text = (
            "Einreicher: Hildegard Schumacher\n"
            "\n"
            "Ich wende mich gegen den geplanten Ausbau der Bundesstraße.\n"
            "\n"
            "Mit freundlichen Gruessen\n"
            "Hildegard Schumacher\n"
        )

        # When the real masker masks the text
        result = masker.mask(text)

        # Then both the header and the signature occurrence are gone (the
        # signature zone keeps recall despite zone restriction)
        assert "Hildegard" not in result.text
        assert "Schumacher" not in result.text
        assert result.entity_counts["NAME"] == 2
