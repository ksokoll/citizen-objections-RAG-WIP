"""Extraction spike: LLM-based argument extraction with structured output.

Tests ARGUMENT_EXTRACTION_PROMPT against gpt-4o-mini with the Bertram
Rechenzentrum document. Validates that the LLM:
1. Extracts all discrete legal arguments
2. Assigns correct catalog_ids from the predefined enum
3. Produces verifiable original_zitat values
4. Classifies einwendungs_typ correctly
"""

import json
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

BERTRAM_DOKUMENT = """
Einspruch gegen den Aufstellungsbeschluss zum vorhabenbezogenen Bebauungsplan Nr. 15
"Gewerbegebiet Starkenburg-Süd", Gemarkung Traben, Verbandsgemeinde Traben-Trarbach

Eingereicht bei: Verbandsgemeinde Traben-Trarbach, Bauamt, Rathausstraße 5, 56841 Traben-Trarbach
Datum: 14.11.2024
Einreicher: Dr. Klaus Bertram, Rechtsanwalt, handelnd im eigenen Namen
Moselblick 12, 56841 Traben-Trarbach

--- EINSPRUCH / STELLUNGNAHME IM RAHMEN DER ÖFFENTLICHEN AUSLEGUNG ---

I. SACHVERHALT

Gegenstand dieser Stellungnahme ist der vorhabenbezogene Bebauungsplan Nr. 15 "Gewerbegebiet Starkenburg-Süd" der Verbandsgemeinde Traben-Trarbach in seiner ausgelegten Fassung vom Oktober 2024. Das Vorhaben sieht die Genehmigung eines Hyperscale-Rechenzentrums der NordCore Digital GmbH (Tochtergesellschaft der Hyperion Data Infrastructure SE, Düsseldorf) auf den Flurstücken 112/3, 113, 114/1 und 118/2 der Flur 4, Gemarkung Traben, vor. Die geplante Nutzfläche beträgt laut Begründung zum Bebauungsplan insgesamt 8,4 Hektar in zwei Phasen.

II. PLANUNGSRECHTLICHE BEDENKEN

1. Widerspruch zum Flächennutzungsplan

Der wirksame Flächennutzungsplan der Verbandsgemeinde Traben-Trarbach weist die betroffenen Flächen als Flächen für die Landwirtschaft sowie als Grünfläche mit Pufferzone zum Weinbaugebiet aus. Ein vorhabenbezogener Bebauungsplan, der von dieser Darstellung des Flächennutzungsplans abweicht, ist nach § 8 Abs. 2 BauGB grundsätzlich nur zulässig, wenn der Flächennutzungsplan gleichzeitig oder vorab entsprechend geändert wird (Parallelverfahren). Aus den ausgelegten Unterlagen ist nicht ersichtlich, dass ein solches Parallelverfahren ordnungsgemäß eingeleitet wurde. Die vorliegenden Unterlagen enthalten keinen Änderungsbeschluss zum Flächennutzungsplan.

2. Fehlerhafte Abwägung nach § 1 Abs. 7 BauGB

Die Begründung zum Bebauungsplan enthält keine hinreichende Auseinandersetzung mit den betroffenen Belangen des Weinbaus, des Fremdenverkehrs und der natürlichen Eigenart der Landschaft im Sinne von § 1 Abs. 6 Nr. 8 BauGB. Insbesondere fehlt eine Bewertung der Auswirkungen des Vorhabens auf die angrenzenden Weinbauflächen der Gemarkung Traben und auf den touristischen Charakter der Gemeinde als anerkannter Weinort.

3. Fehlende Ausweisung als Sondergebiet

Ein Hyperscale-Rechenzentrum mit einer Netzanschlussleistung von 120 MW ist typologisch kein klassisches Gewerbe im Sinne der Baunutzungsverordnung (BauNVO). Es handelt sich um eine fernmeldetechnische Anlage im Sinne des § 14 Abs. 2 BauNVO bzw. um eine Anlage mit erheblichen Auswirkungen, die eine Ausweisung als Sondergebiet gemäß § 11 BauNVO erforderlich macht. Die geplante Festsetzung als Gewerbegebiet GE erscheint daher planungsrechtlich unzulänglich.

III. ANTRAG

Es wird beantragt:
1. Das Planungsverfahren bis zur Vorlage einer ordnungsgemäßen Änderung des Flächennutzungsplans auszusetzen.
2. Die Begründung zum Bebauungsplan um eine vollständige Abwägung der touristischen und weinbaulichen Belange zu ergänzen.
3. Die Gebietsfestsetzung auf ihre Vereinbarkeit mit § 11 BauNVO hin zu überprüfen.

Dr. Klaus Bertram
Rechtsanwalt
"""

from pydantic import BaseModel
from app.triage.catalog import KATALOG, CatalogId


# ---------------------------------------------------------------------------
# Extraction Schema (Pydantic für OpenAI structured output)
# ---------------------------------------------------------------------------

class ExtrahiertesArgumentSchema(BaseModel):
    argument_text: str
    original_zitat: str
    catalog_id: str | None
    einwendungs_typ: str  # "typ_1" oder "typ_2"
    zitierte_normen: list[str]


class ExtractionResult(BaseModel):
    argumente: list[ExtrahiertesArgumentSchema]


# ---------------------------------------------------------------------------
# Katalog-Beschreibungen für den Prompt
# ---------------------------------------------------------------------------

catalog_entries = "\n".join(
    f"- {entry.catalog_id}: {entry.beschreibung}"
    for entry in KATALOG.values()
)

# ---------------------------------------------------------------------------
# Prompt zusammenbauen
# ---------------------------------------------------------------------------

from app.triage.prompts import ARGUMENT_EXTRACTION_PROMPT

prompt = ARGUMENT_EXTRACTION_PROMPT.prompt.format(
    catalog_entries=catalog_entries,
    einwendung_text=BERTRAM_DOKUMENT,
)

# ---------------------------------------------------------------------------
# API-Call mit structured output
# ---------------------------------------------------------------------------

response = client.beta.chat.completions.parse(
    model="gpt-4o-mini",
    temperature=0,
    messages=[{"role": "user", "content": prompt}],
    response_format=ExtractionResult
)

result = response.choices[0].message.parsed

print(f"Extrahierte Argumente: {len(result.argumente)}\n")
for i, arg in enumerate(result.argumente, start=1):
    print(f"Argument {i}:")
    print(f"  catalog_id:      {arg.catalog_id}")
    print(f"  einwendungs_typ: {arg.einwendungs_typ}")
    print(f"  argument_text:   {arg.argument_text}")
    print(f"  zitierte_normen: {arg.zitierte_normen}")
    print(f"  original_zitat:  {repr(arg.original_zitat[:100])}")
    
    verified = arg.original_zitat.strip() in BERTRAM_DOKUMENT
    print(f"  verified:        {verified}")
    print()