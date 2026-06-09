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

from app.document_ingestion.presidio_masker import PresidioMasker


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
