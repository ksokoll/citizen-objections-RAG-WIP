"""Pure classification functions for the Triage bounded context.

Extracted from TriageService to enable direct testing without
instantiating the full service. No I/O, no dependencies.
"""

from app.core.entities import ExtrahiertesArgument
from app.core.statuses import EinwendungsTyp


def classify_einwendungs_typ(
    arguments: list[ExtrahiertesArgument],
) -> EinwendungsTyp:
    """Derive document-level EinwendungsTyp from per-argument types.

    TYP_2 if any argument is TYP_2, otherwise TYP_1.
    Empty argument list returns TYP_1.

    Args:
        arguments: Verified extracted arguments.

    Returns:
        Document-level EinwendungsTyp.
    """
    if any(a.einwendungs_typ == EinwendungsTyp.TYP_2 for a in arguments):
        return EinwendungsTyp.TYP_2
    return EinwendungsTyp.TYP_1
