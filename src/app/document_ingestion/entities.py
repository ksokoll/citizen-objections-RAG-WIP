"""Context-internal entities for the DocumentIngestion bounded context.

MaskingResult is the output of the PII masking step. It never crosses a
context boundary: it is produced and consumed inside DocumentIngestion (the
service reads its fields into the cross-context IngestionResult, which lives
in core). It therefore belongs to this context, not the shared kernel
(ADR-025).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MaskingResult:
    """Output of the PII masking step in DocumentIngestion.

    DTO carrying the masked text plus per-type counts of how many spans of
    each entity type were masked. The counts feed the masking Fitness
    Function and the audit record; they contain no PII themselves (only type
    names and integers). An empty entity_counts dict is a valid result: text
    with no detectable PII produces no masked spans.

    Attributes:
        text: The masked text, with detected PII spans replaced by speaking
            German type placeholders ([NAME], [TELEFON], [EMAIL], [IBAN]).
        entity_counts: Mapping of entity type to the number of spans masked
            for that type (e.g. {"NAME": 2, "TELEFON": 1}).
    """

    text: str
    entity_counts: dict[str, int] = field(default_factory=dict)
