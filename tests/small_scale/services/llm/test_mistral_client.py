"""Behaviour tests for the Mistral endpoint allowlist check (K1).

check_endpoint_allowed is the startup control the narrow PII-masking scope rests
on (ADR-025, ADR-027): the resolved endpoint must be on the configured allowlist
or the bootstrap aborts before any outbound call. These tests cover the check in
isolation; the CLI-level abort and the startup_config record are covered by the
CLI tests.
"""

from __future__ import annotations

import pytest

from app.services.llm.mistral_client import (
    EndpointNotAllowedError,
    check_endpoint_allowed,
)


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
