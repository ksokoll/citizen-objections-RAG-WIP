"""Enumeration types for domain statuses and classifications."""

# Naming convention: German for domain events, English for code identifiers
from enum import StrEnum


class WuerdigungsStatus(StrEnum):
    """Status of legal basis assessment (Würdigung)."""

    GENERIERT = "generiert"
    UNTERDRUECKT_UNVERIFIED = "unterdrueckt_unverified"
    KEIN_TREFFER = "kein_treffer"
    ARGUMENT_UNVERIFIZIERT = "argument_unverifiziert"
    RECHTSGRUNDLAGE_UNVERIFIZIERT = "rechtsgrundlage_unverifiziert"


class AbwaegungsStatus(StrEnum):
    """Status of objection statement in approval workflow."""

    DRAFT = "draft"
    APPROVED = "approved"


class EinwendungsTyp(StrEnum):
    """Classification of objection type."""

    TYP_1 = "typ_1"
    TYP_2 = "typ_2"
