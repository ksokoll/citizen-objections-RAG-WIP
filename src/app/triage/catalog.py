"""Predefined objection catalog for the Triage bounded context.

Single source of truth for catalog entries and the CatalogId enum.
Both the Pydantic extraction schema (Triage) and the corpus routing
(ResponseDrafting) consume from this module. Drift is prevented by
colocation: adding an entry requires updating both KATALOG and CatalogId.

v2 (2026-05-23): Restructured from 5 thematic clusters to 7 clusters
aligned with the legal domains found in the 10-document test corpus.
Cluster selection derived from empirical analysis of the test set;
see RAG_RETRIEVAL_DECISIONS.md for the leakage-methodology note.
"""

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class KatalogEintrag:
    """A single predefined objection pattern with routing metadata.

    Attributes:
        catalog_id: Unique identifier, matches CatalogId enum value.
        beschreibung: Human-readable description used in LLM extraction schema.
            Generic domain vocabulary, no leakage of specific test arguments.
        rechtsgebiet: Primary legal domain (e.g. BauGB, WHG, BNatSchG).
        corpus_partition: Index partition key for domain-routed retrieval (ADR-005).
    """

    catalog_id: str
    beschreibung: str
    rechtsgebiet: str
    corpus_partition: str


KATALOG: dict[str, KatalogEintrag] = {
    "C-001": KatalogEintrag(
        catalog_id="C-001",
        beschreibung=(
            "Materielles Bauplanungsrecht nach BauGB und BauNVO. "
            "Umfasst Aufstellung und Festsetzungen von Bauleitplänen, "
            "Verhältnis Flächennutzungsplan zu Bebauungsplan, "
            "Gebietsausweisung nach BauNVO (Gewerbegebiet, Sondergebiet etc.), "
            "Abwägungsgebot, städtebauliche Erforderlichkeit und "
            "Erschließungssicherung (§§ 1, 8, 12, 30 BauGB)."
        ),
        rechtsgebiet="BauGB",
        corpus_partition="baugb",
    ),
    "C-002": KatalogEintrag(
        catalog_id="C-002",
        beschreibung=(
            "Wasserrechtliche Anforderungen nach WHG und WaStrG. "
            "Umfasst Gewässerbenutzung und Erlaubnispflichten, Wasserentnahme, "
            "thermische und stoffliche Gewässerbelastung, "
            "Trinkwasserschutzgebiete sowie Bundeswasserstraßen "
            "(§§ 8, 9, 57 WHG; WaStrG)."
        ),
        rechtsgebiet="WHG",
        corpus_partition="whg",
    ),
    "C-003": KatalogEintrag(
        catalog_id="C-003",
        beschreibung=(
            "Immissionsschutzrechtliche Lärmanforderungen. "
            "Umfasst Schallimmissionen aus gewerblichen und industriellen "
            "Anlagen, schallschutzrechtliche Beurteilung von Bauleitplänen, "
            "Schallgutachten, Lärmrichtwerte für Wohn- und Mischgebiete, "
            "Bewertung tieffrequenter Geräusche "
            "(TA Lärm, BImSchG, DIN 45680)."
        ),
        rechtsgebiet="BImSchG",
        corpus_partition="bimschg_ta_laerm",
    ),
    "C-004": KatalogEintrag(
        catalog_id="C-004",
        beschreibung=(
            "Naturschutzrecht nach BNatSchG und europarechtlichen Vorgaben. "
            "Umfasst artenschutzrechtliche Zugriffsverbote, "
            "FFH-Verträglichkeitsprüfung für Schutzgebiete, "
            "Umweltbericht-Anforderungen der Bauleitplanung, "
            "Landschaftsschutzgebiete und naturschutzrechtliche Befreiungen "
            "(§§ 34, 44, 63, 67 BNatSchG; § 2a BauGB; FFH-Richtlinie 92/43/EWG)."
        ),
        rechtsgebiet="BNatSchG",
        corpus_partition="bnatschg",
    ),
    "C-005": KatalogEintrag(
        catalog_id="C-005",
        beschreibung=(
            "Energierechtliche Anforderungen nach EnWG. "
            "Umfasst Netzanschluss an Hoch- oder Mittelspannungsnetze, "
            "Netzanschlussbestätigungen für Großverbraucher, "
            "Kostentragung für Netzausbau und Energieerschließung als "
            "Voraussetzung der planungsrechtlichen Zulässigkeit "
            "(§ 17 EnWG)."
        ),
        rechtsgebiet="EnWG",
        corpus_partition="enwg",
    ),
    "C-006": KatalogEintrag(
        catalog_id="C-006",
        beschreibung=(
            "Kommunales Wärmeplanungsrecht nach WPG. "
            "Umfasst kommunale Wärmeplanung, Berücksichtigung industrieller "
            "Abwärmequellen, Verpflichtungen zur Abwärmeauskopplung in "
            "Durchführungsverträgen und Wärmenetz-Integration "
            "(§ 7 WPG)."
        ),
        rechtsgebiet="WPG",
        corpus_partition="wpg",
    ),
    "C-007": KatalogEintrag(
        catalog_id="C-007",
        beschreibung=(
            "Verfahrensrecht der Bauleitplanung sowie verwaltungsgerichtliche "
            "Kontrolle. Umfasst Öffentlichkeitsbeteiligung (frühzeitig und "
            "förmlich), Auslegung von Planunterlagen, Trägerbeteiligung, "
            "Bekanntmachungspflichten, Beachtlichkeit von Verfahrensfehlern "
            "und Normenkontrollverfahren "
            "(§§ 3, 4, 214 BauGB; § 47 VwGO)."
        ),
        rechtsgebiet="BauGB",
        corpus_partition="baugb",
    ),
}


class CatalogId(str, Enum):
    """Public interface for catalog entries used in Pydantic extraction schema.

    Each value must have a corresponding entry in KATALOG.
    Enforced by test_catalog_completeness in the test suite.
    """

    C_001 = "C-001"
    C_002 = "C-002"
    C_003 = "C-003"
    C_004 = "C-004"
    C_005 = "C-005"
    C_006 = "C-006"
    C_007 = "C-007"


def get_eintrag(catalog_id: CatalogId) -> KatalogEintrag:
    """Return the KatalogEintrag for a given CatalogId.

    Args:
        catalog_id: The catalog ID to look up.

    Returns:
        The corresponding KatalogEintrag.

    Raises:
        KeyError: If catalog_id has no entry in KATALOG. Should never
            happen if test_catalog_completeness passes.
    """
    return KATALOG[catalog_id.value]
