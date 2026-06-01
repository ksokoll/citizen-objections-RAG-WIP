"""Mistral implementation of the LLMClient protocol.

Mistral exposes structured output via response_format with json_object
type. Schema conformance is not enforced at decoding time (unlike
OpenAI's beta parse), so the parsed JSON must be validated against
the Pydantic schema after the call. This is less robust than OpenAI's
approach but matches what Mistral currently supports.
"""

from __future__ import annotations

import json
import os

from mistralai.client import Mistral
from pydantic import BaseModel

from app.core.failures import LLMError


class MistralClient:
    """Mistral implementation of the LLMClient protocol.

    Defaults to mistral-large-latest at temperature=0 for deterministic
    behavior. Both can be overridden via constructor arguments.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "mistral-large-latest",
        temperature: float = 0.0,
    ) -> None:
        """Initialize the Mistral client.

        Args:
            api_key: API key for Mistral. If None, reads from the
                MISTRAL_API_KEY environment variable.
            model: Model identifier. Defaults to mistral-large-latest.
            temperature: Sampling temperature. Defaults to 0.0 for
                determinism in classification and extraction tasks.

        Raises:
            KeyError: If api_key is None and MISTRAL_API_KEY is not
                set in the environment.
        """
        resolved_key = api_key or os.environ["MISTRAL_API_KEY"]
        self._client = Mistral(api_key=resolved_key)
        self._model = model
        self._temperature = temperature

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        """Run a free-text generation call.

        Args:
            prompt: User-facing prompt.
            system_prompt: Optional system instructions.

        Returns:
            Generated text content.

        Raises:
            LLMError: If the API call fails or returns no content.
        """
        messages = self._build_messages(prompt, system_prompt)

        try:
            response = self._client.chat.complete(
                model=self._model,
                temperature=self._temperature,
                messages=messages,
            )
        except Exception as exc:
            raise LLMError(f"Mistral generate call failed: {exc}") from exc

        content = response.choices[0].message.content
        if content is None or not isinstance(content, str):
            raise LLMError("Mistral returned no text content")
        return content

    def parse[T: BaseModel](
        self,
        prompt: str,
        response_format: type[T],
        system_prompt: str = "",
    ) -> T:
        """Run a structured-output call via response_format=json_object.

        The system prompt is augmented to instruct Mistral about the
        target schema. The response is JSON-parsed and validated
        against the Pydantic schema. Schema enforcement is post-hoc,
        not at decoding time.

        Args:
            prompt: User-facing prompt.
            response_format: Pydantic model the response must conform to.
            system_prompt: Optional system instructions. The schema
                description is appended to it.

        Returns:
            Parsed Pydantic model instance matching response_format.

        Raises:
            LLMError: If the API call fails, returns invalid JSON, or
                the JSON does not validate against the schema.
        """
        schema_json = json.dumps(response_format.model_json_schema(), indent=2)
        schema_instruction = (
            f"Respond with valid JSON conforming to this schema:\n{schema_json}"
        )
        augmented_system = (
            f"{system_prompt}\n\n{schema_instruction}"
            if system_prompt
            else schema_instruction
        )
        messages = self._build_messages(prompt, augmented_system)

        try:
            response = self._client.chat.complete(
                model=self._model,
                temperature=self._temperature,
                messages=messages,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            raise LLMError(f"Mistral parse call failed: {exc}") from exc

        content = response.choices[0].message.content
        if content is None or not isinstance(content, str):
            raise LLMError("Mistral returned no JSON content")

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Mistral returned invalid JSON: {exc}") from exc

        try:
            return response_format.model_validate(data)
        except Exception as exc:
            raise LLMError(f"Mistral JSON failed schema validation: {exc}") from exc

    @staticmethod
    def _build_messages(prompt: str, system_prompt: str) -> list[dict[str, str]]:
        """Build the Mistral messages list, optionally with a system prompt."""
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages
