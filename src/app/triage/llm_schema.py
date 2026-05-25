"""LLM-facing schemas for the Triage bounded context.

Separated from the internal ExtrahiertesArgument domain model (Variante A).
The LLM produces only the four semantic fields. zitierte_normen is populated
deterministically by norm_extractor after the LLM call, inside
TriageService._build_extrahiertes_argument. argument_id and argument_verified
are infrastructure concerns not exposed to the LLM.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.statuses import EinwendungsTyp
from app.triage.catalog import CatalogId


class LLMArgument(BaseModel):
    """Schema for a single argument as produced by the LLM.

    Attributes:
        argument_text: Concise legal formulation, max two sentences.
        original_zitat: Verbatim substring from the source text. Must be
            exactly reproducible from the source; verified downstream.
        catalog_id: Catalog cluster, or None if argument is TYP_1 or
            cannot be mapped to a known cluster.
        einwendungs_typ: TYP_1 (informal citizen) or TYP_2 (formal legal).
    """

    argument_text: str = Field(
        ...,
        description=("Concise legal formulation of the argument, max two sentences."),
    )
    original_zitat: str = Field(
        ...,
        description=(
            "Verbatim substring from the source text. Must be exactly "
            "reproducible from the source; verified downstream."
        ),
    )
    catalog_id: CatalogId | None = Field(
        ...,
        description=(
            "Catalog cluster, or null if argument is TYP_1 or cannot be "
            "mapped to a known cluster."
        ),
    )
    einwendungs_typ: EinwendungsTyp = Field(
        ...,
        description=("TYP_1 (informal citizen) or TYP_2 (formal legal argumentation)."),
    )


class LLMTriageOutput(BaseModel):
    """Wrapper for the LLM's structured output."""

    argumente: list[LLMArgument] = Field(
        ...,
        description="List of extracted arguments. Empty list is valid for "
        "TYP_1 documents.",
    )
