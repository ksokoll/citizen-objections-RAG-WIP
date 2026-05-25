"""Shared prompt infrastructure for all bounded contexts."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PromptTemplate:
    """A versioned prompt with provenance metadata.

    Attributes:
        name: Stable identifier for the prompt.
        version: Semantic version string.
        last_modified: Date of the last content change.
        tested_models: Models validated against this prompt. Empty tuple
            if not yet validated against a real model (e.g. skeleton stub).
        description: One-line summary of the prompt's purpose.
        prompt: The full prompt text.
    """

    name: str
    version: str
    last_modified: datetime
    tested_models: tuple[str, ...]
    description: str
    prompt: str
