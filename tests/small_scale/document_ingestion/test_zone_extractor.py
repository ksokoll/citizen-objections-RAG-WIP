"""Unit tests for the anchor-based zone extractor.

Pure regex, no spaCy. These tests pin the behaviour of the deterministic
name extraction from the fixed submitter and representative zones, including
regression guards for the prefix-stripping bugs found during development
(ADR-025, PII evaluation Iteration 4).
"""

from app.document_ingestion.zone_extractor import extract_names, extract_zones


class TestSubmitterZone:
    def test_should_extract_name_after_einreicher_anchor(self) -> None:
        # Given a direct submitter line
        text = "Einreicher: Werner Philipp, Im Wingert 7, 56841 Traben-Trarbach"

        # When extracting names
        names = extract_names(text)

        # Then the submitter name is extracted, address excluded
        assert names == ["Werner Philipp"]

    def test_should_extract_name_after_von_anchor(self) -> None:
        # Given a "Von:" submitter line
        text = "Von: Dieter Brons, Moselufer 22, 56841 Traben-Trarbach"

        # When extracting names
        names = extract_names(text)

        # Then the name is extracted
        assert names == ["Dieter Brons"]

    def test_should_extract_couple_as_single_string(self) -> None:
        # Given a married-couple submitter line
        text = "Einreichende Person: Ralf und Brigitte Kessler, Köln"

        # When extracting names
        names = extract_names(text)

        # Then the full couple string is returned (caller tokenises)
        assert names == ["Ralf und Brigitte Kessler"]


class TestRepresentativeZone:
    def test_should_extract_person_after_vertreten_durch(self) -> None:
        # Given an organisation represented by a natural person
        text = (
            "Einreicher: Stadtwerke Traben-Trarbach AöR, vertreten durch "
            "Geschäftsführer Hans-Dieter Volz, Bahnhofstraße 12"
        )

        # When extracting names
        names = extract_names(text)

        # Then the represented person is extracted, the organisation is not
        assert names == ["Hans-Dieter Volz"]

    def test_should_strip_multiple_title_prefixes(self) -> None:
        # Given a representative with a stacked title
        text = (
            "Einreicher: Moselwein e.V., vertreten durch Geschäftsführer "
            "Dr. Karl-Heinz Pontzen, Zeltinger Straße 6"
        )

        # When extracting names
        names = extract_names(text)

        # Then both the function and the academic title are stripped
        assert names == ["Karl-Heinz Pontzen"]

    def test_should_strip_den_vorsitzenden_prefix(self) -> None:
        # Given a representative introduced by "den Vorsitzenden"
        text = (
            "Einreichende Organisation: NABU e.V., vertreten durch den "
            "Vorsitzenden Andreas Wengler, Postfach 1204"
        )

        # When extracting names
        names = extract_names(text)

        # Then the function phrase is stripped
        assert names == ["Andreas Wengler"]

    def test_should_extract_both_submitter_and_representative(self) -> None:
        # Given a submitter couple plus a representative in parentheses
        text = (
            "Einreicher: Ingrid und Paul Nessler, Starkenburger Weg 11\n"
            "(vertreten durch: Dipl.-Ing. Akustik Thomas Reiff, "
            "Schallschutzgutachter)"
        )

        # When extracting names
        names = extract_names(text)

        # Then both the submitter and the representative are extracted
        assert names == ["Ingrid und Paul Nessler", "Thomas Reiff"]


class TestPrefixStrippingRegressionGuards:
    def test_should_not_eat_name_starting_with_prefix_letters(self) -> None:
        # Given "Ralf", whose first two letters match the "RA" title prefix
        text = "Einreichende Person: Ralf und Brigitte Kessler, Köln"

        # When extracting names
        names = extract_names(text)

        # Then "Ralf" is preserved (prefix matched only as a whole token)
        assert names == ["Ralf und Brigitte Kessler"]
        assert "Ralf" in names[0]

    def test_should_not_partially_strip_sprecherin(self) -> None:
        # Given "Sprecherin", which contains the "Sprecher" prefix
        text = (
            'Einreicher: Bürgerinitiative "Mosel", vertreten durch '
            "Sprecherin Erika Feldmann, Weinbergspfad 3"
        )

        # When extracting names
        names = extract_names(text)

        # Then "Sprecherin" is fully stripped, no "in" leaks into the name
        assert names == ["Erika Feldmann"]


class TestOrganisationHandling:
    def test_should_skip_organisation_in_direct_submitter_zone(self) -> None:
        # Given an org submitter with no represented person on the same line
        text = "Einreicher: Moselwein e.V., Zeltinger Straße 6"

        # When extracting names
        names = extract_names(text)

        # Then no name is extracted from the organisation
        assert names == []

    def test_should_return_empty_list_when_no_anchor_present(self) -> None:
        # Given text with no submitter or representative anchor
        text = "Hiermit lege ich Einspruch gegen das Bauvorhaben ein."

        # When extracting names
        names = extract_names(text)

        # Then nothing is extracted
        assert names == []


class TestZoneSpans:
    def test_should_confine_anchor_zone_to_the_header(self) -> None:
        # Given a submitter line followed by running text mentioning the
        # submitter token again as a common word
        text = (
            "Einreicher: Lärmschutz Müller, Musterweg 1\n"
            "\n"
            "Der Lärmschutz ist unzureichend gewürdigt."
        )

        # When extracting zones
        zones = extract_zones(text)

        # Then the anchor zone ends at the header, before the running text, so
        # the body occurrence of the common word is outside it
        assert zones.anchor_zone is not None
        start, end = zones.anchor_zone
        assert text[start:end].startswith("Einreicher:")
        assert "Der Lärmschutz" not in text[start:end]
        assert text.index("Der Lärmschutz") >= end

    def test_should_open_signature_zone_at_grussformel(self) -> None:
        # Given a closing formula before the signature
        text = (
            "Einreicher: Hildegard Schumacher\n\n"
            "Ich wende mich gegen den Ausbau.\n\n"
            "Mit freundlichen Grüßen\nHildegard Schumacher\n"
        )

        # When extracting zones
        zones = extract_zones(text)

        # Then the signature zone starts at the Grußformel and reaches the end
        assert zones.signature_zone is not None
        start, end = zones.signature_zone
        assert text[start:].startswith("Mit freundlichen Grüßen")
        assert end == len(text)
        assert "Hildegard Schumacher" in text[start:end]

    def test_should_cover_signature_line_before_trailing_ps(self) -> None:
        # Given no Grußformel, a signature line, then a short PS block (the
        # regression that the last-block-only fallback missed). The reasoning
        # paragraph above the signature is long, as real ones are, so the climb
        # stops there.
        text = (
            "Von: Horst Kleinen, Bergstraße 8\n\n"
            "Ich fordere mindestens ein unabhängiges Schallgutachten, das für "
            "alle betroffenen Anwohner jederzeit einsehbar ist, bevor das "
            "Vorhaben überhaupt weiter geplant werden darf.\n\n"
            "Horst Kleinen\n\n"
            "PS: Auch meine Frau Gretel Kleinen ist dagegen.\n"
        )

        # When extracting zones
        zones = extract_zones(text)

        # Then the signature zone reaches up to the signature line, covering
        # both the name line and the PS, but not the reasoning paragraph
        assert zones.signature_zone is not None
        start, _ = zones.signature_zone
        assert text[start:].startswith("Horst Kleinen")
        assert "Schallgutachten" not in text[start:]

    def test_should_not_open_signature_zone_when_ending_mid_reasoning(
        self,
    ) -> None:
        # Given a document whose final block is a long reasoning paragraph
        text = (
            "Einreicher: Klaus Bertram\n\n"
            "Der vorgelegte Bebauungsplan missachtet den gebotenen Lärmschutz "
            "der Anwohner und ist in der ausgelegten Fassung nicht "
            "genehmigungsfähig, weshalb wir umfassende Einwendung erheben."
        )

        # When extracting zones
        zones = extract_zones(text)

        # Then no signature zone is opened, leaving the tail to NER rather than
        # masking anchor tokens inside the legal argument
        assert zones.signature_zone is None
