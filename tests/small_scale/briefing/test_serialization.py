"""Behaviour tests for the briefing delivery-contract serialization.

The serialized form is what the consumer parses (ADR-028), so the tests
assert on the parsed JSON: field presence including the verification verdict,
ISO-8601 UTC datetimes, and unescaped German text.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.briefing.entities import (
    BriefingEntry,
    BriefingStatus,
    ResolvedNormEntry,
    WuerdigungsBriefing,
)
from app.briefing.serialization import to_json

_CREATED_AT = datetime(2026, 6, 12, 9, 30, tzinfo=UTC)


def _briefing(argument_verified: bool = True) -> WuerdigungsBriefing:
    """Build a one-entry briefing with the fields the tests assert on."""
    status = (
        BriefingStatus.BRIEFING_READY
        if argument_verified
        else BriefingStatus.ZITAT_NICHT_VERIFIZIERT
    )
    return WuerdigungsBriefing(
        document_id="doc-1",
        einwendungs_typ="TYP_2",
        corpus_id="corpus-id-serialization-tests",
        created_at=_CREATED_AT,
        entries=[
            BriefingEntry(
                argument_id="arg-1",
                argument_text="Die Versiegelung ist zu hoch.",
                original_zitat="Die Grundfläche wird zu stark versiegelt.",
                einwendungs_typ="TYP_2",
                catalog_id="baugb",
                argument_verified=argument_verified,
                norms=[
                    ResolvedNormEntry(
                        canonical_citation="§ 1a BauGB",
                        paragraph_key="§ 1a BauGB",
                        source_text="Mit Grund und Boden soll sparsam umgegangen "
                        "werden.",
                        resolved=True,
                    )
                ],
                status=status,
                requires_case_context=argument_verified,
            )
        ],
    )


def test_to_json_output_parses_and_carries_the_verification_verdict() -> None:
    """The serialized briefing is parseable JSON carrying argument_verified.

    Given a briefing built from an unverified argument, when it is
    serialized, then the parsed output carries argument_verified false and
    the ZITAT_NICHT_VERIFIZIERT status: the consumer can tell a verified
    quote from a potentially fabricated one (S2, ADR-028).
    """
    parsed = json.loads(to_json(_briefing(argument_verified=False)))

    entry = parsed["entries"][0]
    assert entry["argument_verified"] is False
    assert entry["status"] == "ZITAT_NICHT_VERIFIZIERT"


def test_to_json_renders_created_at_as_iso_8601_utc() -> None:
    """created_at round-trips as an ISO-8601 string with an explicit UTC offset."""
    parsed = json.loads(to_json(_briefing()))

    created_at = datetime.fromisoformat(parsed["created_at"])
    assert created_at.tzinfo is not None
    assert created_at.utcoffset().total_seconds() == 0
    assert created_at == _CREATED_AT


def test_to_json_keeps_german_text_unescaped() -> None:
    """ensure_ascii=False: umlauts and the section sign survive readable."""
    rendered = to_json(_briefing())

    assert "Grundfläche" in rendered
    assert "§ 1a BauGB" in rendered
