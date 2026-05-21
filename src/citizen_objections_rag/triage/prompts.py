"""Versioned prompt templates for the Triage bounded context."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PromptTemplate:
    """A versioned prompt with provenance metadata.

    Attributes:
        name: Stable identifier for the prompt.
        version: Semantic version string.
        last_modified: Date of the last content change.
        tested_models: Models the prompt has been validated against.
        description: One-line summary of the prompt's purpose.
        prompt: The full prompt text.
    """

    name: str
    version: str
    last_modified: datetime
    tested_models: tuple[str, ...]
    description: str
    prompt: str


ARGUMENT_EXTRACTION_PROMPT = PromptTemplate(
    name="triage_argument_extraction",
    version="1.0.0",
    last_modified=datetime(2026, 5, 21),
    tested_models=("claude-sonnet-4-6",),
    description=(
        "Extracts discrete legal arguments from a German Einwendung document "
        "and classifies each against the predefined catalog."
    ),
    prompt="""\
Du bist ein juristischer Analyse-Assistent für deutsche Behörden im Bereich \
Bauleitplanung und Beteiligungsverfahren.

Deine Aufgabe: Extrahiere alle diskreten Rechtsargumente aus der folgenden \
Einwendung und klassifiziere jedes Argument gegen den vordefinierten Katalog.

## Katalog (Constraint)
Du darfst ausschließlich die folgenden catalog_id-Werte verwenden:
{catalog_entries}

## Regeln
1. Extrahiere jedes eigenständige juristische Argument als separaten Eintrag.
2. `argument_text`: Normalisierter Suchtext für die Vektorsuche. Präzise, \
juristisch formuliert, max. 2 Sätze.
3. `original_zitat`: Wörtliches Zitat aus dem Einwendungstext das das Argument \
belegt. Muss exakt im Originaltext auffindbar sein.
4. `catalog_id`: Wähle den passendsten Katalogeintrag. Wenn kein Eintrag passt, \
setze null.
5. `einwendungs_typ`: "typ_2" wenn juristische Fachbegriffe oder Paragraphen \
zitiert werden, sonst "typ_1".
6. Wenn das Dokument keine juristisch verwertbaren Argumente enthält, \
gib eine leere Liste zurück.

## Ausgabeformat
Antworte ausschließlich mit einem JSON-Array. Kein erklärender Text.

```json
[
  {{
    "argument_text": "...",
    "original_zitat": "...",
    "catalog_id": "C-001",
    "einwendungs_typ": "typ_2"
  }}
]
```

## Einwendung
{einwendung_text}
""",
)
