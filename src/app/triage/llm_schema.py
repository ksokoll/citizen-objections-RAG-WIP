"""LLM-facing schemas for the Triage bounded context.

Separated from the internal ExtrahiertesArgument domain model (Variante A).
The LLM produces only the four semantic fields. zitierte_normen is populated
deterministically by norm_extractor after the LLM call, inside
TriageService._build_extrahiertes_argument. argument_id and argument_verified
are infrastructure concerns not exposed to the LLM.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.core import EinwendungsTyp
from app.triage.catalog import CatalogId


class LLMArgument(BaseModel):
    """Schema for a single argument as produced by the LLM.

    Attributes:
        argument_text: Concise legal formulation, max two sentences.
        original_zitat: Verbatim substring from the source text. Must be
            exactly reproducible from the source; verified downstream.
        catalog_id: Catalog entry (one per Gesetz), or None if argument
            is TYP_1 or cannot be mapped to a known law.
        einwendungs_typ: TYP_1 (informal citizen) or TYP_2 (formal legal).
    """

    argument_text: str = Field(
        ...,
        description=("Concise legal formulation of the argument, max two sentences."),
    )
    original_zitat: str = Field(
        ...,
        min_length=1,
        description=(
            "Verbatim substring from the source text. Must be exactly "
            "reproducible from the source; verified downstream."
        ),
    )
    catalog_id: CatalogId | None = Field(
        ...,
        description=(
            "Catalog entry (one per Gesetz), or null if argument is TYP_1 "
            "or cannot be mapped to a known law."
        ),
    )
    einwendungs_typ: EinwendungsTyp = Field(
        ...,
        description=("TYP_1 (informal citizen) or TYP_2 (formal legal argumentation)."),
    )

    @field_validator("original_zitat")
    @classmethod
    def _reject_blank_zitat(cls, value: str) -> str:
        """Reject a whitespace-only quote at the trust boundary.

        min_length=1 excludes the empty string, but a quote of only spaces
        passes a length check and strips to empty at the verification site,
        where str.find("") returns 0 and the empty quote would count as
        verified (ADR-006 Layer 1). Rejecting it here excludes the degenerate
        case at entry; TriageService applies the same guard as the backstop.
        """
        if not value.strip():
            raise ValueError("original_zitat must not be blank or whitespace-only")
        return value


class LLMTriageOutput(BaseModel):
    """Wrapper for the LLM's structured output."""

    argumente: list[LLMArgument] = Field(
        ...,
        description="List of extracted arguments. Empty list is valid for "
        "TYP_1 documents.",
    )
