"""Behaviour tests for the Mistral endpoint allowlist check (K1).

check_endpoint_allowed is the startup control the narrow PII-masking scope rests
on (ADR-025, ADR-027): the resolved endpoint must be on the configured allowlist
or the bootstrap aborts before any outbound call. These tests cover the check in
isolation; the CLI-level abort and the startup_config record are covered by the
CLI tests.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.failures import LLMError
from app.services.llm.mistral_client import (
    EndpointNotAllowedError,
    MistralClient,
    check_endpoint_allowed,
)
from app.triage.llm_schema import LLMTriageOutput

#: A PII-shaped fragment a provider exception could carry from a prompt echo.
#: Not real data; shaped like the masked entities (name plus IBAN) so a leak
#: into the error message would be unmistakable in the assertion.
_PII_SHAPED = "Sachbearbeiter Max Mustermann, IBAN DE89370400440532013000"


class _RaisingChat:
    """chat namespace whose complete() raises the configured provider error."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def complete(self, **_kwargs: Any) -> Any:
        raise self._error


class _RaisingMistralSDK:
    """Mistral SDK double whose chat.complete raises (the call-failure path)."""

    def __init__(self, error: Exception) -> None:
        self.chat = _RaisingChat(error)


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Response:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class _ReturningChat:
    """chat namespace whose complete() returns the configured raw content."""

    def __init__(self, content: str) -> None:
        self._content = content

    def complete(self, **_kwargs: Any) -> _Response:
        return _Response(self._content)


class _ReturningMistralSDK:
    """Mistral SDK double that returns raw content (the post-call parse path)."""

    def __init__(self, content: str) -> None:
        self.chat = _ReturningChat(content)


def test_endpoint_on_the_allowlist_is_returned_normalized() -> None:
    # Given an allowlisted endpoint carrying a cosmetic trailing slash
    # When the check runs
    resolved = check_endpoint_allowed(
        "https://api.mistral.ai/", ("https://api.mistral.ai",)
    )

    # Then the normalized endpoint is returned, so a trailing slash cannot defeat
    # the check or split one destination into two
    assert resolved == "https://api.mistral.ai"


def test_endpoint_off_the_allowlist_raises_naming_the_endpoint() -> None:
    # Given an endpoint the allowlist does not admit
    # When the check runs, then it raises with the resolved endpoint in the
    # message so the startup abort is actionable
    with pytest.raises(EndpointNotAllowedError) as exc_info:
        check_endpoint_allowed("https://evil.example", ("https://api.mistral.ai",))

    assert "https://evil.example" in str(exc_info.value)


def test_allowlist_entry_trailing_slash_is_normalized() -> None:
    # Given an allowlist entry with a trailing slash and a bare endpoint
    # When the check runs, then the two are the same destination
    resolved = check_endpoint_allowed(
        "https://mistral.intern", ("https://mistral.intern/",)
    )

    assert resolved == "https://mistral.intern"


def test_public_cloud_default_does_not_admit_an_encapsulated_endpoint() -> None:
    # Given the public-cloud-only default allowlist and the encapsulated endpoint
    # a Behörde would use
    # When the check runs unconfigured, then it raises: the demo default does not
    # silently admit other destinations, so a Behörde must configure the
    # allowlist explicitly
    with pytest.raises(EndpointNotAllowedError):
        check_endpoint_allowed(
            "https://mistral.intern.behoerde", ("https://api.mistral.ai",)
        )


class TestExceptionPolicy:
    """LLMError messages carry the exception type only; PII rides the cause (M2).

    A provider exception may interpolate prompt fragments with residual PII. The
    LLMError message must carry only type(exc).__name__, with the original
    reachable as __cause__ for the logging chain to reduce to type plus location.
    A future handler that logs str(LLMError) must find no PII there.
    """

    def test_generate_failure_carries_type_only_and_chains_the_cause(self) -> None:
        # Given an SDK whose call raises a provider exception carrying PII
        provider_error = RuntimeError(_PII_SHAPED)
        client = MistralClient(client=_RaisingMistralSDK(provider_error))

        # When generate runs, then LLMError carries the type name only, the
        # original is the chained cause, and the PII is absent from the message
        with pytest.raises(LLMError) as exc_info:
            client.generate("irrelevant prompt")

        assert exc_info.value.__cause__ is provider_error
        assert "RuntimeError" in str(exc_info.value)
        assert _PII_SHAPED not in str(exc_info.value)

    def test_parse_call_failure_carries_type_only_and_chains_the_cause(self) -> None:
        # Given an SDK whose call raises a provider exception carrying PII
        provider_error = RuntimeError(_PII_SHAPED)
        client = MistralClient(client=_RaisingMistralSDK(provider_error))

        # When parse runs, then the same policy holds at the parse-call site
        with pytest.raises(LLMError) as exc_info:
            client.parse("irrelevant prompt", LLMTriageOutput)

        assert exc_info.value.__cause__ is provider_error
        assert "RuntimeError" in str(exc_info.value)
        assert _PII_SHAPED not in str(exc_info.value)

    def test_invalid_json_carries_type_only_and_chains_the_cause(self) -> None:
        # Given an SDK that returns non-JSON content (the post-call decode path)
        client = MistralClient(client=_ReturningMistralSDK(f"not json {_PII_SHAPED}"))

        # When parse runs, then the JSON decode failure carries the type name
        # only and chains the JSONDecodeError
        with pytest.raises(LLMError) as exc_info:
            client.parse("irrelevant prompt", LLMTriageOutput)

        assert type(exc_info.value.__cause__).__name__ == "JSONDecodeError"
        assert "JSONDecodeError" in str(exc_info.value)
        assert _PII_SHAPED not in str(exc_info.value)

    def test_schema_validation_failure_does_not_echo_input_values(self) -> None:
        # Given an SDK that returns valid JSON which violates the schema, with a
        # PII-shaped value where a list is required: pydantic's ValidationError
        # echoes the offending input value into its message, the rawest leak
        bad = json.dumps({"argumente": _PII_SHAPED})
        client = MistralClient(client=_ReturningMistralSDK(bad))

        # When parse runs, then the schema-validation failure carries the type
        # name only, the ValidationError is the chained cause, and the echoed
        # input value does not appear in the LLMError message. pydantic
        # truncates the middle of the echoed input but keeps the IBAN tail
        # verbatim, so the IBAN is the discriminating substring: the old
        # interpolation would have leaked it, the type-only message does not.
        with pytest.raises(LLMError) as exc_info:
            client.parse("irrelevant prompt", LLMTriageOutput)

        assert type(exc_info.value.__cause__).__name__ == "ValidationError"
        assert "ValidationError" in str(exc_info.value)
        assert _PII_SHAPED not in str(exc_info.value)
        assert "DE89370400440532013000" not in str(exc_info.value)
