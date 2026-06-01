"""Markdown renderer for the Briefing context.

Infrastructure-layer concern: turns a WuerdigungsBriefing domain object
into a human-readable Markdown document for the Sachbearbeiter. Kept
separate from the domain so the data model and its presentation evolve
independently and the assembly logic stays free of formatting concerns.
"""

from __future__ import annotations

from app.briefing.entities import (
    BriefingStatus,
    WuerdigungsBriefing,
)

_STATUS_LABEL: dict[BriefingStatus, str] = {
    BriefingStatus.BRIEFING_READY: "Bereit zur Würdigung",
    BriefingStatus.NORM_UNRESOLVED: "Norm nicht aufgelöst",
    BriefingStatus.KEIN_TREFFER: "Kein Katalogtreffer",
}


def render_briefing(briefing: WuerdigungsBriefing) -> str:
    """Render a briefing to a Markdown document.

    Args:
        briefing: The assembled briefing.

    Returns:
        A Markdown string with one section per argument, the resolved
        norm text inline, and the document-level limitation note.
    """
    lines: list[str] = []
    lines.append(f"# Würdigungs-Briefing: {briefing.document_id}")
    lines.append("")
    lines.append(f"Einwendungstyp: {briefing.einwendungs_typ}")
    lines.append(f"Anzahl Argumente: {len(briefing.entries)}")
    lines.append("")
    lines.append(f"> {briefing.limitation_note}")
    lines.append("")

    for index, entry in enumerate(briefing.entries, start=1):
        status_label = _STATUS_LABEL.get(entry.status, entry.status.value)
        lines.append(f"## Argument {index}: {status_label}")
        lines.append("")
        lines.append(f"Katalog-ID: {entry.catalog_id or 'kein Treffer'}")
        lines.append("")
        lines.append("Argument der einwendenden Person:")
        lines.append(f"> {entry.original_zitat}")
        lines.append("")

        if entry.status == BriefingStatus.KEIN_TREFFER:
            lines.append(
                "Dieses Argument wurde keinem Katalogeintrag zugeordnet "
                "und enthält keine aufzulösende Norm."
            )
            lines.append("")
            continue

        lines.append("Einschlägige Normen:")
        lines.append("")
        for norm in entry.norms:
            if norm.resolved:
                lines.append(f"### {norm.paragraph_key}")
                lines.append("")
                lines.append(norm.source_text)
                lines.append("")
            else:
                lines.append(f"### {norm.canonical_citation} (nicht aufgelöst)")
                lines.append("")
                lines.append(
                    "Der Gesetzestext zu dieser Citation konnte nicht "
                    "aufgelöst werden. Bitte manuell prüfen."
                )
                lines.append("")

        if entry.requires_case_context:
            lines.append(
                "Hinweis: Die abschließende Abwägung erfordert den "
                "fallbezogenen Sachverhalt aus der Projektakte."
            )
            lines.append("")

    return "\n".join(lines)
