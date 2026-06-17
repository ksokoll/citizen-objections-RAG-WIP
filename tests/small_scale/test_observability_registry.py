"""Behaviour tests for the root-assembled event vocabulary and key allowlist.

observability holds the enforcement mechanism but no domain vocabulary (H2,
H3): each context declares its own event constants and its own allowlisted log
field names, and the composition root unions them into the registry and the
allowlist the logging chain enforces against. These tests assert the ownership
move and the assembly, not the chain behaviour (that is covered by
test_observability_logging.py). They replace the former golden test on the
central ALLOWED_KEYS frozenset: the union treats the cause (per-context
ownership), where the golden list treated the symptom.
"""

from __future__ import annotations

import pytest
import structlog

from app.audit_log.events import AUDIT_APPEND_FAILED, AUDIT_EVENTS, AUDIT_KEYS
from app.document_ingestion.events import INGESTION_EVENTS, INGESTION_KEYS
from app.observability import events as observability_events
from app.observability.events import (
    OBSERVABILITY_EVENTS,
    UnregisteredLogEventError,
    registered_events,
)
from app.observability.logging_config import (
    OBSERVABILITY_KEYS,
    UnregisteredLogKeyError,
    allowed_keys,
)
from app.observability_registry import (
    CLI_EVENTS,
    CLI_KEYS,
    register_observability_vocabulary,
)
from app.triage.events import TRIAGE_EVENTS, TRIAGE_KEYS


def test_every_context_event_is_registered_via_the_union() -> None:
    # Given: the conftest composition root has assembled the vocabulary
    # When: the registry is read
    registered = registered_events()

    # Then: each context's declared events plus the root's CLI events and the
    # observability layer's own events are all present, so the registry is the
    # union of the per-context declarations, not a central god-list.
    for declared in (
        TRIAGE_EVENTS,
        INGESTION_EVENTS,
        AUDIT_EVENTS,
        CLI_EVENTS,
        OBSERVABILITY_EVENTS,
    ):
        assert declared <= registered


def test_observability_events_module_holds_no_domain_constant() -> None:
    # Given: the observability events module
    module_strings = {
        value
        for name, value in vars(observability_events).items()
        if isinstance(value, str) and not name.startswith("__")
    }

    # When/Then: every event string the module defines is one of its own
    # self-instrumentation events; no domain (triage., ingestion., audit.) or
    # CLI (app.) constant lives here anymore (H2).
    domain_prefixes = ("triage.", "ingestion.", "audit.", "app.")
    leaked = {value for value in module_strings if value.startswith(domain_prefixes)}
    assert leaked == set()


def test_registration_is_idempotent() -> None:
    # Given: the vocabulary already assembled by the conftest root
    before = registered_events()

    # When: a second root assembly runs (the CLI in the same process)
    register_observability_vocabulary()

    # Then: the registry is unchanged, so two roots calling it never drift or
    # double-count.
    assert registered_events() == before


def test_an_unregistered_event_still_raises_in_strict_mode() -> None:
    # Given: strict mode (the autouse default) and the assembled registry
    log = structlog.get_logger()

    # When/Then: an event name that no context declared is rejected at the
    # chain, so the union enforcement is the same loud failure as the former
    # central frozenset.
    with pytest.raises(UnregisteredLogEventError):
        log.info("nobody.declared.this.event")


def test_allowlist_is_exactly_the_union_of_per_context_declarations() -> None:
    # Given: the conftest composition root has assembled the allowlist
    # When: the allowlist is read
    assembled = allowed_keys()

    # Then: it is exactly the union of the layer's own keys and every context's
    # declared keys plus the CLI keys, no more and no less. This replaces the
    # golden test on a central frozenset: the contract is that the assembled
    # set IS the union, so a key cannot exist anywhere but in a context's *_KEYS
    # declaration (or the root's CLI_KEYS).
    expected = OBSERVABILITY_KEYS | TRIAGE_KEYS | INGESTION_KEYS | AUDIT_KEYS | CLI_KEYS
    assert assembled == expected


def test_a_field_emitted_without_its_declaration_raises_in_strict_mode() -> None:
    # Given: strict mode (the autouse default) and a registered audit event
    log = structlog.get_logger()

    # When/Then: emitting a registered event with a field name that no context
    # declared in its *_KEYS set fails loudly at the allowlist processor. This
    # is the loud-failure path the round preserves: a context field renamed
    # without updating its declared keys is caught in strict/CI at its origin,
    # not dropped silently from the line (ADR-026, enforcement at origin).
    with pytest.raises(UnregisteredLogKeyError):
        log.error(AUDIT_APPEND_FAILED, undeclared_renamed_field="x")
