"""Predefined objection catalog for the Triage bounded context.

Single source of truth for catalog entries and the CatalogId enum.
Both the Pydantic extraction schema (Triage) and the corpus routing
(ResponseDrafting) consume from this module. Drift is prevented by
colocation: adding an entry requires updating both KATALOG and CatalogId.
"""

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class KatalogEintrag:
    """A single predefined objection pattern with routing metadata.

    Attributes:
        catalog_id: Unique identifier, matches CatalogId enum value.
        beschreibung: Human-readable description used in LLM extraction schema.
        rechtsgebiet: Primary legal domain (e.g. BImSchG, BauGB).
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
            "Lärmschutz: Einwendung gegen unzumutbare Lärmimmissionen "
            "durch das Vorhaben im Wohn- und Mischgebiet (§ 41 BImSchG, "
            "DIN 18005)."
        ),
        rechtsgebiet="BImSchG",
        corpus_partition="bimschg",
    ),
    "C-002": KatalogEintrag(
        catalog_id="C-002",
        beschreibung=(
            "Verkehr und Erschließung: Einwendung gegen Beeinträchtigung "
            "der Verkehrssicherheit und unzureichende Erschließungsqualität "
            "durch das geplante Bauvorhaben."
        ),
        rechtsgebiet="BauGB",
        corpus_partition="baugb",
    ),
    "C-003": KatalogEintrag(
        catalog_id="C-003",
        beschreibung=(
            "Naturschutz und Grünflächen: Einwendung wegen Eingriffs in "
            "schutzwürdige Biotope, Grünflächen oder Ausgleichsflächen "
            "(§ 14 BNatSchG)."
        ),
        rechtsgebiet="BNatSchG",
        corpus_partition="bnatschg",
    ),
    "C-004": KatalogEintrag(
        catalog_id="C-004",
        beschreibung=(
            "Luftqualität und Emissionen: Einwendung gegen Schadstoff- "
            "und Feinstaubbelastung durch gewerbliche oder industrielle "
            "Nutzung (39. BImSchV, TA Luft)."
        ),
        rechtsgebiet="BImSchG",
        corpus_partition="bimschg",
    ),
    "C-005": KatalogEintrag(
        catalog_id="C-005",
        beschreibung=(
            "Planungsrecht: Einwendung gegen fehlerhafte Abwägung, "
            "Widerspruch zum Flächennutzungsplan oder unzulässige "
            "Gebietsfestsetzung (§ 1, § 8 BauGB, BauNVO)."
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
