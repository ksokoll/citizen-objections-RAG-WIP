"""Unit tests for the LLM-facing Triage schemas (the trust boundary).

These tests pin the schema-edge guards on original_zitat: the field is the
LLM's verbatim-quote claim that ADR-006 Layer 1 verifies downstream, and a
degenerate empty or whitespace-only quote must be rejected at construction so
it cannot reach the verification site as an apparent match (str.find("")
returns 0). See the ADR-006 Layer 1 robustness note and ADR-028.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core import EinwendungsTyp
from app.triage.llm_schema import LLMArgument


def _argument_with_zitat(zitat: str) -> LLMArgument:
    """Construct an LLMArgument through full validation with the given quote."""
    return LLMArgument(
        catalog_id="baugb",
        einwendungs_typ=EinwendungsTyp.TYP_2,
        argument_text="Eine knappe rechtliche Formulierung.",
        original_zitat=zitat,
    )


def test_rejects_empty_original_zitat_at_construction() -> None:
    # Given the reproduced degenerate case: an empty quote (str.find("") == 0)
    # When an LLMArgument is constructed with it
    # Then validation rejects it at the trust boundary
    with pytest.raises(ValidationError):
        _argument_with_zitat("")


def test_rejects_whitespace_only_original_zitat_at_construction() -> None:
    # Given a quote of only whitespace, which strips to empty downstream
    # When an LLMArgument is constructed with it
    # Then validation rejects it: the blank-rejecting validator backs min_length
    with pytest.raises(ValidationError):
        _argument_with_zitat("   \t  ")


def test_accepts_a_non_empty_quote() -> None:
    # Given a substantive verbatim quote
    # When an LLMArgument is constructed with it
    # Then construction succeeds and the quote is carried unchanged
    argument = _argument_with_zitat("Der Plan verstößt gegen das Abwägungsgebot.")
    assert argument.original_zitat == "Der Plan verstößt gegen das Abwägungsgebot."
