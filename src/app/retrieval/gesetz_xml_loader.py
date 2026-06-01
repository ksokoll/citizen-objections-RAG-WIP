"""XML loader for gesetze-im-internet.de statute files.

Infrastructure-layer component for the Retrieval bounded context.
Parses the official gesetze-im-internet.de XML format (gii-norm.dtd)
into GesetzParagraph domain entities, one per real paragraph (§).

The format is a sequence of <norm> elements. Several element kinds
appear that must be distinguished:

    Frame norm: the first <norm>, carrying statute-level metadata
        (jurabk, amtabk, footnotes) with no enbez. Skipped; used only
        to extract the statute abbreviation.
    Inhaltsübersicht norm: enbez "Inhaltsübersicht" holding the table
        of contents. Skipped.
    Gliederung norms: structural markers carrying a gliederungseinheit
        (chapters, parts, sections) with empty content. Skipped.
    Paragraph norms: enbez matching "§ N" with real text content.
        These become GesetzParagraph entities.
    Anlage norms: enbez "Anlage N" (appendices). Skipped by default;
        they lack a § designation and fall outside the 9-Gesetz
        paragraph-citation scope.

Paragraph text is flattened: all text nodes under the content element
are concatenated with single spaces, discarding structural nesting.
This is sufficient for embedding and for display to the Sachbearbeiter.
Sub-paragraph (Absatz/Satz/Nummer) granularity is deliberately not
preserved; see ADR-020 for the rationale.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

from app.retrieval.entities import GesetzParagraph

# Matches a paragraph enbez such as "§ 9", "§ 9a", "§ 135a". The section
# sign may be followed by variable whitespace in the source.
_PARAGRAPH_ENBEZ_PATTERN = re.compile(r"^§\s*(\d+[a-z]?)$")

# Marker text for repealed paragraphs. These carry an enbez but no
# substantive content and are excluded from the index.
_REPEALED_MARKER = "(weggefallen)"


def _normalise_paragraph(enbez: str) -> str | None:
    """Normalise a paragraph enbez to canonical "§ N" form.

    Args:
        enbez: The raw enbez text from the XML (e.g. "§   9", "§ 9a").

    Returns:
        The normalised paragraph string (e.g. "§ 9", "§ 9a"), or None
        if the enbez is not a paragraph designation (for example an
        Anlage or a section heading).
    """
    collapsed = " ".join(enbez.split())
    match = _PARAGRAPH_ENBEZ_PATTERN.match(collapsed)
    if match is None:
        return None
    return f"§ {match.group(1)}"


def _extract_statute_abbreviation(root: ElementTree.Element) -> str:
    """Extract the statute abbreviation from the frame norm metadata.

    Prefers the amtabk (official abbreviation) element. Falls back to
    the jurabk (juristic abbreviation) when no amtabk is present, which
    occurs for some statutes.

    Args:
        root: The parsed XML root (the dokumente element).

    Returns:
        The statute abbreviation (e.g. "BauGB").

    Raises:
        ValueError: If neither amtabk nor jurabk can be found.
    """
    amtabk = root.find(".//norm/metadaten/amtabk")
    if amtabk is not None and amtabk.text:
        return amtabk.text.strip()

    jurabk = root.find(".//norm/metadaten/jurabk")
    if jurabk is not None and jurabk.text:
        return jurabk.text.strip()

    raise ValueError("No amtabk or jurabk found in statute metadata.")


def _flatten_text(element: ElementTree.Element) -> str:
    """Recursively concatenate all text content under an element.

    Walks the element tree collecting text and tail strings, joining
    them with single spaces and collapsing runs of whitespace. Structural
    nesting (Absatz, Satz, lists, tables) is discarded; only the textual
    content survives.

    Args:
        element: The element to flatten (typically the Content node).

    Returns:
        The flattened plain text, with collapsed whitespace.
    """
    parts: list[str] = []
    for text in element.itertext():
        stripped = text.strip()
        if stripped:
            parts.append(stripped)
    return " ".join(parts)


def _extract_title(metadaten: ElementTree.Element) -> str:
    """Extract the paragraph heading from the metadata, if present.

    The heading lives in the titel element under metadaten for real
    paragraph norms. Returns an empty string when absent.

    Args:
        metadaten: The metadaten element of a norm.

    Returns:
        The paragraph title, or empty string if none.
    """
    titel = metadaten.find("titel")
    if titel is not None and titel.text:
        return " ".join(titel.text.split())
    return ""


def load_gesetz(xml_path: Path) -> list[GesetzParagraph]:
    """Parse one statute XML file into its paragraph entities.

    Reads a gesetze-im-internet.de XML file, identifies the statute
    abbreviation from the frame norm, then iterates the norm elements
    extracting real paragraphs. Frame, table-of-contents, structural,
    appendix, and repealed norms are filtered out.

    Args:
        xml_path: Path to the statute XML file.

    Returns:
        A list of GesetzParagraph entities, one per real paragraph with
        substantive content, in document order.

    Raises:
        FileNotFoundError: If the XML file does not exist.
        ValueError: If the statute abbreviation cannot be determined.
        ElementTree.ParseError: If the file is not well-formed XML.
    """
    if not xml_path.exists():
        raise FileNotFoundError(f"Statute XML not found: {xml_path}")

    tree = ElementTree.parse(xml_path)
    root = tree.getroot()
    gesetz = _extract_statute_abbreviation(root)

    paragraphs: list[GesetzParagraph] = []
    for norm in root.findall("norm"):
        metadaten = norm.find("metadaten")
        if metadaten is None:
            continue

        enbez_element = metadaten.find("enbez")
        if enbez_element is None or not enbez_element.text:
            continue

        paragraph = _normalise_paragraph(enbez_element.text)
        if paragraph is None:
            continue

        content = norm.find("textdaten/text/Content")
        if content is None:
            continue

        text = _flatten_text(content)
        if not text or text == _REPEALED_MARKER:
            continue

        paragraphs.append(
            GesetzParagraph(
                gesetz=gesetz,
                paragraph=paragraph,
                canonical_key=f"{paragraph} {gesetz}",
                title=_extract_title(metadaten),
                text=text,
            )
        )

    return paragraphs


def load_all_gesetze(xml_dir: Path) -> list[GesetzParagraph]:
    """Parse every statute XML in a directory into paragraph entities.

    Args:
        xml_dir: Directory containing the statute XML files.

    Returns:
        A flat list of GesetzParagraph entities across all statutes.

    Raises:
        FileNotFoundError: If the directory does not exist.
    """
    if not xml_dir.exists():
        raise FileNotFoundError(f"Statute XML directory not found: {xml_dir}")

    all_paragraphs: list[GesetzParagraph] = []
    for xml_path in sorted(xml_dir.glob("*.xml")):
        all_paragraphs.extend(load_gesetz(xml_path))
    return all_paragraphs
