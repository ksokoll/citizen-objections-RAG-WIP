"""The chain head and its external-anchor serialization (ADR-031).

The chain's tip (last hash and sequence) and the shaping that commits it into an
eval's results.json live here, beside the verification half rather than inside
the file store. The store owns the in-memory head ADR-030 maintains and exposes
it as a ChainHead; this module is the small value object plus the anchor form an
eval run embeds so the git history witnesses the chain's tip.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChainHead:
    """The chain's current tip: the last event's hash and its sequence number.

    The value an eval run anchors into its committed results.json, so the git
    history witnesses the chain's tip at that moment (ADR-031). A fresh chain has
    the genesis sentinel as event_hash and None as sequence_number, recorded
    honestly: there is no chain to anchor yet.
    """

    event_hash: str
    sequence_number: int | None


def head_anchor(head: ChainHead) -> dict[str, object]:
    """Serialize the chain head as the external-anchor block for results.json.

    Returns {"chain_anchor": {"head_hash", "head_sequence"}}, the value an eval
    run embeds into its committed results.json so the git history is an external
    trust anchor against later truncation or rewrite (ADR-031). A None sequence
    (an empty chain) is recorded as null, not masked: there was no chain to
    anchor.

    Args:
        head: The chain head to serialize, typically a store's `head`.
    """
    return {
        "chain_anchor": {
            "head_hash": head.event_hash,
            "head_sequence": head.sequence_number,
        }
    }


def results_with_anchor(
    results: dict[str, object], head: ChainHead
) -> dict[str, object]:
    """Merge the chain-head anchor block into an eval's results (ADR-031).

    The load-bearing anchor logic, here under src/app so it is mypy- and
    ruff-checked rather than escaping static analysis under experiments/ (A4):
    it merges head_anchor(head) into the results under the reserved chain_anchor
    key, so the committed results.json witnesses the chain's tip without
    colliding with the eval's own metrics. Writing the file is eval glue that
    stays under experiments/.

    Args:
        results: The eval's own result mapping (metrics, per-document outcomes).
        head: The audit store's current chain head.
    """
    return {**results, **head_anchor(head)}
