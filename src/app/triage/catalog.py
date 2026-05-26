"""Predefined objection catalog for the Triage bounded context.

Single source of truth for catalog entries and the CatalogId enum.
Both the Pydantic extraction schema (Triage) and the corpus routing
(ResponseDrafting) consume from this module. Drift is prevented by
colocation: adding an entry requires updating both KATALOG and CatalogId.

v3 (2026-05-26): Restructured from 7 thematic clusters (v2) to 9
gesetz-based entries, one per law in the corpus. catalog_id IS the
retriever partition key; no separate mapping. See ADR-016.
"""

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class KatalogEintrag:
    """A single predefined objection pattern with routing metadata.

    Attributes:
        catalog_id: Unique identifier, matches CatalogId enum value. Also
            serves directly as the retriever partition key (ADR-016).
        beschreibung: Human-readable description used in LLM extraction schema.
            Generic domain vocabulary, no leakage of specific test arguments.
        rechtsgebiet: Primary legal domain (e.g. BauGB, WHG, BNatSchG).
    """

    catalog_id: str
    beschreibung: str
    rechtsgebiet: str


KATALOG: dict[str, KatalogEintrag] = {
    "baugb": KatalogEintrag(
        catalog_id="baugb",
        beschreibung=(
            "Baugesetzbuch (BauGB). Materielles und formelles Bauplanungsrecht: "
            "Bauleitplanung (Flächennutzungsplan, Bebauungsplan), Aufstellung "
            "und Festsetzungen, Verhältnis FNP zu Bebauungsplan, "
            "Abwägungsgebot, städtebauliche Erforderlichkeit, "
            "Erschließungssicherung, Öffentlichkeits- und Trägerbeteiligung, "
            "Auslegungspflichten, Beachtlichkeit von Verfahrensfehlern, "
            "Umweltbericht (§§ 1, 2a, 3, 4, 8, 12, 30, 214 BauGB)."
        ),
        rechtsgebiet="BauGB",
    ),
    "baunvo": KatalogEintrag(
        catalog_id="baunvo",
        beschreibung=(
            "Baunutzungsverordnung (BauNVO). Gebietsausweisung und "
            "Nutzungsarten in Bebauungsplänen: Wohngebiete, Gewerbegebiete, "
            "Industriegebiete, Sondergebiete, zulässige und unzulässige "
            "Nutzungen, Anlagen für technische Infrastruktur."
        ),
        rechtsgebiet="BauNVO",
    ),
    "bimschg": KatalogEintrag(
        catalog_id="bimschg",
        beschreibung=(
            "Bundes-Immissionsschutzgesetz (BImSchG). Immissionsschutzrechtliche "
            "Anforderungen aus genehmigungsbedürftigen Anlagen: "
            "Anlagengenehmigung, Schutz vor schädlichen Umwelteinwirkungen "
            "(Geräusche, Erschütterungen, Luftverunreinigungen), "
            "Betreiberpflichten, Stand der Technik."
        ),
        rechtsgebiet="BImSchG",
    ),
    "bnatschg": KatalogEintrag(
        catalog_id="bnatschg",
        beschreibung=(
            "Bundesnaturschutzgesetz (BNatSchG). Naturschutzrecht: "
            "artenschutzrechtliche Zugriffsverbote, FFH-Verträglichkeit für "
            "Schutzgebiete, Landschaftsschutzgebiete, naturschutzrechtliche "
            "Befreiungen, Umweltbericht-Anforderungen der Bauleitplanung "
            "(§§ 34, 44, 63, 67 BNatSchG)."
        ),
        rechtsgebiet="BNatSchG",
    ),
    "enwg": KatalogEintrag(
        catalog_id="enwg",
        beschreibung=(
            "Energiewirtschaftsgesetz (EnWG). Energiewirtschaftsrechtliche "
            "Anforderungen: Netzanschluss an Hoch- oder Mittelspannungsnetze, "
            "Versorgungspflichten, Kostentragung für Netzausbau und "
            "Energieerschließung als Voraussetzung der planungsrechtlichen "
            "Zulässigkeit (§ 17 EnWG)."
        ),
        rechtsgebiet="EnWG",
    ),
    "vwgo": KatalogEintrag(
        catalog_id="vwgo",
        beschreibung=(
            "Verwaltungsgerichtsordnung (VwGO). Verwaltungsgerichtliche "
            "Verfahren: Normenkontrollverfahren gegen Bebauungspläne, "
            "Klagearten, Klagebefugnis, Fristen und Antragsvoraussetzungen "
            "(§ 47 VwGO)."
        ),
        rechtsgebiet="VwGO",
    ),
    "wastrg": KatalogEintrag(
        catalog_id="wastrg",
        beschreibung=(
            "Bundeswasserstraßengesetz (WaStrG). Bundeswasserstraßen-Recht: "
            "Schifffahrt, Wasserstraßen-Verwaltung, Strom- und Schifffahrtspolizei, "
            "Anlagen an Bundeswasserstraßen."
        ),
        rechtsgebiet="WaStrG",
    ),
    "whg": KatalogEintrag(
        catalog_id="whg",
        beschreibung=(
            "Wasserhaushaltsgesetz (WHG). Wasserrecht: Gewässerbenutzung und "
            "Erlaubnispflichten, Wasserentnahme, thermische und stoffliche "
            "Gewässerbelastung, Trinkwasserschutzgebiete "
            "(§§ 8, 9, 57 WHG)."
        ),
        rechtsgebiet="WHG",
    ),
    "wpg": KatalogEintrag(
        catalog_id="wpg",
        beschreibung=(
            "Wärmeplanungsgesetz (WPG). Kommunale Wärmeplanung und "
            "Abwärmenutzung: Berücksichtigung industrieller Abwärmequellen, "
            "Verpflichtungen zur Abwärmeauskopplung in Durchführungsverträgen, "
            "Wärmenetz-Integration (§ 7 WPG)."
        ),
        rechtsgebiet="WPG",
    ),
}


class CatalogId(str, Enum):
    """Public interface for catalog entries used in Pydantic extraction schema.

    Each value must have a corresponding entry in KATALOG.
    Enforced by test_catalog_completeness in the test suite.

    The enum value is also the retriever partition key (ADR-016).
    """

    BAUGB = "baugb"
    BAUNVO = "baunvo"
    BIMSCHG = "bimschg"
    BNATSCHG = "bnatschg"
    ENWG = "enwg"
    VWGO = "vwgo"
    WASTRG = "wastrg"
    WHG = "whg"
    WPG = "wpg"


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
