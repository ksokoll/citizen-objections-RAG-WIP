"""Behaviour tests for the root-assembled event vocabulary (H2).

observability holds the enforcement mechanism but no domain vocabulary: each
context declares its own event constants, and the composition root unions them
into the registry the logging chain enforces against. These tests assert the
ownership move and the assembly, not the chain behaviour (that is covered by
test_observability_logging.py).
"""

from __future__ import annotations

import pytest
import structlog

from app.audit_log.events import AUDIT_EVENTS
from app.document_ingestion.events import INGESTION_EVENTS
from app.observability import events as observability_events
from app.observability.events import (
    OBSERVABILITY_EVENTS,
    UnregisteredLogEventError,
    registered_events,
)
from app.observability_registry import (
    CLI_EVENTS,
    register_observability_vocabulary,
)
from app.triage.events import TRIAGE_EVENTS


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
