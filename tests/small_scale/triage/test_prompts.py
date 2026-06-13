"""Behaviour tests for the Triage prompt template and its fence neutralization.

The citizen document travels as fenced data with a precedence rule (S3). The
fence markers are code-resident (ADR-028): the template and the neutralization
that defends them share one source of truth. neutralize_fence_markers closes the
trivial fence-forgery vector (H1) by rewriting literal markers planted in
citizen text; the fence stays a soft constraint, the nonce delimiter is backlog.
"""

from __future__ import annotations

from app.triage.prompts import (
    ARGUMENT_EXTRACTION_PROMPT,
    EINWENDUNG_ENDE_MARKER,
    EINWENDUNG_START_MARKER,
    neutralize_fence_markers,
)


def test_template_uses_the_code_resident_fence_markers() -> None:
    # Given the extraction template and the code-resident marker constants
    # When/Then the template contains both, so the constants are the single
    # source of truth the neutralization defends, not a second copy that could
    # drift from the template's literal fence
    assert EINWENDUNG_START_MARKER in ARGUMENT_EXTRACTION_PROMPT.prompt
    assert EINWENDUNG_ENDE_MARKER in ARGUMENT_EXTRACTION_PROMPT.prompt


def test_neutralize_defangs_both_markers() -> None:
    # Given citizen text that plants both literal fence markers
    text = f"vor {EINWENDUNG_START_MARKER} mitte {EINWENDUNG_ENDE_MARKER} nach"

    # When the text is neutralized
    out = neutralize_fence_markers(text)

    # Then neither triple-angle fence token survives, so neither can read as a
    # boundary, while the citizen's surrounding words stay intact
    assert EINWENDUNG_START_MARKER not in out
    assert EINWENDUNG_ENDE_MARKER not in out
    assert "vor " in out
    assert " mitte " in out
    assert " nach" in out
    # the defanged token stays legible (the citizen's intent is not erased)
    assert "EINWENDUNG_ENDE" in out


def test_neutralize_leaves_text_without_markers_unchanged() -> None:
    # Given ordinary citizen text with no fence markers
    text = "Der Plan verstößt gegen das Abwägungsgebot nach § 1 Abs. 7 BauGB."

    # When neutralized, then it is returned unchanged: the defense touches only
    # the exact fence tokens, not normal angle brackets or prose
    assert neutralize_fence_markers(text) == text
