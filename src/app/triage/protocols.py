# triage.protocols.py - Protocols for the Triage bounded context.
"""Protocols for the Triage bounded context.

Holds the LLM-client interface the Triage service depends on for argument
extraction. The protocol lives with its only consumer, the context that owns
the prompt and the structured output it parses into, rather than in the shared
kernel: an LLM interface consumed by exactly one context is context knowledge,
not a cross-context contract (H1, Round 20). This mirrors the Retriever move of
Round 17.1, which relocated a single-context protocol out of core; the two
remaining single-context protocols are now retro-fitted to that precedent so
the repo carries one rule, not two.

The composition root (__main__) builds the concrete MistralClient and injects
it into TriageService; tests substitute a structural fake. No context other
than Triage imports this protocol.
"""

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClientProtocol(Protocol):
    """Provider-agnostic LLM text generation and structured parsing.

    Temperature and model selection are implementation details, not part of
    the interface. All LLM calls must go through this Protocol; no direct
    imports of anthropic, openai, or provider SDKs are allowed in domain or
    application code.
    """

    def generate(self, prompt: str, system_prompt: str = "") -> str: ...

    def parse(
        self,
        prompt: str,
        response_format: type[T],
        system_prompt: str = "",
    ) -> T: ...
