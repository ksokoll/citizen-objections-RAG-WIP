"""Anchor-based extraction of submitter and representative names.

Deterministic structural extraction of personal names from the fixed zones of
a citizen objection: the submitter line ("Einreicher:", "Von:", "Einreichende
Person:") and the representative clause ("vertreten durch ..."). These zones
carry the identifying names in roughly nine of ten documents, and a flat NER
misses them often because a name right after a title in a header line is an
out-of-distribution construction for a model trained on running text
(ADR-025, PII evaluation baseline).

This module is pure regex and string handling, with no spaCy dependency, so it
can be unit-tested in isolation. extract_zones returns the extracted name
strings together with two character spans: the anchor zone (the header region
the names came from) and the signature zone (the document tail, after a
Grußformel/"gez." marker, else the last blank-line block). The PresidioMasker
masks the extracted name tokens only within those two zones, not at every
word-boundary occurrence across the whole text. This keeps a crafted submitter
line ("Einreicher: Lärmschutz Bebauungsplan") from redacting substantive words
throughout the running text, while the signature zone still covers the recurring
submitter name at the document end. NER continues to cover running-text names.

The representative clause names an organisation before "vertreten durch" (not a
person, not masked) and the natural person after it. Titles and functions
between "vertreten durch" and the name are stripped via a shared prefix set,
also reused for the direct submitter zone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Titles and functions that may precede a name and are not part of it. Shared
# between the direct submitter zone and the representative clause. Kept small
# and curated; extend from the corpus, not speculatively.
_NAME_PREFIXES: tuple[str, ...] = (
    "Dr.",
    "Prof.",
    "Dipl.-Ing. Akustik",
    "Dipl.-Ing.",
    "Dipl.-Wirtsch.-Ing.",
    "Rechtsanwalt",
    "Rechtsanwältin",
    "RA",
    "RAin",
    "Geschäftsführer",
    "Geschäftsführerin",
    "Sprecher",
    "Sprecherin",
    "den Vorsitzenden",
    "der Vorsitzende",
    "Vorsitzender",
    "Vorsitzende",
)

# Tokens that mark the text after a submitter anchor as an organisation rather
# than a person. If present before the first comma, the direct submitter zone
# is treated as an organisation and the name is taken from the representative
# clause instead.
_ORG_MARKERS: tuple[str, ...] = (
    "e.V.",
    "GmbH",
    "AöR",
    "PartGmbB",
    "Initiative",
    "Bürgerinitiative",
    "Stadtwerke",
    "Kanzlei",
    "Verband",
    "Verein",
)

_SUBMITTER_ANCHOR = re.compile(
    r"(?:Einreichende Person|Einreichende Organisation|Einreicher|Von|Name)\s*:\s*",
    re.IGNORECASE,
)
_REPRESENTATIVE_ANCHOR = re.compile(
    r"vertreten durch\s*:?\s*",
    re.IGNORECASE,
)

# Closing-formula and signature markers that open the signature zone at the
# document tail. Matched case-insensitively; both the umlaut and the ASCII
# transliteration spellings appear in the corpus (Grüßen / Gruessen). Curated;
# extend from the corpus, not speculatively.
_SIGNATURE_MARKERS: tuple[str, ...] = (
    "mit freundlichen grüßen",
    "mit freundlichen gruessen",
    "mit freundlichem gruß",
    "mit freundlichem gruss",
    "freundliche grüße",
    "freundliche gruesse",
    "mit besten grüßen",
    "mit besten gruessen",
    "hochachtungsvoll",
    "gez.",
)

# Upper length of a block still treated as part of the signature when no
# closing formula is present. A signature line ("Horst Kleinen") or a short PS
# is well under this; a reasoning paragraph is well over it, which is the
# boundary the trailing-block fallback stops at so it never sweeps the legal
# argument into the signature zone.
_MAX_SIGNATURE_BLOCK_CHARS = 120

# Hard cap on the trailing signature tail. Bounds the blast radius in the
# pathological case of a document built entirely from short blocks: the fallback
# can mask anchor-name tokens within at most this many trailing characters,
# never the whole body. Only the already-extracted submitter names are masked in
# the zone, so the residual over-masking risk is small and bounded.
_MAX_SIGNATURE_TAIL_CHARS = 250


@dataclass(frozen=True)
class ZoneExtraction:
    """Extracted names plus the spans where anchor-name masking is allowed.

    Attributes:
        names: The submitter and representative name strings (may be empty).
        anchor_zone: (start, end) character span of the header region the names
            were extracted from, or None when no name was found. Anchor-name
            masking is confined to this span (plus the signature zone), so a
            crafted submitter line cannot redact words from the running text.
        signature_zone: (start, end) character span of the document tail, or
            None when no tail could be located. The submitter name typically
            recurs here, so masking must reach it.
    """

    names: list[str]
    anchor_zone: tuple[int, int] | None
    signature_zone: tuple[int, int] | None


def _strip_prefixes(segment: str) -> str:
    """Remove leading title and function prefixes from a name segment.

    Prefixes are only stripped when they form a whole token (followed by a
    space, a period, or the end of the segment), so that a prefix like "RA"
    does not match the start of a name like "Ralf", and "Sprecher" does not
    consume the start of "Sprecherin".

    Args:
        segment: Text that may begin with one or more known prefixes.

    Returns:
        The segment with leading known prefixes removed and trimmed.
    """
    changed = True
    result = segment.strip()
    while changed:
        changed = False
        for prefix in _NAME_PREFIXES:
            if not result.lower().startswith(prefix.lower()):
                continue
            remainder = result[len(prefix) :]
            # Only strip when the prefix is a whole token: the remainder is
            # empty or starts with a non-word character (space, period, etc.).
            if remainder and (remainder[0].isalnum() or remainder[0] == "-"):
                continue
            result = remainder.lstrip(" .\t")
            changed = True
            break
    return result


def _segment_end(after: str) -> int:
    """Return the offset of the first segment delimiter in `after`, or its length.

    The name segment ends at the first comma, newline, or opening parenthesis.
    Returning the offset (not the trimmed string) lets the caller map the
    segment back to a span in the source text for the anchor zone.

    Args:
        after: Text starting just after an anchor match.

    Returns:
        The index of the earliest delimiter, or len(after) if none is present.
    """
    end = len(after)
    for delimiter in (",", "\n", "("):
        index = after.find(delimiter)
        if index != -1 and index < end:
            end = index
    return end


def _signature_zone(text: str) -> tuple[int, int] | None:
    """Locate the signature zone at the document tail.

    Prefers the last closing-formula or "gez." marker (the signature follows
    it); falls back to the last blank-line-separated block when no marker is
    present. Returns None for a single-block document with no marker, so the
    anchor layer leaves the tail to NER rather than mask a whole paragraph.

    Args:
        text: The full document text.

    Returns:
        A (start, end) span from the signature opener to the end of the text,
        or None when no tail could be located.
    """
    lowered = text.lower()
    marker_start = max(
        (lowered.rfind(marker) for marker in _SIGNATURE_MARKERS),
        default=-1,
    )
    if marker_start != -1:
        return (marker_start, len(text))
    return _trailing_short_blocks(text)


def _trailing_short_blocks(text: str) -> tuple[int, int] | None:
    """Span the trailing run of short blank-line blocks (the signature tail).

    With no closing formula the submitter name typically re-appears as a short
    signature line, sometimes followed by a short PS. Walking blocks from the
    end and stopping at the first block longer than _MAX_SIGNATURE_BLOCK_CHARS
    captures those lines while never climbing into a reasoning paragraph, so the
    zone cannot sweep the legal argument. Returns None when even the last block
    is long (a document that ends mid-reasoning, no signature), leaving the tail
    to NER.

    Args:
        text: The full document text.

    Returns:
        A (start, end) span over the trailing short blocks, or None.
    """
    stripped = text.rstrip()
    if not stripped:
        return None
    cursor = len(stripped)
    zone_start: int | None = None
    while cursor > 0:
        separator = stripped.rfind("\n\n", 0, cursor)
        block_start = separator + 2 if separator != -1 else 0
        block_len = len(stripped[block_start:cursor].strip())
        tail_len = len(stripped) - block_start
        if block_len > _MAX_SIGNATURE_BLOCK_CHARS or (
            zone_start is not None and tail_len > _MAX_SIGNATURE_TAIL_CHARS
        ):
            break
        zone_start = block_start
        if separator == -1:
            break
        cursor = separator
    if zone_start is None:
        return None
    return (zone_start, len(text))


def extract_zones(text: str) -> ZoneExtraction:
    """Extract submitter and representative names plus their masking zones.

    Returns the extracted name strings together with the anchor zone (the
    header span the names came from) and the signature zone (the document
    tail). The PresidioMasker masks the name tokens only within those zones.

    Args:
        text: The raw document text.

    Returns:
        A ZoneExtraction. names may be empty; anchor_zone is None when no name
        was found; signature_zone is None when no tail could be located.
    """
    names: list[str] = []
    anchor_starts: list[int] = []
    anchor_ends: list[int] = []

    submitter = _SUBMITTER_ANCHOR.search(text)
    if submitter is not None:
        after = text[submitter.end() :]
        end = _segment_end(after)
        segment = after[:end].strip()
        if not _contains_org_marker(segment):
            name = _strip_prefixes(segment)
            if name:
                names.append(name)
                anchor_starts.append(submitter.start())
                anchor_ends.append(submitter.end() + end)

    for rep in _REPRESENTATIVE_ANCHOR.finditer(text):
        after = text[rep.end() :]
        end = _segment_end(after)
        segment = after[:end].strip()
        name = _strip_prefixes(segment)
        if name:
            names.append(name)
            anchor_starts.append(rep.start())
            anchor_ends.append(rep.end() + end)

    anchor_zone = (min(anchor_starts), max(anchor_ends)) if anchor_starts else None
    return ZoneExtraction(
        names=names,
        anchor_zone=anchor_zone,
        signature_zone=_signature_zone(text),
    )


def _contains_org_marker(segment: str) -> bool:
    """Return whether a segment looks like an organisation name.

    Args:
        segment: The candidate segment after a submitter anchor.

    Returns:
        True if an organisation marker is present.
    """
    return any(marker in segment for marker in _ORG_MARKERS)


def extract_names(text: str) -> list[str]:
    """Extract personal names from the submitter and representative zones.

    Thin wrapper over extract_zones for callers that need only the names (the
    zone-extractor unit tests). Names are returned as whole strings (for
    example "Hildegard Schumacher"); the masker tokenises and masks them within
    the anchor and signature zones.

    Args:
        text: The raw document text.

    Returns:
        A list of extracted name strings (may be empty).
    """
    return extract_zones(text).names
