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
from collections.abc import Iterable

from mistralai.client import Mistral
from pydantic import BaseModel

from app.core.failures import LLMError


class EndpointNotAllowedError(Exception):
    """The resolved Mistral endpoint is not on the configured allowlist (K1).

    The narrow PII-masking scope (ADR-025) rests on the assumption that the
    Triage LLM is reached at an encapsulated endpoint. That assumption is now a
    checked fact, not prose: the composition root resolves the endpoint and the
    allowlist at startup and raises this before any client is built, so an
    outbound call to an unvetted destination fails loud at the bootstrap line
    rather than silently sending pseudonymized text off-box (ADR-027).
    """


def check_endpoint_allowed(endpoint: str, allowlist: Iterable[str]) -> str:
    """Return the normalized endpoint if it is on the allowlist, else raise.

    Normalizes by stripping a trailing slash from the endpoint and every
    allowlist entry, matching the Mistral SDK's own server-url handling, so
    ``https://host`` and ``https://host/`` are the same destination and a
    cosmetic slash cannot defeat the check.

    Args:
        endpoint: The resolved Triage endpoint (the SDK server_url).
        allowlist: The endpoints a deployment permits. The default admits the
            public Mistral cloud so the demo runs unconfigured; a Behörde
            narrows it to its encapsulated endpoint and excludes the rest.

    Returns:
        The normalized endpoint, safe to hand to the client.

    Raises:
        EndpointNotAllowedError: If the normalized endpoint is not on the
            normalized allowlist. The message names the resolved endpoint and
            the allowlist so the abort is actionable.
    """
    normalized = endpoint.rstrip("/")
    allowed = tuple(entry.rstrip("/") for entry in allowlist)
    if normalized not in allowed:
        raise EndpointNotAllowedError(
            f"Mistral endpoint '{normalized}' is not on the configured "
            f"allowlist {list(allowed)}"
        )
    return normalized


class MistralClient:
    """Mistral implementation of the LLMClient protocol.

    Defaults to mistral-large-latest at temperature=0 for deterministic
    behavior. Both can be overridden via constructor arguments. The underlying
    SDK client can be injected directly via the `client` parameter; this is the
    recommended path for tests where the SDK is replaced by a test double, and
    removes the need for tests to reach into internal attributes.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "mistral-large-latest",
        temperature: float = 0.0,
        base_url: str | None = None,
        client: Mistral | None = None,
    ) -> None:
        """Initialize the Mistral client.

        Args:
            api_key: API key for Mistral. If None and `client` is not provided,
                reads from the MISTRAL_API_KEY environment variable. Ignored
                when `client` is provided.
            model: Model identifier. Defaults to mistral-large-latest.
            temperature: Sampling temperature. Defaults to 0.0 for
                determinism in classification and extraction tasks.
            base_url: The endpoint to reach (the SDK server_url). The
                composition root resolves it and checks it against the
                endpoint allowlist before constructing this client (K1,
                check_endpoint_allowed). None falls back to the SDK default
                (the public Mistral cloud). Ignored when `client` is provided.
            client: Pre-configured Mistral SDK client. When provided, api_key
                and base_url are ignored and no environment lookup happens.
                Used primarily in tests where the SDK is replaced by a double.

        Raises:
            KeyError: If `client` is None, api_key is None, and MISTRAL_API_KEY
                is not set in the environment.
        """
        if client is not None:
            self._client = client
        else:
            resolved_key = api_key or os.environ["MISTRAL_API_KEY"]
            self._client = Mistral(api_key=resolved_key, server_url=base_url)
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
                # The SDK accepts dict messages at runtime; its stubs require the
                # typed message objects, so the plain-dict list is flagged.
                messages=messages,  # type: ignore[arg-type]
            )
        except Exception as exc:
            # Exception policy (ADR-026, M2): the message carries the provider
            # exception type only. The provider's own message may interpolate
            # prompt fragments with residual PII, so it travels solely on the
            # chained cause (from exc), where the logging chain reduces it to
            # type plus location and never writes it to disk. The same policy
            # applies at every raise below.
            raise LLMError(
                f"Mistral generate call failed: {type(exc).__name__}"
            ) from exc

        message = response.choices[0].message
        content = message.content if message is not None else None
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
                # See generate(): dict messages are valid at runtime, typed-only
                # in the stubs.
                messages=messages,  # type: ignore[arg-type]
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            raise LLMError(f"Mistral parse call failed: {type(exc).__name__}") from exc

        message = response.choices[0].message
        content = message.content if message is not None else None
        if content is None or not isinstance(content, str):
            raise LLMError("Mistral returned no JSON content")

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"Mistral returned invalid JSON: {type(exc).__name__}"
            ) from exc

        try:
            return response_format.model_validate(data)
        except Exception as exc:
            # A pydantic ValidationError echoes the offending input values into
            # its message, the rawest PII-leak channel here; the type name only.
            raise LLMError(
                f"Mistral JSON failed schema validation: {type(exc).__name__}"
            ) from exc

    @staticmethod
    def _build_messages(prompt: str, system_prompt: str) -> list[dict[str, str]]:
        """Build the Mistral messages list, optionally with a system prompt."""
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages
