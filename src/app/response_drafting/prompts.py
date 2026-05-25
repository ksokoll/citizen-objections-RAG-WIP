from datetime import datetime

from app.core.entities import RetrievedChunk
from app.core.prompts import PromptTemplate


def format_rechtsgrundlagen(chunks: list[RetrievedChunk]) -> str:
    """Format retrieved chunks for prompt injection.

    Format per chunk: [paragraph_id] gesetz – text
    This format is parsed by the post-hoc §-reference verifier (ADR-006).

    Args:
        chunks: Retrieved norm chunks.

    Returns:
        Formatted string, one chunk per line. Empty string if no chunks.
    """
    if not chunks:
        return "Keine Rechtsgrundlagen verfügbar."
    return "\n".join(
        f"[{chunk.paragraph_id}] {chunk.gesetz} – {chunk.text}" for chunk in chunks
    )


ABWAEGUNG_PROMPT = PromptTemplate(
    name="response_drafting_abwaegung",
    version="1.0.0",
    last_modified=datetime(2026, 5, 22),
    tested_models=(),
    description=(
        "Generates a structured Abwägungsstellungnahme draft for a single "
        "extracted legal argument, grounded in retrieved legal norms."
    ),
    prompt="""\
Du bist ein juristischer Sachbearbeiter in einer deutschen Behörde im Bereich \
Bauleitplanung. Du verfasst Abwägungsstellungnahmen zu eingereichten Einwendungen.

## Aufgabe
Verfasse eine Abwägungsstellungnahme für das folgende Rechtsargument. \
Stütze dich ausschließlich auf die bereitgestellten Rechtsgrundlagen. \
Zitiere keinen Paragraphen der nicht im Kontext enthalten ist. \
Wenn keine einschlägige Norm vorhanden ist, erkläre das explizit.

## Einwendungstyp
{einwendungs_typ}

## Originalzitat aus der Einwendung
{original_zitat}

## Normalisiertes Rechtsargument (Grundlage der Würdigung)
{argument_text}

## Bereitgestellte Rechtsgrundlagen
{rechtsgrundlagen}

## Ausgabe
Verfasse ausschließlich den Würdigungstext. Kein erklärender Text davor oder danach.
Für TYP_1: sachlich, knapp, ohne juristische Fachterminologie.
Für TYP_2: juristisch präzise, mit expliziten Normreferenzen im Format [paragraph_id].
""",
)
