"""Deterministic norm extractor for German legal documents.

Based on the jura_regex pattern by Kiersch (https://github.com/kiersch/jura_regex,
permissive license). Whitelist-variant: only laws indexed in the corpus are
recognized. This eliminates false positives where unrelated uppercase
abbreviations would be mistaken for law names.

Used by:
- TriageService to populate ExtrahiertesArgument.zitierte_normen deterministically.
  Replaces the LLM-extracted version, which carried hallucination risk per the
  empirical evidence in EVAL_RESULTS.md and the broader literature
  (Magesh et al. 2025, Stanford RegLab).
- ADR-006 Layer 2 verification to check generated Würdigung citations against
  retrieved chunks: each cited norm must be canonicalizable and match a norm
  from the retrieved chunk set.

Design rationale: norm extraction is a deterministic pattern-matching task.
LLM-based extraction adds variance and hallucination surface without semantic
benefit. The jura_regex pattern has been validated in production legal NLP
contexts and is the de facto Python standard for this task.

i.V.m. chain handling: legal citations of the form "§ X Abs. Y i.V.m. § Z GESETZ"
are common in formal Anwaltsdokumente and earlier versions of this extractor
missed them because the inner § Z fell outside the regex's filler tolerance.
The pattern now explicitly allows i.V.m. chains between the primary citation
and the closing gesetz, and a post-processing step in extract_norms emits
separate ExtractedNorm objects for the inner citations, all attributed to the
same gesetz that closes the chain.

Known limitations:
- §§-chains ("§§ 346, 437, 440 BGB") are matched as a single citation with
  only the first norm captured. Affects formal Anwaltsdokumente; mitigation
  via the `regex` library with overlapped=True is possible but adds a
  dependency. Documented as Skeleton limitation in RAG_RETRIEVAL_DECISIONS.md.
- Non-paragraph citations (TA Lärm, DIN 45680, LSG-Verordnungen, FFH-Richtlinie)
  are not captured. They require separate pattern handlers and are out of
  scope for the Skeleton corpus.
- "Absatz" written out instead of "Abs.": not captured by the current pattern.
  Add to the Abs. group if needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Gesetz(str, Enum):
    """Whitelist of laws indexed in the corpus.

    Each entry must have a corresponding XML file in data/XML/ and a
    matching catalog_id in KATALOG (ADR-016: catalog_id IS the partition
    key). Adding a law requires updating both this enum and catalog.py.

    Order in the Enum is irrelevant for matching: the regex builds an
    alternation that matches the longest applicable abbreviation, with the
    trailing (?![\\w-]) guard preventing partial matches.
    """

    BAUGB = "BauGB"
    BAUNVO = "BauNVO"
    BIMSCHG = "BImSchG"
    BNATSCHG = "BNatSchG"
    ENWG = "EnWG"
    VWGO = "VwGO"
    WASTRG = "WaStrG"
    WHG = "WHG"
    WPG = "WPG"


@dataclass(frozen=True)
class ExtractedNorm:
    """A single norm reference found in source text.

    Attributes:
        full_match: Verbatim citation text (e.g. "§ 8 Abs. 2 BauGB"), trimmed.
            For inner citations from i.V.m. chains, this is the inner span
            only (e.g. "§ 8") without the chain context or the gesetz suffix.
        gesetz: Law abbreviation from the whitelist.
        norm: Paragraph or article number, possibly with letter suffix (e.g. "305c").
        absatz: Absatz value if cited (e.g. "2", "1a"), else None.
        satz: Satz value if cited (e.g. "1"), else None.
        nummer: Nr. value if cited (e.g. "8", "2a"), else None.
        lit: Litera if cited (e.g. "a"), else None.
        start: Start index of the match in the source text.
        end: End index of the match (exclusive) in the source text.
    """

    full_match: str
    gesetz: Gesetz
    norm: str
    absatz: str | None = None
    satz: str | None = None
    nummer: str | None = None
    lit: str | None = None
    start: int = 0
    end: int = 0

    def canonical(self) -> str:
        """Canonical form for comparison and deduplication.

        Format: "§ NORM[ Abs. ABSATZ][ S. SATZ][ Nr. NUMMER][ lit. LIT] GESETZ"
        Optional components are appended only when present.

        Examples:
            "§ 8 Abs. 2 BauGB"
            "§ 1 Abs. 6 Nr. 8 BauGB"
            "§ 3 Abs. 2 S. 1 BauGB"
            "§ 214 Abs. 1 Nr. 2 BauGB"

        Returns:
            Canonical string suitable for set-based deduplication and for
            string-comparison against external canonical forms (e.g. the
            ground truth in katalog_und_zuordnung.json).
        """
        parts = [f"§ {self.norm}"]
        if self.absatz is not None:
            parts.append(f"Abs. {self.absatz}")
        if self.satz is not None:
            parts.append(f"S. {self.satz}")
        if self.nummer is not None:
            parts.append(f"Nr. {self.nummer}")
        if self.lit is not None:
            parts.append(f"lit. {self.lit}")
        parts.append(self.gesetz.value)
        return " ".join(parts)


def _build_pattern() -> re.Pattern[str]:
    """Compile the norm-extraction regex using the Gesetz whitelist.

    Pattern structure (line by line):
        1. Norm intro: § or §§, Art, or Artikel, optional period, whitespace.
        2. Norm number: digits, optionally followed by a single letter
           (e.g. "305c"). Word-boundary prevents "305cc" from matching as "305c".
        3. Absatz: optional, introduced by "Abs.", digits with optional letter.
        4. Satz: optional, introduced by "S.", digits only.
        5. Nr.: optional, introduced by "Nr.", digits with optional letter.
        6. lit.: optional, single lowercase letter introduced by "lit.".
        7. i.V.m. chains: optional, repeatable. Each chain is `i.V.m. § X
           (Abs. Y)? (S. Z)? (Nr. W)?`. The inner § N citations are not
           captured here; they are extracted in a post-processing step in
           extract_norms() so they can be attributed to the closing gesetz
           as separate ExtractedNorm objects.
        8. Up to 10 characters of filler (non-greedy) before the law abbreviation.
           The 10-character limit prevents accidental cross-citation matches.
        9. Law abbreviation: alternation over the whitelist, with a trailing
           negative lookahead to prevent partial matches (e.g. "WHG" should
           not match inside "WHGesetz").

    Returns:
        Compiled regex with re.IGNORECASE and re.VERBOSE flags.
    """
    gesetze = "|".join(re.escape(g.value) for g in Gesetz)
    pattern = rf"""
        (?P<intro>§§?|Art\.?|Artikel)\.?\s*
        (?P<norm>\d+(?:\w\b)?)\s*
        (?:Abs\.\s*(?P<absatz>\d+(?:\w\b)?))?\s*
        (?:S\.\s*(?P<satz>\d+))?\s*
        (?:Nr\.\s*(?P<nr>\d+(?:\w\b)?))?\s*
        (?:lit\.\s*(?P<lit>[a-z]))?
        (?:\s*i\.\s*V\.\s*m\.\s*§§?\s*\d+(?:\w\b)?
            (?:\s*Abs\.\s*\d+(?:\w\b)?)?
            (?:\s*S\.\s*\d+)?
            (?:\s*Nr\.\s*\d+(?:\w\b)?)?
        )*
        \s*.{{0,10}}?
        (?P<gesetz>{gesetze})(?![\w-])
    """
    return re.compile(pattern, re.IGNORECASE | re.VERBOSE)


_NORM_PATTERN: re.Pattern[str] = _build_pattern()

# Marker to detect "i.V.m." (in Verbindung mit), tolerating whitespace.
_IVM_MARKER: re.Pattern[str] = re.compile(r"i\.\s*V\.\s*m\.", re.IGNORECASE)

# Sub-pattern for extracting individual citations from inside a matched span.
# Used by _extract_ivm_inner_citations to find the secondary norms in an
# i.V.m. chain. Does not include the gesetz, because the gesetz is shared
# across all citations in the chain.
_INNER_CITATION_PATTERN: re.Pattern[str] = re.compile(
    r"""
        §§?\s*
        (?P<norm>\d+(?:\w\b)?)\s*
        (?:Abs\.\s*(?P<absatz>\d+(?:\w\b)?))?\s*
        (?:S\.\s*(?P<satz>\d+))?\s*
        (?:Nr\.\s*(?P<nr>\d+(?:\w\b)?))?\s*
        (?:lit\.\s*(?P<lit>[a-z]))?
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _resolve_gesetz(matched_string: str) -> Gesetz | None:
    """Map a case-insensitive matched law string back to the Gesetz enum.

    Args:
        matched_string: The string captured by the (?P<gesetz>...) group.

    Returns:
        The matching Gesetz enum value, or None if no case-insensitive
        match was found (defensive, should not happen given the whitelist).
    """
    needle = matched_string.lower()
    for g in Gesetz:
        if g.value.lower() == needle:
            return g
    return None


def _extract_ivm_inner_citations(
    matched_text: str,
    span_start: int,
    gesetz: Gesetz,
) -> list[ExtractedNorm]:
    """Extract secondary citations from inside an i.V.m. chain.

    Called by extract_norms when the primary match contains an i.V.m. marker.
    The primary citation has already been recorded by the caller. This
    function walks through all `§ N (Abs. M)? ...` sub-citations inside the
    matched span and emits one ExtractedNorm per secondary citation, all
    attributed to the gesetz that closes the chain.

    The first inner citation match is the primary citation itself; it is
    skipped because the caller has already recorded it.

    Args:
        matched_text: The full match text from the primary regex, including
            the i.V.m. chain and the gesetz name.
        span_start: Absolute start position of matched_text in the source text.
            Used to compute absolute positions for each inner ExtractedNorm.
        gesetz: The gesetz that closes the chain. All inner citations inherit
            this gesetz, since the i.V.m. construct shares the gesetz across
            all linked norms.

    Returns:
        List of ExtractedNorm objects for the inner citations. Empty list if
        the matched span contains no secondary citations (defensive; should
        not happen if the caller only invokes this on i.V.m.-containing spans).
    """
    inner_norms: list[ExtractedNorm] = []
    matches = list(_INNER_CITATION_PATTERN.finditer(matched_text))
    # Skip the first match: it is the primary citation, already recorded.
    for inner in matches[1:]:
        inner_norms.append(
            ExtractedNorm(
                full_match=inner.group(0).strip(),
                gesetz=gesetz,
                norm=inner.group("norm"),
                absatz=inner.group("absatz"),
                satz=inner.group("satz"),
                nummer=inner.group("nr"),
                lit=inner.group("lit"),
                start=span_start + inner.start(),
                end=span_start + inner.end(),
            )
        )
    return inner_norms


def extract_norms(text: str) -> list[ExtractedNorm]:
    """Find all norm references in the given text.

    Deterministic and hallucination-free: cannot return norms not present
    in the source text. Matches are returned in primary-match order with
    inner i.V.m. citations appended directly after their primary. Duplicates
    by canonical form are NOT removed, because positional information is
    needed for argument assignment downstream.

    Args:
        text: Source text to search (e.g. an Einwendung document or an
            original_zitat from a single ExtrahiertesArgument).

    Returns:
        List of ExtractedNorm objects. Empty list if no norms are found.
    """
    results: list[ExtractedNorm] = []
    for match in _NORM_PATTERN.finditer(text):
        gesetz_str = match.group("gesetz")
        gesetz = _resolve_gesetz(gesetz_str)
        if gesetz is None:
            continue
        results.append(
            ExtractedNorm(
                full_match=match.group(0).strip(),
                gesetz=gesetz,
                norm=match.group("norm"),
                absatz=match.group("absatz"),
                satz=match.group("satz"),
                nummer=match.group("nr"),
                lit=match.group("lit"),
                start=match.start(),
                end=match.end(),
            )
        )
        # Post-process i.V.m. chains: emit secondary citations attributed
        # to the same gesetz that closes the chain.
        matched_text = match.group(0)
        if _IVM_MARKER.search(matched_text):
            results.extend(
                _extract_ivm_inner_citations(matched_text, match.start(), gesetz)
            )
    return results


def extract_canonical_norms(text: str) -> list[str]:
    """Extract canonical norm strings from text, deduplicated, in first-occurrence
    order.

    Convenience function for populating ExtrahiertesArgument.zitierte_normen.
    Deduplication is by canonical form, so "§ 8 Abs. 2 BauGB" and
    "§8 Abs.2 BauGB" collapse to a single entry.

    Args:
        text: Source text to search (typically an original_zitat).

    Returns:
        Deduplicated list of canonical norm strings in first-occurrence order.
    """
    seen: set[str] = set()
    result: list[str] = []
    for norm in extract_norms(text):
        canonical = norm.canonical()
        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result
