"""Unit tests for the BriefingService assembly logic.

Small tests: pure assembly, no I/O, no collaborators. Each test exercises
one named behaviour through the public assemble() API and asserts on the
returned state, not on internal calls.
"""

from __future__ import annotations

from app.briefing.entities import (
    BriefingStatus,
    ResolvedNormEntry,
)
from app.briefing.service import BriefingService


def _argument(
    argument_id: str = "arg-1",
    catalog_id: str | None = "CAT_VERSIEGELUNG",
    einwendungs_typ: str = "TYP_2",
) -> dict:
    """Build an extracted-argument dict, defaulting the irrelevant fields."""
    return {
        "argument_id": argument_id,
        "argument_text": "Die Versiegelung ist zu hoch.",
        "original_zitat": "Die Grundfläche wird zu stark versiegelt.",
        "einwendungs_typ": einwendungs_typ,
        "catalog_id": catalog_id,
    }


def _resolved_norm(resolved: bool = True) -> ResolvedNormEntry:
    """Build a resolved (or unresolved) norm entry."""
    if resolved:
        return ResolvedNormEntry(
            canonical_citation="§ 9 Abs. 1 Nr. 1 BauGB",
            paragraph_key="§ 9 BauGB",
            source_text="(1) Im Bebauungsplan können festgesetzt werden ...",
            resolved=True,
        )
    return ResolvedNormEntry(
        canonical_citation="§ 50 BImSchG",
        paragraph_key="",
        source_text="",
        resolved=False,
    )


def test_argument_with_match_and_resolved_norm_is_briefing_ready():
    # Given: an argument with a catalog match and a resolved norm
    service = BriefingService()
    arguments = [_argument(argument_id="arg-1")]
    norms = {"arg-1": [_resolved_norm(resolved=True)]}

    # When: the briefing is assembled
    briefing = service.assemble("doc-1", "TYP_2", arguments, norms)

    # Then: the entry is ready and flags that case context is still needed
    entry = briefing.entries[0]
    assert entry.status == BriefingStatus.BRIEFING_READY
    assert entry.requires_case_context is True


def test_argument_with_unresolved_norm_is_norm_unresolved():
    # Given: an argument with a catalog match but an unresolved norm
    service = BriefingService()
    arguments = [_argument(argument_id="arg-1")]
    norms = {"arg-1": [_resolved_norm(resolved=False)]}

    # When: the briefing is assembled
    briefing = service.assemble("doc-1", "TYP_2", arguments, norms)

    # Then: the entry is flagged unresolved and needs no case-context flag
    entry = briefing.entries[0]
    assert entry.status == BriefingStatus.NORM_UNRESOLVED
    assert entry.requires_case_context is False


def test_argument_without_catalog_match_is_kein_treffer():
    # Given: an argument that matched no catalog entry
    service = BriefingService()
    arguments = [_argument(argument_id="arg-1", catalog_id=None)]
    norms = {"arg-1": []}

    # When: the briefing is assembled
    briefing = service.assemble("doc-1", "TYP_1", arguments, norms)

    # Then: the entry is KEIN_TREFFER and carries no norms
    entry = briefing.entries[0]
    assert entry.status == BriefingStatus.KEIN_TREFFER
    assert entry.norms == []


def test_argument_with_one_unresolved_among_resolved_is_norm_unresolved():
    # Given: an argument whose norms are partly resolved, partly not
    service = BriefingService()
    arguments = [_argument(argument_id="arg-1")]
    norms = {"arg-1": [_resolved_norm(resolved=True), _resolved_norm(resolved=False)]}

    # When: the briefing is assembled
    briefing = service.assemble("doc-1", "TYP_2", arguments, norms)

    # Then: any unresolved norm downgrades the whole entry
    assert briefing.entries[0].status == BriefingStatus.NORM_UNRESOLVED


def test_assembled_briefing_preserves_argument_order():
    # Given: three arguments in a specific order
    service = BriefingService()
    arguments = [
        _argument(argument_id="arg-1"),
        _argument(argument_id="arg-2"),
        _argument(argument_id="arg-3"),
    ]
    norms = {
        "arg-1": [_resolved_norm()],
        "arg-2": [_resolved_norm()],
        "arg-3": [_resolved_norm()],
    }

    # When: the briefing is assembled
    briefing = service.assemble("doc-1", "TYP_2", arguments, norms)

    # Then: the entries appear in the input order
    assert [e.argument_id for e in briefing.entries] == ["arg-1", "arg-2", "arg-3"]


def test_briefing_carries_document_metadata():
    # Given: a document id and type
    service = BriefingService()
    arguments = [_argument(argument_id="arg-1")]
    norms = {"arg-1": [_resolved_norm()]}

    # When: the briefing is assembled
    briefing = service.assemble("doc-42", "TYP_2", arguments, norms)

    # Then: the document-level fields are set
    assert briefing.document_id == "doc-42"
    assert briefing.einwendungs_typ == "TYP_2"


def test_briefing_includes_case_context_limitation_note():
    # Given: any assembled briefing
    service = BriefingService()
    arguments = [_argument(argument_id="arg-1")]
    norms = {"arg-1": [_resolved_norm()]}

    # When: the briefing is assembled
    briefing = service.assemble("doc-1", "TYP_2", arguments, norms)

    # Then: the limitation note names the case file as out of scope
    assert "Projektakte" in briefing.limitation_note


def test_empty_argument_list_yields_empty_briefing():
    # Given: a document with no extracted arguments
    service = BriefingService()

    # When: the briefing is assembled with no arguments
    briefing = service.assemble("doc-1", "TYP_1", [], {})

    # Then: the briefing has no entries
    assert briefing.entries == []
