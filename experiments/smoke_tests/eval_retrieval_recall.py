"""Retrieval recall evaluation for the Retrieval bounded context.

Run from the repository root:
    python eval_retrieval_recall.py <xml-dir> <ground-truth-dir>

Loads all nine statutes, then runs every must_retrieve citation from the
Phase A ground truth through the resolver. Reports the resolution-method
distribution (exact / unresolved), overall and per Gesetz.

This answered the open question that motivated the original experiment:
the exact-match path with paragraph-level normalisation resolves every
valid citation (25/25 on the Phase A ground truth), so the vector
fallback was removed as unnecessary complexity (ADR-021).

Ground truth format assumed (per the Phase A retrieval_gt files):
    {
      "expected_arguments": [
        {"expected_norms": {"must_retrieve": [{"citation": "§ 9 WHG"}, ...]}},
        ...
      ]
    }
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from app.retrieval.service import (  # noqa: E402
    NormRetrievalService,
)
from app.retrieval.entities import NormWithSource  # noqa: E402
from app.retrieval.gesetz_xml_loader import (  # noqa: E402
    load_all_gesetze,
)


def collect_citations(gt_dir: Path) -> list[str]:
    """Gather every must_retrieve citation across all ground-truth files.

    Args:
        gt_dir: Directory of Phase A retrieval ground-truth JSON files.

    Returns:
        A de-duplicated, sorted list of canonical citation strings.
    """
    citations: set[str] = set()
    for gt_path in sorted(gt_dir.glob("*.json")):
        with gt_path.open(encoding="utf-8") as fh:
            gt = json.load(fh)
        for arg in gt.get("expected_arguments", []):
            for entry in arg["expected_norms"]["must_retrieve"]:
                citations.add(" ".join(entry["citation"].split()))
    return sorted(citations)


def _gesetz_of(citation: str) -> str:
    """Extract the trailing Gesetz token from a citation for grouping."""
    tokens = citation.split()
    return tokens[-1] if tokens else "?"


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python eval_retrieval_recall.py <xml-dir> <ground-truth-dir>")
        sys.exit(1)

    xml_dir = Path(sys.argv[1])
    gt_dir = Path(sys.argv[2])
    for path in (xml_dir, gt_dir):
        if not path.exists():
            print(f"Path not found: {path}")
            sys.exit(1)

    print("Loading corpus...")
    paragraphs = load_all_gesetze(xml_dir)
    service = NormRetrievalService(paragraphs)
    print(f"  corpus size={len(paragraphs)}\n")

    citations = collect_citations(gt_dir)
    print(f"Resolving {len(citations)} unique must_retrieve citations...\n")

    results: list[tuple[str, NormWithSource]] = [
        (c, service.resolve([c])[0]) for c in citations
    ]

    method_counts: Counter[str] = Counter(r.method for _, r in results)
    total = len(results)

    print("=" * 70)
    print("Resolution method distribution")
    print("=" * 70)
    for method in ("exact", "none"):
        count = method_counts.get(method, 0)
        pct = (count / total * 100) if total else 0
        print(f"  {method:<8} {count:>4} / {total}  ({pct:.1f}%)")

    # Per-Gesetz breakdown.
    print("\n" + "=" * 70)
    print("Per-Gesetz resolution")
    print("=" * 70)
    by_gesetz: dict[str, Counter[str]] = {}
    for citation, result in results:
        gesetz = _gesetz_of(citation)
        by_gesetz.setdefault(gesetz, Counter())[result.method] += 1
    for gesetz in sorted(by_gesetz):
        counts = by_gesetz[gesetz]
        line = "  ".join(f"{m}={counts.get(m, 0)}" for m in ("exact", "none"))
        print(f"  {gesetz:<10} {line}")

    # Unresolved citations: these are the misses.
    unresolved = [(c, r) for c, r in results if not r.resolved]
    if unresolved:
        print("\n" + "=" * 70)
        print("Unresolved citations (retrieval misses)")
        print("=" * 70)
        for citation, _ in unresolved:
            print(f"  {citation}")

    # Headline recall: fraction resolved by any method.
    resolved_count = sum(1 for _, r in results if r.resolved)
    recall = (resolved_count / total * 100) if total else 0
    print("\n" + "=" * 70)
    print(f"Overall resolution recall: {resolved_count}/{total} ({recall:.1f}%)")
    print("=" * 70)


if __name__ == "__main__":
    main()
