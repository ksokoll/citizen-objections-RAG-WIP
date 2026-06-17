"""Per-event payload schemas: what each custody event may carry (Form B, ADR-032).

The hash chain stays content-free so it can coexist with the right to erasure:
the chain is undeletable, erasure reaches only the raw store, and that holds only
if the chain carries nothing erasure-bound. Round 18a-c enforced this with a
length heuristic (a payload string up to 128 chars), which a name like "Max
Mustermann" passes: a length bound is not an erasure guarantee.

Form B replaces the heuristic with positive declaration. Each AuditEventType
declares exactly which payload keys it carries and the type of each, here in the
audit context. The store validates a payload against its event's schema at write
entry (JsonLinesAuditStore._append): only declared keys with declared
types pass, everything else is rejected loudly. {"namen": ["Max Mustermann"]}
cannot enter because `namen` is declared on no event, and the bound is no longer
a string length but a fixed key allowlist, which is what makes the content-free
property mechanical rather than a heuristic (ADR-032).

The schema is enforced only at write entry, never on the read path: the hash
chain checks integrity, and a content rule has no business failing a store open
(Sec-3). A pre-18d or later-tightened non-conforming line is read back
tolerantly; only a new write must conform.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.core.events import AuditEventType


class PayloadSchemaError(ValueError):
    """A payload carries a key or type the event's declared schema forbids.

    A ValueError subclass, not an AuditLogError: an undeclared key or a wrong
    type is a programming error in the emitter, not a recoverable store I/O
    failure, so it propagates loudly past the recoverable-failure routing
    (ADR-027) rather than being swallowed as a transient append failure.
    """


@dataclass(frozen=True)
class FlatDict:
    """A one-level dict[str, value_type]: the only container the chain admits.

    Counts maps (masked_entity_counts) and version maps (package_versions) are
    flat dicts of simple leaves, content-free by the same reasoning as a scalar.
    The values must all be value_type; a dict of dicts is not a FlatDict and is
    rejected, so depth cannot smuggle structure (and content) into the chain.
    """

    value_type: type


#: The declared payload schema per event type (Form B, ADR-032). Each entry maps
#: a custody event to the payload keys it may carry and each key's type: a scalar
#: type (str, int, bool) or a FlatDict of one. Presence is not required (an event
#: may carry a subset, e.g. show-document's startup_config omits the
#: process-only fields); only the keys that ARE present must be declared and
#: well-typed. Every AuditEventType must appear here, so a new event type without
#: a declared schema fails closed rather than admitting an undeclared payload.
PAYLOAD_SCHEMAS: dict[AuditEventType, dict[str, type | FlatDict]] = {
    AuditEventType.EINGANG: {
        "document_id": str,
        "masked_entity_counts": FlatDict(int),
    },
    AuditEventType.TRIAGE: {
        "argument_count": int,
        "contradiction_detected": bool,
        "substance_threshold_exceeded": bool,
    },
    AuditEventType.RETRIEVAL: {
        "resolved_norm_count": int,
    },
    AuditEventType.BRIEFING_ERSTELLT: {
        "entry_count": int,
    },
    AuditEventType.KEIN_TREFFER: {
        "entry_count": int,
    },
    # Declared in the enum but not emitted today; they carry no payload yet. A
    # future emitter declares its keys here rather than relaxing the gate.
    AuditEventType.ENTWURF_UNTERDRUECKT: {},
    AuditEventType.FREIGABE: {},
    AuditEventType.PIPELINE_FEHLER: {
        "reason": str,
    },
    AuditEventType.STARTKONFIGURATION: {
        "git_sha": str,
        "package_versions": FlatDict(str),
        "allowlist_size": int,
        "tracing_enabled": bool,
        "log_format": str,
        "corpus_id": str,
        "model_id": str,
        "mistral_endpoint": str,
    },
    # The read-access custody event (ADR-033): the show-document path records a
    # raw-document read here before disclosing content. The document_id is the
    # only payload key; it is content-free (the pseudonymous id, never the
    # document text) and redundant with the event's einwendungs_id by
    # construction, the same self-describing duplication EINGANG carries. The
    # "when" is the event's own top-level timestamp, already inside the canonical
    # hash (serialization.py) and so tamper-evident, not duplicated into the
    # payload as a second clock that could diverge from it.
    AuditEventType.ROHDOKUMENT_ZUGRIFF: {
        "document_id": str,
    },
}


def _scalar_matches(value: object, expected: type) -> bool:
    """Whether a scalar value is of the declared type.

    bool is an int subclass, so the two are separated explicitly: a declared int
    rejects a bool, and a declared bool requires a bool. Otherwise a flag could
    pass where a count is declared (or the reverse), quietly changing the proof's
    shape.
    """
    if expected is bool:
        return isinstance(value, bool)
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, expected)


def _value_matches(value: object, spec: type | FlatDict) -> bool:
    """Whether one payload value satisfies its declared spec.

    A FlatDict spec requires a dict with string keys whose every value is the
    declared leaf type; a scalar spec requires the declared scalar type.
    """
    if isinstance(spec, FlatDict):
        return isinstance(value, dict) and all(
            isinstance(key, str) and _scalar_matches(item, spec.value_type)
            for key, item in value.items()
        )
    return _scalar_matches(value, spec)


def _describe(spec: type | FlatDict) -> str:
    """Render a spec for an error message."""
    if isinstance(spec, FlatDict):
        return f"a flat dict[str, {spec.value_type.__name__}]"
    return spec.__name__


def validate_payload(event_type: AuditEventType, payload: Mapping[str, object]) -> None:
    """Reject a payload that the event's declared schema does not permit (ADR-032).

    The content-free gate, enforced at write entry. Every key present in the
    payload must be declared on this event type with a matching type; an
    undeclared key or a wrong type is a PayloadSchemaError. A value outside the
    declared scalar/flat-dict shape cannot enter the chain, so a text fragment
    (declared on no event) is rejected by construction rather than by a length
    bound.

    Args:
        event_type: The custody event type whose schema governs the payload.
        payload: The payload to check.

    Raises:
        PayloadSchemaError: If the event type declares no schema, if a payload
            key is undeclared, or if a declared key carries the wrong type.
    """
    schema = PAYLOAD_SCHEMAS.get(event_type)
    if schema is None:
        raise PayloadSchemaError(
            f"event type {event_type.value!r} declares no payload schema; the "
            "audit chain admits only declared, well-typed payload keys so it "
            "stays content-free (ADR-032)"
        )
    for key, value in payload.items():
        spec = schema.get(key)
        if spec is None:
            raise PayloadSchemaError(
                f"payload key {key!r} is not declared on the {event_type.value} "
                "event; the audit chain admits only each event's declared keys, "
                "so an undeclared key (and any text it could carry) is rejected "
                "at write entry (ADR-032)"
            )
        if not _value_matches(value, spec):
            raise PayloadSchemaError(
                f"payload[{key!r}] on the {event_type.value} event must be "
                f"{_describe(spec)}, not {type(value).__name__} (ADR-032)"
            )
