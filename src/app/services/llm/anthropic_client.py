"""Anthropic implementation of the LLMClient protocol.

Anthropic does not expose a direct response_format parameter analogous
to OpenAI's beta parse. Structured output is achieved via the tool_use
pattern: the LLM is forced to call a single virtual tool whose
input_schema is the Pydantic model's JSON Schema. The tool's input
becomes the parsed response.

This pattern is stable across Claude model versions and works
identically for all Pydantic models.
"""

from __future__ import annotations

import os

from anthropic import Anthropic
from pydantic import BaseModel

from app.core.failures import LLMError


class AnthropicClient:
    """Anthropic implementation of the LLMClient protocol.

    Defaults to Claude Sonnet 4 at temperature=0 for deterministic
    behavior. Both can be overridden via constructor arguments.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-20250514",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> None:
        """Initialize the Anthropic client.

        Args:
            api_key: API key for Anthropic. If None, reads from the
                ANTHROPIC_API_KEY environment variable.
            model: Model identifier. Defaults to Claude Sonnet 4.
            temperature: Sampling temperature. Defaults to 0.0 for
                determinism in classification and extraction tasks.
            max_tokens: Maximum tokens in the response. Anthropic
                requires this parameter explicitly, unlike OpenAI.

        Raises:
            KeyError: If api_key is None and ANTHROPIC_API_KEY is not
                set in the environment.
        """
        resolved_key = api_key or os.environ["ANTHROPIC_API_KEY"]
        self._client = Anthropic(api_key=resolved_key)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        """Run a free-text generation call.

        Args:
            prompt: User-facing prompt.
            system_prompt: Optional system instructions.

        Returns:
            Generated text content.

        Raises:
            LLMError: If the API call fails or returns no text content.
        """
        try:
            response = self._client.messages.create(
                model=self._model,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                system=system_prompt if system_prompt else "",
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise LLMError(f"Anthropic generate call failed: {exc}") from exc

        text_blocks = [b for b in response.content if b.type == "text"]
        if not text_blocks:
            raise LLMError("Anthropic returned no text content")
        return text_blocks[0].text

    def parse[T: BaseModel](
        self,
        prompt: str,
        response_format: type[T],
        system_prompt: str = "",
    ) -> T:
        """Run a structured-output call via tool_use forcing.

        The Pydantic schema is exposed to the model as a single virtual
        tool. tool_choice forces the model to call that tool, making
        the tool's input the structured response. Schema conformance
        is enforced by Anthropic's tool_use validation.

        Args:
            prompt: User-facing prompt.
            response_format: Pydantic model the response must conform to.
            system_prompt: Optional system instructions.

        Returns:
            Parsed Pydantic model instance matching response_format.

        Raises:
            LLMError: If the API call fails, returns no tool_use block,
                or the tool_use input does not validate against the schema.
        """
        tool_name = "respond"
        tool_schema = {
            "name": tool_name,
            "description": (
                "Provide the structured response. You MUST call this tool "
                "exactly once with the complete answer."
            ),
            "input_schema": response_format.model_json_schema(),
        }

        try:
            # The plain-dict tool schema and tool_choice are accepted by the SDK
            # at runtime; its create() overloads require the typed param objects.
            response = self._client.messages.create(  # type: ignore[call-overload]
                model=self._model,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                system=system_prompt if system_prompt else "",
                messages=[{"role": "user", "content": prompt}],
                tools=[tool_schema],
                tool_choice={"type": "tool", "name": tool_name},
            )
        except Exception as exc:
            raise LLMError(f"Anthropic parse call failed: {exc}") from exc

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_use_blocks:
            raise LLMError("Anthropic returned no tool_use block")

        try:
            return response_format.model_validate(tool_use_blocks[0].input)
        except Exception as exc:
            raise LLMError(
                f"Anthropic tool_use input failed schema validation: {exc}"
            ) from exc
