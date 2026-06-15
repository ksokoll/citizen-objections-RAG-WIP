"""Anchor the audit chain head into a committed eval results.json (ADR-031).

An eval run that exercises the audit chain records the chain's current head
(hash plus sequence) into its results.json. results.json is committed, so the
git history becomes an external trust anchor: a later truncation or rewrite of
the chain no longer matches the head a past commit witnessed, even though a
keyless chain rewritten from the break onward still verifies internally.

The anchor is honest about its limits. It is sparse (written only at eval runs,
so the detection window is the gap between runs) and, in a one-person repo,
committed by the same actor who could tamper, so it raises the cost of an
undetected truncation rather than closing it. It demonstrates the concept of an
external, distributed anchor; a dedicated, more frequent anchor command is
backlog (ADR-031).

Usage in an eval run script that runs the pipeline (and so populates the chain):

    from app.audit_log.store import JsonLinesAuditStore
    from eval_chain_anchor import write_results_with_anchor

    store = JsonLinesAuditStore(audit_log_path)
    # ... run the pipeline, populating both `results` and the chain ...
    write_results_with_anchor(results, store, results_path)
"""

from __future__ import annotations

import json
from pathlib import Path

from app.audit_log.anchor import results_with_anchor
from app.audit_log.store import JsonLinesAuditStore


def write_results_with_anchor(
    results: dict[str, object], store: JsonLinesAuditStore, path: Path
) -> None:
    """Write eval results with the chain head anchored, as committed JSON.

    The anchor's core logic (merging the chain head into the results under the
    reserved "chain_anchor" key) lives under src/app, mypy- and ruff-checked
    (results_with_anchor, A4). This module keeps only the eval-glue file write,
    which stays under experiments/.

    Args:
        results: The eval's own result mapping (metrics, per-document outcomes).
        store: The audit store whose current head is anchored.
        path: Where to write the committed results.json.
    """
    document = results_with_anchor(results, store.head)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
