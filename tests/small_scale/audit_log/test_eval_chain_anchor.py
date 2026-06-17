"""Behaviour test for the eval head-anchoring helper (ADR-031).

The helper lives under experiments/ (it writes an eval artifact), so it is
loaded here from its file path rather than imported as a package. The behaviour
under test: after a run, the committed results.json carries the chain head hash
and sequence, so the git history witnesses the chain's tip.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path
from types import ModuleType

from app.audit_log.store import JsonLinesAuditStore
from app.core.events import AuditEvent, AuditEventType


def _load_anchor_helper() -> ModuleType:
    """Load experiments/eval_chain_anchor.py by path (it is not a package)."""
    helper_path = (
        Path(__file__).resolve().parents[3] / "experiments" / "eval_chain_anchor.py"
    )
    spec = importlib.util.spec_from_file_location("eval_chain_anchor", helper_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_results_json_carries_the_chain_head_after_a_run(tmp_path: Path) -> None:
    """Given a populated chain and an eval's results, when results are written
    with the anchor, then results.json carries both the eval's own metrics and a
    chain_anchor block with the current head hash and sequence (ADR-031).
    """
    helper = _load_anchor_helper()
    store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    for index in range(3):
        store.publish(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type=AuditEventType.EINGANG,
                einwendungs_id=f"EW-{index:03d}",
            )
        )
    results = {"recall": 0.9, "precision": 0.95}
    results_path = tmp_path / "results.json"

    helper.write_results_with_anchor(results, store, results_path)

    document = json.loads(results_path.read_text(encoding="utf-8"))
    assert document["recall"] == 0.9
    assert document["chain_anchor"]["head_hash"] == store.head.event_hash
    assert document["chain_anchor"]["head_sequence"] == 2
