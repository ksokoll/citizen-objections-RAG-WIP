"""Canonical, byte-deterministic serialization of AuditEvents for the hash chain.

The tamper-evident chain (ADR-024) is only a proof if the same logical event
always produces the same bytes. Pydantic's model_dump_json() does not promise
that: key order, separator whitespace, and unicode escaping are serializer
implementation details that can shift across Pydantic or Python versions. A
shifted byte form makes the verify path recompute a different hash than the
write path produced, breaking the chain with nobody having tampered. So the
canonical form is pinned here explicitly: a dict of the hashed fields, json.dumps
with sorted keys, the tightest separators, and no ASCII escaping, encoded utf-8.

One function, shared by the write path (which assigns event_hash) and the verify
path (which recomputes it). Two copies could drift; the proof needs exactly one.

The serializer is selected per event by serialization_version, so a future v2
format can be added without invalidating events written under v1: each event
records the version that produced its bytes, and that version reproduces them.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from app.core.events import AuditEvent


class UnknownSerializationVersionError(ValueError):
    """Raised when an event's serialization_version has no registered serializer.

    Every persisted event records the version that produced its canonical bytes
    so historical events stay verifiable as the format evolves. A version with
    no serializer means the code that could reproduce those bytes is absent:
    fail loud rather than silently hash a different byte form and report a break
    that is really a missing serializer.
    """


def _canonical_content_v1(event: AuditEvent) -> bytes:
    """Serialize an event to its canonical v1 bytes.

    The hashed content is every field except event_hash, which is the output of
    hashing this content and so cannot be one of its inputs. sequence_number is
    included: it fixes the event's position in the chain, so reordering events
    must change their bytes.

    Args:
        event: The event to serialize. Its event_hash is ignored.

    Returns:
        The canonical utf-8 bytes: json.dumps with sorted keys, tight
        separators, and ensure_ascii=False, so the form is stable across
        machines and Python versions.
    """
    content = event.model_dump(mode="json", exclude={"event_hash"})
    canonical = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return canonical.encode("utf-8")


#: Canonical serializer per serialization_version. A later v2 registers its own
#: entry; events written under v1 keep selecting _canonical_content_v1, so the
#: format addition does not invalidate the historical chain.
_SERIALIZERS: dict[int, Callable[[AuditEvent], bytes]] = {
    1: _canonical_content_v1,
}


def canonical_bytes(event: AuditEvent) -> bytes:
    """Return an event's canonical bytes, dispatched on its serialization_version.

    Args:
        event: The event to serialize.

    Returns:
        The deterministic byte form the hash chain hashes over.

    Raises:
        UnknownSerializationVersionError: If no serializer is registered for the
            event's serialization_version.
    """
    serializer = _SERIALIZERS.get(event.serialization_version)
    if serializer is None:
        raise UnknownSerializationVersionError(
            "no canonical serializer for serialization_version "
            f"{event.serialization_version!r}"
        )
    return serializer(event)
