"""Contract test: loader Gesetz vocabulary matches the extractor whitelist.

The Retrieval loader derives each paragraph's `gesetz` from the statute
XML (amtabk, with jurabk fallback). The norm_extractor recognizes only the
laws in its `Gesetz` whitelist and emits that exact spelling as the suffix
of every canonical citation. The exact-match resolver joins these two: a
citation suffix is looked up against the loader's `gesetz` keys.

If the loader ever produces a `gesetz` value outside the whitelist (for
example because an XML lacks amtabk and the jurabk fallback yields
"EnWG 2005"), or the whitelist names a law whose XML is missing or spelled
differently, citations for that law silently resolve to resolved=False.
No other test exercises this cross-module agreement, so this contract
guards it directly (Beyonce rule: if you rely on it, test it).

This is a small_scale test: it reads the nine checked-in XML files, which
are part of the repository, so it runs hermetically in CI.
"""

from __future__ import annotations

from pathlib import Path

from app.retrieval.gesetz_xml_loader import load_all_gesetze
from app.triage.norm_extractor import Gesetz

# Repo-root-relative path to the checked-in statute corpus. This file lives
# at tests/small_scale/retrieval/, so the repo root is three levels up.
_XML_DIR = Path(__file__).parents[3] / "data" / "XML"


def test_loader_gesetz_vocabulary_equals_extractor_whitelist():
    # Given: the checked-in statute corpus and the extractor whitelist
    paragraphs = load_all_gesetze(_XML_DIR)
    loader_gesetze = {p.gesetz for p in paragraphs}
    whitelist = {g.value for g in Gesetz}

    # Then: the two vocabularies are exactly equal in both directions
    missing_in_loader = whitelist - loader_gesetze
    unexpected_in_loader = loader_gesetze - whitelist
    assert not missing_in_loader, (
        f"Whitelist laws with no matching loader gesetz "
        f"(XML missing or abbreviation mismatch): {sorted(missing_in_loader)}"
    )
    assert not unexpected_in_loader, (
        f"Loader produced gesetz values outside the extractor whitelist "
        f"(citations for these would silently fail to resolve): "
        f"{sorted(unexpected_in_loader)}"
    )


def test_loader_produces_paragraphs_for_every_whitelisted_law():
    # Given: the corpus loaded once
    paragraphs = load_all_gesetze(_XML_DIR)
    counts_by_gesetz: dict[str, int] = {}
    for p in paragraphs:
        counts_by_gesetz[p.gesetz] = counts_by_gesetz.get(p.gesetz, 0) + 1

    # Then: each whitelisted law has at least one loaded paragraph
    for g in Gesetz:
        count = counts_by_gesetz.get(g.value, 0)
        assert count > 0, (
            f"Whitelisted law {g.value} has no loaded paragraphs; "
            f"its XML is missing, empty, or uses a different abbreviation."
        )
