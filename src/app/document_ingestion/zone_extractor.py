"""Anchor-based extraction of submitter and representative names.

Deterministic structural extraction of personal names from the fixed zones of
a citizen objection: the submitter line ("Einreicher:", "Von:", "Einreichende
Person:") and the representative clause ("vertreten durch ..."). These zones
carry the identifying names in roughly nine of ten documents, and a flat NER
misses them often because a name right after a title in a header line is an
out-of-distribution construction for a model trained on running text
(ADR-025, PII evaluation baseline).

This module is pure regex and string handling, with no spaCy dependency, so it
can be unit-tested in isolation. It returns the extracted name strings; the
PresidioMasker then masks every word-boundary occurrence of those names, which
also covers the signature (the same name string recurs there) without a
separate signature anchor.

The representative clause names an organisation before "vertreten durch" (not a
person, not masked) and the natural person after it. Titles and functions
between "vertreten durch" and the name are stripped via a shared prefix set,
also reused for the direct submitter zone.
"""

from __future__ import annotations

import re

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


def _first_segment(text: str) -> str:
    """Return the text up to the first comma, newline, or opening parenthesis.

    Args:
        text: Text starting at a name.

    Returns:
        The name segment, trimmed.
    """
    for delimiter in (",", "\n", "("):
        index = text.find(delimiter)
        if index != -1:
            text = text[:index]
    return text.strip()


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

    Args:
        text: The raw document text.

    Returns:
        A list of extracted name strings (may be empty). Names are returned as
        whole strings (for example "Hildegard Schumacher"); the caller masks
        every word-boundary occurrence, which also covers the signature.
    """
    names: list[str] = []

    submitter = _SUBMITTER_ANCHOR.search(text)
    if submitter is not None:
        after = text[submitter.end() :]
        segment = _first_segment(after)
        if not _contains_org_marker(segment):
            name = _strip_prefixes(segment)
            if name:
                names.append(name)

    for rep in _REPRESENTATIVE_ANCHOR.finditer(text):
        after = text[rep.end() :]
        segment = _first_segment(after)
        name = _strip_prefixes(segment)
        if name:
            names.append(name)

    return names
