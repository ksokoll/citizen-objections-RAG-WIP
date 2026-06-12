"""Serialization of the briefing delivery contract (ADR-028).

The WuerdigungsBriefing is the system boundary and its serialized form is
what the consumer parses, so the serialization is part of the contract, not
of any transport. It lives in the Briefing context beside the entities it
serializes; the CLI (and any future transport) delegates here rather than
owning its own copy that could drift (H1, Round 16.1).

Contract: JSON with ISO-8601 UTC datetimes, ensure_ascii=False so German
legal text survives byte-for-byte readable.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime

from app.briefing.entities import WuerdigungsBriefing


def to_json(briefing: WuerdigungsBriefing) -> str:
    """Serialize a briefing per the delivery contract (ADR-028).

    Args:
        briefing: The assembled briefing to deliver.

    Returns:
        The briefing as a JSON string with ISO-8601 UTC datetimes.

    Raises:
        TypeError: If a briefing field carries a type outside the contract
            (a contract violation, not an input condition).
    """

    def _default(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"not JSON serializable: {type(value).__name__}")

    return json.dumps(
        dataclasses.asdict(briefing),
        ensure_ascii=False,
        default=_default,
    )
