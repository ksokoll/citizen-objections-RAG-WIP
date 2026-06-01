"""Unit tests for the briefing Markdown renderer.

Small tests: pure string rendering, no I/O. Each test asserts on the
observable content of the rendered Markdown for one named behaviour.
"""

from __future__ import annotations

from app.briefing.entities import (
    BriefingEntry,
    BriefingStatus,
    ResolvedNormEntry,
    WuerdigungsBriefing,
)
from app.briefing.renderer import (
    render_briefing,
)


def _ready_entry() -> BriefingEntry:
    """A BRIEFING_READY entry with one resolved norm."""
    return BriefingEntry(
        argument_id="arg-1",
        argument_text="Die Versiegelung ist zu hoch.",
        original_zitat="Die Grundfläche wird zu stark versiegelt.",
        einwendungs_typ="TYP_2",
        catalog_id="CAT_VERSIEGELUNG",
        norms=[
            ResolvedNormEntry(
                canonical_citation="§ 9 Abs. 1 Nr. 1 BauGB",
                paragraph_key="§ 9 BauGB",
                source_text="(1) Im Bebauungsplan können festgesetzt werden ...",
                resolved=True,
            )
        ],
        status=BriefingStatus.BRIEFING_READY,
        requires_case_context=True,
    )


def _briefing(entries: list[BriefingEntry]) -> WuerdigungsBriefing:
    """Wrap entries in a briefing with default document metadata."""
    return WuerdigungsBriefing(
        document_id="doc-1",
        einwendungs_typ="TYP_2",
        entries=entries,
    )


def test_renders_document_id_in_heading():
    # Given: a briefing for a specific document
    briefing = _briefing([_ready_entry()])

    # When: it is rendered
    markdown = render_briefing(briefing)

    # Then: the document id appears in the top heading
    assert "# Würdigungs-Briefing: doc-1" in markdown


def test_renders_limitation_note_as_blockquote():
    # Given: any briefing
    briefing = _briefing([_ready_entry()])

    # When: it is rendered
    markdown = render_briefing(briefing)

    # Then: the case-file limitation appears as a blockquote
    assert "> Dieses Briefing ordnet" in markdown
    assert "Projektakte" in markdown


def test_renders_resolved_norm_source_text():
    # Given: a ready entry with a resolved norm
    briefing = _briefing([_ready_entry()])

    # When: it is rendered
    markdown = render_briefing(briefing)

    # Then: the resolved paragraph key and its source text are present
    assert "### § 9 BauGB" in markdown
    assert "Im Bebauungsplan können festgesetzt werden" in markdown


def test_ready_entry_shows_case_context_hint():
    # Given: a ready entry that requires case context
    briefing = _briefing([_ready_entry()])

    # When: it is rendered
    markdown = render_briefing(briefing)

    # Then: the case-context hint is shown for the entry
    assert "fallbezogenen Sachverhalt aus der Projektakte" in markdown


def test_unresolved_norm_renders_manual_check_note():
    # Given: an entry whose norm could not be resolved
    entry = BriefingEntry(
        argument_id="arg-1",
        argument_text="Lärmschutz fehlt.",
        original_zitat="Der Lärmschutz wurde nicht geprüft.",
        einwendungs_typ="TYP_2",
        catalog_id="CAT_LAERM",
        norms=[
            ResolvedNormEntry(
                canonical_citation="§ 50 BImSchG",
                paragraph_key="",
                source_text="",
                resolved=False,
            )
        ],
        status=BriefingStatus.NORM_UNRESOLVED,
        requires_case_context=False,
    )
    briefing = _briefing([entry])

    # When: it is rendered
    markdown = render_briefing(briefing)

    # Then: the unresolved citation is flagged for manual checking
    assert "§ 50 BImSchG (nicht aufgelöst)" in markdown
    assert "konnte nicht" in markdown


def test_kein_treffer_entry_states_no_norm_to_resolve():
    # Given: an entry with no catalog match
    entry = BriefingEntry(
        argument_id="arg-1",
        argument_text="Allgemeine Unzufriedenheit.",
        original_zitat="Das Projekt gefällt mir nicht.",
        einwendungs_typ="TYP_1",
        catalog_id=None,
        norms=[],
        status=BriefingStatus.KEIN_TREFFER,
        requires_case_context=False,
    )
    briefing = _briefing([entry])

    # When: it is rendered
    markdown = render_briefing(briefing)

    # Then: it states the argument had no catalog match and no norm
    assert "keinem Katalogeintrag zugeordnet" in markdown


def test_renders_one_section_per_argument():
    # Given: a briefing with three entries
    briefing = _briefing([_ready_entry(), _ready_entry(), _ready_entry()])

    # When: it is rendered
    markdown = render_briefing(briefing)

    # Then: there are three argument sections
    assert markdown.count("## Argument ") == 3
