"""Contract test for the Coordinator's argument-mapping seam (ADR-028).

_map_arguments is where Triage arguments cross into the Briefing context. A
BriefingEntry field that is neither mapped at this seam nor derived by the
Briefing context itself would silently vanish from the delivery contract
(the S2 finding: the verification verdict was computed and then dropped
here). This test pins the seam's field list against BriefingEntry so a
dropped or orphaned field fails loudly instead.
"""

from __future__ import annotations

import dataclasses

from app.briefing.entities import BriefingEntry
from app.core.entities import EinwendungsTyp, ExtrahiertesArgument
from app.pipeline import Pipeline

#: BriefingEntry fields the Briefing context derives itself; everything else
#: must come through the mapping seam.
_BRIEFING_DERIVED_FIELDS = frozenset({"norms", "status", "requires_case_context"})


def test_map_arguments_field_list_covers_briefing_entry() -> None:
    """Every BriefingEntry field is either mapped at the seam or derived.

    Given one extracted argument, when it crosses the mapping seam, then the
    mapped keys are exactly the BriefingEntry fields the Briefing context
    does not derive itself: a new BriefingEntry field that nobody maps, or a
    mapped key no entry field consumes, fails this assertion.
    """
    argument = ExtrahiertesArgument(
        argument_id="arg-1",
        argument_text="Die Versiegelung ist zu hoch.",
        original_zitat="Die Grundfläche wird zu stark versiegelt.",
        catalog_id="baugb",
        einwendungs_typ=EinwendungsTyp.TYP_2,
        zitierte_normen=["§ 1a BauGB"],
        argument_verified=True,
    )

    mapped = Pipeline._map_arguments([argument])[0]

    entry_fields = {field.name for field in dataclasses.fields(BriefingEntry)}
    assert set(mapped) == entry_fields - _BRIEFING_DERIVED_FIELDS


def test_map_arguments_carries_the_verification_verdict() -> None:
    """The seam transports the verdict value, not just the key (S2).

    Given an unverified argument, when it crosses the seam, then the mapped
    dict carries argument_verified false.
    """
    argument = ExtrahiertesArgument(
        argument_id="arg-1",
        argument_text="Halluziniertes Argument",
        original_zitat="Dieser Satz steht nirgends im Dokument.",
        catalog_id="baugb",
        einwendungs_typ=EinwendungsTyp.TYP_2,
        zitierte_normen=[],
        argument_verified=False,
    )

    mapped = Pipeline._map_arguments([argument])[0]

    assert mapped["argument_verified"] is False
