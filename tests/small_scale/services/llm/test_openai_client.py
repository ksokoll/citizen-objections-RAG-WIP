"""OpenAI implementation of the LLMClient protocol.

Wraps OpenAI's chat completion API for two use cases: free-text
generation and structured output via constrained decoding (used by
Triage for argument extraction). The client is provider-specific
but exposes only the methods declared in app.triage.protocols.LLMClientProtocol.
No OpenAI types leak across this boundary; callers receive plain
strings or Pydantic model instances.
"""

from __future__ import annotations

import os

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel

from app.core.failures import LLMError


class OpenAIClient:
    """OpenAI implementation of the LLMClient protocol.

    Defaults to gpt-4o-mini at temperature=0 for deterministic behavior.
    Both can be overridden via constructor arguments. The underlying SDK
    client can be injected directly via the `client` parameter; this is
    the recommended path for tests where the SDK is replaced by a test
    double, and removes the need for tests to access internal attributes.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        temperature: float = 0.0,
        client: OpenAI | None = None,
    ) -> None:
        """Initialize the OpenAI client.

        Args:
            api_key: API key for OpenAI. If None and `client` is not
                provided, reads from the OPENAI_API_KEY environment
                variable. Ignored when `client` is provided.
            model: Model identifier. Defaults to gpt-4o-mini.
            temperature: Sampling temperature. Defaults to 0.0 for
                determinism in classification and extraction tasks.
            client: Pre-configured OpenAI SDK client. When provided,
                `api_key` is ignored and no environment lookup happens.
                Used primarily in tests where the SDK is replaced by
                a test double.

        Raises:
            KeyError: If `client` is None, `api_key` is None, and
                OPENAI_API_KEY is not set in the environment.
        """
        if client is not None:
            self._client = client
        else:
            resolved_key = api_key or os.environ["OPENAI_API_KEY"]
            self._client = OpenAI(api_key=resolved_key)
        self._model = model
        self._temperature = temperature

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        """Run a free-text generation call.

        For callers that post-process the raw LLM output rather than
        schema-validating it.

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
            response = self._client.chat.completions.create(
                model=self._model,
                temperature=self._temperature,
                messages=messages,
            )
        except Exception as exc:
            raise LLMError(f"OpenAI generate call failed: {exc}") from exc

        content = response.choices[0].message.content
        if content is None:
            raise LLMError("OpenAI returned no content")
        return content

    def parse[T: BaseModel](
        self,
        prompt: str,
        response_format: type[T],
        system_prompt: str = "",
    ) -> T:
        """Run a structured-output call via constrained decoding.

        Used by Triage where the LLM output must conform to a Pydantic
        schema. OpenAI's beta parse API enforces schema conformance at
        decoding time, eliminating format-drift errors.

        Args:
            prompt: User-facing prompt.
            response_format: Pydantic model the response must conform to.
            system_prompt: Optional system instructions.

        Returns:
            Parsed Pydantic model instance matching response_format.

        Raises:
            LLMError: If the API call fails or returns no parsed content.
        """
        messages = self._build_messages(prompt, system_prompt)

        try:
            response = self._client.beta.chat.completions.parse(
                model=self._model,
                temperature=self._temperature,
                messages=messages,
                response_format=response_format,
            )
        except Exception as exc:
            raise LLMError(f"OpenAI parse call failed: {exc}") from exc

        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise LLMError("OpenAI returned no parsed content")
        return parsed

    @staticmethod
    def _build_messages(
        prompt: str, system_prompt: str
    ) -> list[ChatCompletionMessageParam]:
        """Build the OpenAI messages list, optionally with a system prompt."""
        messages: list[ChatCompletionMessageParam] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages
