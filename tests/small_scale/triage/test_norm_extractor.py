"""Tests for the norm_extractor module.

Validates the public contract of extract_norms, extract_canonical_norms,
and ExtractedNorm.canonical(). Tests are organized by behavior, named with
"should_" prefix, and structured as Given/When/Then.
"""

from __future__ import annotations

import pytest

from app.triage.norm_extractor import (
    ExtractedNorm,
    Gesetz,
    extract_canonical_norms,
    extract_norms,
)

# -----------------------------------------------------------------------------
# Single citation extraction
# -----------------------------------------------------------------------------


class TestSingleCitationExtraction:
    """One citation in source text should yield one structured ExtractedNorm."""

    def test_should_extract_norm_with_no_optional_components(self) -> None:
        # Given a citation with only paragraph and law abbreviation
        text = "§ 8 BauGB"

        # When extracting norms
        result = extract_norms(text)

        # Then a single norm is returned with all optional components as None
        assert len(result) == 1
        assert result[0].norm == "8"
        assert result[0].gesetz == Gesetz.BAUGB
        assert result[0].absatz is None
        assert result[0].satz is None
        assert result[0].nummer is None
        assert result[0].lit is None

    def test_should_extract_absatz_when_cited_with_abs_keyword(self) -> None:
        # Given a citation with Abs. component
        text = "§ 8 Abs. 2 BauGB"

        # When extracting norms
        result = extract_norms(text)

        # Then absatz is captured as a string
        assert len(result) == 1
        assert result[0].absatz == "2"

    def test_should_extract_satz_when_cited_with_s_keyword(self) -> None:
        # Given a citation with S. component
        text = "§ 3 Abs. 2 S. 1 BauGB"

        # When extracting norms
        result = extract_norms(text)

        # Then satz is captured
        assert len(result) == 1
        assert result[0].satz == "1"

    def test_should_extract_nummer_when_cited_with_nr_keyword(self) -> None:
        # Given a citation with Nr. component
        text = "§ 1 Abs. 6 Nr. 8 BauGB"

        # When extracting norms
        result = extract_norms(text)

        # Then nummer is captured
        assert len(result) == 1
        assert result[0].nummer == "8"

    def test_should_extract_lit_when_cited_with_lit_keyword(self) -> None:
        # Given a citation with lit. component
        text = "§ 8 Abs. 2 lit. a BauGB"

        # When extracting norms
        result = extract_norms(text)

        # Then lit is captured as a lowercase letter
        assert len(result) == 1
        assert result[0].lit == "a"

    def test_should_extract_all_components_from_full_citation(self) -> None:
        # Given a citation with paragraph, absatz, satz, nummer, lit
        text = "§ 1 Abs. 6 S. 1 Nr. 8 lit. a BauGB"

        # When extracting norms
        result = extract_norms(text)

        # Then every component is populated
        assert len(result) == 1
        norm = result[0]
        assert norm.norm == "1"
        assert norm.absatz == "6"
        assert norm.satz == "1"
        assert norm.nummer == "8"
        assert norm.lit == "a"
        assert norm.gesetz == Gesetz.BAUGB

    def test_should_extract_norm_number_with_letter_suffix(self) -> None:
        # Given a paragraph with letter suffix (e.g. §13a BauGB)
        text = "§ 13a BauGB regelt Innenentwicklung"

        # When extracting norms
        result = extract_norms(text)

        # Then the suffix is preserved in the norm field
        assert len(result) == 1
        assert result[0].norm == "13a"

    def test_should_extract_three_digit_norm_number(self) -> None:
        # Given a citation with a three-digit paragraph
        text = "§ 214 Abs. 1 Nr. 2 BauGB"

        # When extracting norms
        result = extract_norms(text)

        # Then the full number is captured
        assert len(result) == 1
        assert result[0].norm == "214"

    def test_should_capture_first_norm_only_in_short_paragraph_chain(self) -> None:
        # Given a §§-chain with short filler (under 10 chars before law)
        text = "§§ 346, 437 BauGB"

        # When extracting norms
        result = extract_norms(text)

        # Then only the first norm of the chain is captured
        assert len(result) == 1
        assert result[0].norm == "346"
        assert result[0].gesetz == Gesetz.BAUGB


# -----------------------------------------------------------------------------
# Multi-citation extraction
# -----------------------------------------------------------------------------


class TestMultiCitationExtraction:
    """Multiple citations in source text should each produce a separate norm."""

    def test_should_extract_both_norms_separated_by_ivm(self) -> None:
        # Given two citations connected by i.V.m.
        text = "§ 4 Abs. 2 BauGB i.V.m. § 63 BNatSchG"

        # When extracting norms
        result = extract_norms(text)

        # Then both citations are extracted as separate norms
        assert len(result) == 2
        assert result[0].gesetz == Gesetz.BAUGB
        assert result[0].norm == "4"
        assert result[1].gesetz == Gesetz.BNATSCHG
        assert result[1].norm == "63"

    def test_should_extract_three_norms_in_compound_sentence(self) -> None:
        # Given a sentence with three citations mixing BauGB and BauNVO
        text = (
            "Die Festsetzung verstößt gegen § 8 Abs. 2 Nr. 1 BauGB "
            "sowie § 11 BauNVO und überschreitet die Grenzen des "
            "§ 1 Abs. 7 BauGB."
        )

        # When extracting norms
        result = extract_norms(text)

        # Then all three citations are extracted with correct canonical forms
        assert len(result) == 3
        assert result[0].canonical() == "§ 8 Abs. 2 Nr. 1 BauGB"
        assert result[1].canonical() == "§ 11 BauNVO"
        assert result[2].canonical() == "§ 1 Abs. 7 BauGB"

    def test_should_return_norms_in_order_of_appearance(self) -> None:
        # Given three citations in a specific text order
        text = "Erst § 5 WHG, dann § 1 BauGB, schließlich § 9 BNatSchG."

        # When extracting norms
        result = extract_norms(text)

        # Then norms are returned in the order they appear
        assert len(result) == 3
        assert result[0].gesetz == Gesetz.WHG
        assert result[1].gesetz == Gesetz.BAUGB
        assert result[2].gesetz == Gesetz.BNATSCHG

    def test_should_extract_citations_from_three_different_laws(self) -> None:
        # Given a sentence citing BauGB, BImSchG, and WHG
        text = (
            "Der Bebauungsplan verletzt § 1 Abs. 7 BauGB, das "
            "Schallgutachten genügt § 50 BImSchG nicht, und für die "
            "Gewässerbenutzung fehlt die Erlaubnis nach § 9 WHG."
        )

        # When extracting norms
        result = extract_norms(text)

        # Then all three laws are represented in the result
        assert len(result) == 3
        assert result[0].gesetz == Gesetz.BAUGB
        assert result[1].gesetz == Gesetz.BIMSCHG
        assert result[2].gesetz == Gesetz.WHG


# -----------------------------------------------------------------------------
# Whitelist enforcement
# -----------------------------------------------------------------------------


class TestWhitelistEnforcement:
    """Laws not in the Gesetz enum should not be matched, regardless of syntax."""

    def test_should_reject_citation_referencing_bgb(self) -> None:
        # Given a citation to BGB which is outside the corpus
        text = "§ 305c BGB ist Schuldrecht"

        # When extracting norms
        result = extract_norms(text)

        # Then no norm is returned
        assert result == []

    def test_should_reject_citation_referencing_gg(self) -> None:
        # Given a citation to the Grundgesetz which is outside the corpus
        text = "Art. 14 GG schützt Eigentum"

        # When extracting norms
        result = extract_norms(text)

        # Then no norm is returned
        assert result == []

    def test_should_reject_citation_referencing_stgb(self) -> None:
        # Given a citation to StGB which is outside the corpus
        text = "§ 263 StGB regelt Betrug"

        # When extracting norms
        result = extract_norms(text)

        # Then no norm is returned
        assert result == []

    def test_should_match_law_regardless_of_case_in_source(self) -> None:
        # Given a citation written entirely in lowercase
        text = "§ 8 abs. 2 baugb"

        # When extracting norms
        result = extract_norms(text)

        # Then the law is matched and returned in canonical case
        assert len(result) == 1
        assert result[0].gesetz == Gesetz.BAUGB
        assert result[0].canonical() == "§ 8 Abs. 2 BauGB"

    @pytest.mark.parametrize("gesetz", list(Gesetz))
    def test_should_match_every_law_in_the_whitelist(self, gesetz: Gesetz) -> None:
        # Given a citation referencing one of the whitelisted laws
        text = f"§ 1 {gesetz.value} verlangt etwas"

        # When extracting norms
        result = extract_norms(text)

        # Then the law is matched and the enum value is returned
        assert len(result) == 1
        assert result[0].gesetz == gesetz


# -----------------------------------------------------------------------------
# Degenerate and empty inputs
# -----------------------------------------------------------------------------


class TestDegenerateInputs:
    """Inputs without recognizable citations should produce an empty list."""

    def test_should_return_empty_list_for_empty_string(self) -> None:
        # Given an empty string
        text = ""

        # When extracting norms
        result = extract_norms(text)

        # Then an empty list is returned
        assert result == []

    def test_should_return_empty_list_for_text_without_citations(self) -> None:
        # Given a text containing no legal citations
        text = "Dies ist ein normaler Satz ohne juristische Zitate."

        # When extracting norms
        result = extract_norms(text)

        # Then an empty list is returned
        assert result == []

    def test_should_return_empty_list_for_paragraph_sign_without_law(self) -> None:
        # Given a paragraph sign without a following law abbreviation
        text = "§ 5 ist relevant"

        # When extracting norms
        result = extract_norms(text)

        # Then an empty list is returned
        assert result == []

    def test_should_return_empty_list_for_law_abbreviation_without_paragraph(
        self,
    ) -> None:
        # Given a law name without a § or Art prefix
        text = "Das BauGB regelt Vieles"

        # When extracting norms
        result = extract_norms(text)

        # Then an empty list is returned
        assert result == []


# -----------------------------------------------------------------------------
# Canonical form rendering
# -----------------------------------------------------------------------------


class TestCanonicalFormRendering:
    """ExtractedNorm.canonical() should produce strings matching ground-truth format."""

    def test_should_render_paragraph_and_law_only(self) -> None:
        # Given an ExtractedNorm with only paragraph and law
        norm = ExtractedNorm(
            full_match="§ 8 BauGB",
            gesetz=Gesetz.BAUGB,
            norm="8",
        )

        # When rendering canonical form
        canonical = norm.canonical()

        # Then only paragraph and law are present
        assert canonical == "§ 8 BauGB"

    def test_should_render_paragraph_with_absatz(self) -> None:
        # Given an ExtractedNorm with absatz
        norm = ExtractedNorm(
            full_match="§ 8 Abs. 2 BauGB",
            gesetz=Gesetz.BAUGB,
            norm="8",
            absatz="2",
        )

        # When rendering canonical form
        canonical = norm.canonical()

        # Then Abs. is included between paragraph and law
        assert canonical == "§ 8 Abs. 2 BauGB"

    def test_should_render_all_components_in_fixed_order(self) -> None:
        # Given an ExtractedNorm with every optional component
        norm = ExtractedNorm(
            full_match="§ 1 Abs. 6 S. 1 Nr. 8 lit. a BauGB",
            gesetz=Gesetz.BAUGB,
            norm="1",
            absatz="6",
            satz="1",
            nummer="8",
            lit="a",
        )

        # When rendering canonical form
        canonical = norm.canonical()

        # Then components appear in order: § norm Abs. satz S. Nr. lit. Gesetz
        assert canonical == "§ 1 Abs. 6 S. 1 Nr. 8 lit. a BauGB"

    def test_should_match_ground_truth_format_for_typical_citation(self) -> None:
        # Given a typical citation pattern from ground_truth_cleaned.json
        text = "§ 1 Abs. 6 Nr. 8 BauGB"

        # When extracting canonical norms
        result = extract_canonical_norms(text)

        # Then the canonical form matches the GT exactly
        assert result == ["§ 1 Abs. 6 Nr. 8 BauGB"]


# -----------------------------------------------------------------------------
# Deduplication
# -----------------------------------------------------------------------------


class TestDeduplicationInCanonicalExtraction:
    """extract_canonical_norms should remove duplicates while preserving order."""

    def test_should_collapse_exact_duplicate_citations(self) -> None:
        # Given a text containing the same citation twice
        text = "§ 8 Abs. 2 BauGB ist relevant. Auch § 8 Abs. 2 BauGB nochmal."

        # When extracting canonical norms
        result = extract_canonical_norms(text)

        # Then only one canonical entry is returned
        assert result == ["§ 8 Abs. 2 BauGB"]

    def test_should_collapse_whitespace_variants_to_one_canonical(self) -> None:
        # Given two stylistic variants of the same citation
        text = "§ 8 Abs. 2 BauGB und §8 Abs.2 BauGB"

        # When extracting canonical norms
        result = extract_canonical_norms(text)

        # Then both collapse to the canonical form
        assert result == ["§ 8 Abs. 2 BauGB"]

    def test_should_preserve_first_occurrence_order(self) -> None:
        # Given a text with two citations where one is repeated later
        text = "§ 11 BauNVO, dann § 8 BauGB, später § 11 BauNVO erneut."

        # When extracting canonical norms
        result = extract_canonical_norms(text)

        # Then the order reflects first occurrence and the duplicate is removed
        assert result == ["§ 11 BauNVO", "§ 8 BauGB"]

    def test_should_keep_different_absatze_as_separate_entries(self) -> None:
        # Given two citations differing only in absatz
        text = "§ 8 Abs. 1 BauGB und § 8 Abs. 2 BauGB"

        # When extracting canonical norms
        result = extract_canonical_norms(text)

        # Then both entries are kept because they are different norms
        assert result == ["§ 8 Abs. 1 BauGB", "§ 8 Abs. 2 BauGB"]


# -----------------------------------------------------------------------------
# Position tracking
# -----------------------------------------------------------------------------


class TestPositionTracking:
    """The start and end fields should support downstream argument assignment."""

    def test_should_allow_slicing_back_to_full_match_using_position(self) -> None:
        # Given a citation embedded in surrounding text
        text = "Verletzung von § 8 Abs. 2 BauGB durch die Behörde."

        # When extracting norms
        result = extract_norms(text)

        # Then text sliced with [start:end] reproduces the full match
        assert len(result) == 1
        assert text[result[0].start : result[0].end] == "§ 8 Abs. 2 BauGB"

    def test_should_return_norms_with_strictly_increasing_positions(self) -> None:
        # Given three citations at distinct positions
        text = "§ 5 WHG ist erste, § 1 BauGB ist zweite, " "§ 9 BNatSchG ist dritte."

        # When extracting norms
        result = extract_norms(text)

        # Then each subsequent norm starts after the previous one
        assert len(result) == 3
        assert result[0].start < result[1].start
        assert result[1].start < result[2].start


# -----------------------------------------------------------------------------
# Documented limitations
# -----------------------------------------------------------------------------


class TestDocumentedLimitations:
    """Lock-in tests for known pattern limitations.

    These tests assert current (intentional) behavior. If the extractor is
    later extended to address any of these, the affected test must be updated.
    """

    def test_should_not_match_long_paragraph_chain_with_filler_over_10_chars(
        self,
    ) -> None:
        # Given a §§-chain where filler between norm and law exceeds 10 chars
        text = "§§ 346, 437 Nr. 2, 440, 326 BauGB"

        # When extracting norms
        result = extract_norms(text)

        # Then no norm is extracted because the pattern requires shorter filler
        assert result == []

    def test_should_not_capture_absatz_when_spelled_out(self) -> None:
        # Given a citation using "Absatz" instead of "Abs."
        text = "§ 8 Absatz 2 BauGB"

        # When extracting norms
        result = extract_norms(text)

        # Then the paragraph and law are captured but absatz is not
        assert len(result) == 1
        assert result[0].norm == "8"
        assert result[0].absatz is None

    def test_should_not_match_ta_laerm_citation(self) -> None:
        # Given a TA Lärm reference which uses a different notation style
        text = "Die TA Lärm Nr. 6.1 wird verletzt."

        # When extracting norms
        result = extract_norms(text)

        # Then no norm is returned because TA Lärm is out of scope
        assert result == []

    def test_should_not_match_din_45680_citation(self) -> None:
        # Given a DIN standard reference
        text = "Die DIN 45680 schreibt vor: ..."

        # When extracting norms
        result = extract_norms(text)

        # Then no norm is returned because DIN standards are out of scope
        assert result == []

    def test_should_not_match_ffh_richtlinie_citation(self) -> None:
        # Given an Art. citation referencing the FFH-Richtlinie
        text = "Art. 6 Abs. 3 FFH-Richtlinie"

        # When extracting norms
        result = extract_norms(text)

        # Then no norm is returned because FFH-Richtlinie is not in the whitelist
        assert result == []


# -----------------------------------------------------------------------------
# Realistic corpus snippets
# -----------------------------------------------------------------------------


class TestRealisticCorpusSnippets:
    """Sanity checks on text resembling actual einspruch documents."""

    def test_should_extract_three_norms_from_baugb_bnatschg_compound(self) -> None:
        # Given a sentence in the style of einspruch_14
        text = (
            "Das Vorhaben verletzt § 4 Abs. 2 BauGB i.V.m. § 63 BNatSchG, "
            "weil die Erheblichkeitsschwelle des § 34 BNatSchG nicht "
            "geprüft wurde."
        )

        # When extracting canonical norms
        result = extract_canonical_norms(text)

        # Then exactly the three cited norms are returned in text order
        assert result == [
            "§ 4 Abs. 2 BauGB",
            "§ 63 BNatSchG",
            "§ 34 BNatSchG",
        ]

    def test_should_extract_three_norms_from_baunvo_baugb_compound(self) -> None:
        # Given a sentence in the style of einspruch_17
        text = (
            "Die Festsetzung als Gewerbegebiet nach § 8 BauNVO ist "
            "fehlerhaft. Erforderlich wäre ein Sondergebiet nach § 11 "
            "BauNVO. Damit liegt ein Verstoß gegen § 1 Abs. 3 BauGB vor."
        )

        # When extracting canonical norms
        result = extract_canonical_norms(text)

        # Then exactly the three cited norms are returned in text order
        assert result == ["§ 8 BauNVO", "§ 11 BauNVO", "§ 1 Abs. 3 BauGB"]

    def test_should_extract_two_norms_from_enwg_compound(self) -> None:
        # Given a sentence in the style of einspruch_19
        text = (
            "Der Netzanschluss verstößt gegen § 17 EnWG. Auch die "
            "Genehmigungsanforderungen aus § 43 EnWG sind nicht erfüllt."
        )

        # When extracting canonical norms
        result = extract_canonical_norms(text)

        # Then exactly the two cited norms are returned in text order
        assert result == ["§ 17 EnWG", "§ 43 EnWG"]

    def test_should_match_non_existent_paragraph_when_present_in_text(self) -> None:
        # Given a paragraph reference that exists syntactically but not legally
        text = "Das Gericht hat in § 999 BauGB festgestellt"

        # When extracting norms
        result = extract_norms(text)

        # Then the citation is returned because extraction is deterministic
        # over text (semantic existence checks belong to a separate layer)
        assert len(result) == 1
        assert result[0].norm == "999"
        assert result[0].gesetz == Gesetz.BAUGB


class TestIVMChainExtraction:
    """i.V.m. chain handling: both primary and inner citations extracted."""

    def test_should_extract_both_norms_from_simple_ivm_chain(self) -> None:
        text = "§ 12 i.V.m. § 30 BauGB regelt..."
        norms = extract_norms(text)
        canonicals = {n.canonical() for n in norms}
        assert "§ 12 BauGB" in canonicals
        assert "§ 30 BauGB" in canonicals

    def test_should_extract_both_norms_from_complex_ivm_chain(self) -> None:
        # Pattern from einspruch_12_mixed that motivated this feature.
        text = "gemäß § 9 Abs. 1 Nr. 1 i.V.m. § 8 WHG einer Erlaubnis"
        norms = extract_norms(text)
        canonicals = {n.canonical() for n in norms}
        assert "§ 9 Abs. 1 Nr. 1 WHG" in canonicals
        assert "§ 8 WHG" in canonicals

    def test_should_attribute_inner_citation_to_outer_gesetz(self) -> None:
        text = "§ 12 i.V.m. § 30 BauGB"
        norms = extract_norms(text)
        gesetze = {n.gesetz for n in norms}
        assert gesetze == {Gesetz.BAUGB}

    def test_should_handle_multiple_ivm_links(self) -> None:
        text = "§ 9 i.V.m. § 8 i.V.m. § 7 BauGB regelt..."
        norms = extract_norms(text)
        canonicals = {n.canonical() for n in norms}
        assert canonicals == {"§ 7 BauGB", "§ 8 BauGB", "§ 9 BauGB"}


class TestIVMChainRegressionGuards:
    """Ensure i.V.m. extension does not break simple citations."""

    def test_should_preserve_simple_citation_behavior(self) -> None:
        text = "§ 8 BauGB und § 9 WHG."
        norms = extract_norms(text)
        canonicals = {n.canonical() for n in norms}
        assert canonicals == {"§ 8 BauGB", "§ 9 WHG"}

    def test_should_not_attribute_unrelated_citations_to_first_gesetz(self) -> None:
        # Two distinct citations, no i.V.m. - must keep separate gesetze.
        text = "Verstoß gegen § 8 BauGB. Auch § 9 WHG betroffen."
        norms = extract_norms(text)
        canonicals = {n.canonical() for n in norms}
        assert "§ 8 BauGB" in canonicals
        assert "§ 9 WHG" in canonicals
