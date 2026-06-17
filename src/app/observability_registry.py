"""Composition-root assembly of the observability registries.

The observability layer holds the enforcement mechanism but no domain
vocabulary (H2, H3): each bounded context declares the event constants and the
allowlisted field names it emits in its own events.py. This module is the
composition root for that vocabulary. It imports every context's declared
events and keys, adds the root's own CLI events and keys, and unions them into
the live registry and allowlist the logging chain enforces against.

Both composition roots (the CLI in __main__ and the test conftest) call
register_observability_vocabulary once before configure_logging, so the two
roots assemble the same registry and cannot drift. register_events and
register_keys are idempotent, so the order of the two roots in a test process
does not matter.

The CLI/self-instrumentation events and keys belong to the root, not to any
context: STARTUP_CONFIG records the active toolset and CLI_UNHANDLED_ERROR is
emitted by the CLI dispatch boundary; the startup_config provenance fields and
resolved store paths are the root's own keys. They are declared here rather
than in a bounded context because the root is what emits them.
"""

from __future__ import annotations

from typing import Final

from app.audit_log.events import AUDIT_EVENTS, AUDIT_KEYS
from app.document_ingestion.events import INGESTION_EVENTS, INGESTION_KEYS
from app.observability.events import register_events
from app.observability.logging_config import register_keys
from app.triage.events import TRIAGE_EVENTS, TRIAGE_KEYS

#: The active toolset at startup, emitted once by the CLI composition root
#: after a successful bootstrap. Records what produced the run's output:
#: git_sha, model_id, package versions, corpus_id, allowlist size, tracing
#: flag, log format, and the resolved store paths. Operational provenance,
#: never payload.
STARTUP_CONFIG: Final[str] = "app.startup_config"

#: An unexpected exception reached the CLI dispatch boundary. Emitted at ERROR
#: by the entrypoint catch-all before the process exits 1; the exception is
#: reduced to type plus location by the chain and its message (foreign-authored
#: text) is never written. The stderr line the user sees carries the type only,
#: no detail and no traceback.
CLI_UNHANDLED_ERROR: Final[str] = "app.unhandled_error"

#: The composition root's own events, owned by the root rather than a context.
CLI_EVENTS: Final[frozenset[str]] = frozenset({STARTUP_CONFIG, CLI_UNHANDLED_ERROR})

#: The composition root's own allowlisted log keys, owned by the root rather
#: than a context: the startup_config provenance fields (git_sha, model_id,
#: package_versions, corpus_id, allowlist_size, tracing_enabled, log_format,
#: mistral_endpoint) and the resolved absolute store paths (app_home, log_dir,
#: raw_store, audit_log; S5). The corpus_id key keeps its name: it is the
#: briefing provenance field, recorded in startup_config (ADR-028). The
#: mistral_endpoint key records the destination the startup allowlist check
#: admitted (K1, ADR-027). Static configuration provenance, never document
#: content.
CLI_KEYS: Final[frozenset[str]] = frozenset(
    {
        "git_sha",
        "model_id",
        "package_versions",
        "corpus_id",
        "allowlist_size",
        "tracing_enabled",
        "log_format",
        "mistral_endpoint",
        "app_home",
        "log_dir",
        "raw_store",
        "audit_log",
    }
)


def register_observability_vocabulary() -> None:
    """Union every context's events and keys plus the root's into the registries.

    The composition-root act that builds the event vocabulary and the key
    allowlist the logging chain enforces against (H2, H3). Idempotent and safe
    to call from either root (the CLI or the test conftest). observability's own
    self-instrumentation events and keys seed the registries at import; this
    adds the domain and root vocabulary.
    """
    register_events(TRIAGE_EVENTS)
    register_events(INGESTION_EVENTS)
    register_events(AUDIT_EVENTS)
    register_events(CLI_EVENTS)

    register_keys(TRIAGE_KEYS)
    register_keys(INGESTION_KEYS)
    register_keys(AUDIT_KEYS)
    register_keys(CLI_KEYS)
