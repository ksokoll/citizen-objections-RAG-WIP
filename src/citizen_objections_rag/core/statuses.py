"""Enumeration types for domain statuses and classifications."""

from enum import StrEnum


class WuerdigungsStatus(StrEnum):
    """Status of legal basis assessment (Würdigung)."""

    GENERIERT = "generiert"
    UNTERDRUECKT_UNVERIFIED = "unterdrueckt_unverified"
    NO_MATCH = "no_match"


class AbwaegungsStatus(StrEnum):
    """Status of objection statement in approval workflow."""

    DRAFT = "draft"
    APPROVED = "approved"


class EinwendungsTyp(StrEnum):
    """Classification of objection type."""

    TYP_1 = "typ_1"
    TYP_2 = "typ_2"
