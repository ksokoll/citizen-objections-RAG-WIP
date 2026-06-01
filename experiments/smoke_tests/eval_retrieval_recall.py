"""Retrieval recall evaluation for the Retrieval bounded context.

Run from the repository root:
    python eval_retrieval_recall.py <xml-dir> <ground-truth-dir>

Builds the real index over all nine statutes, then runs every
must_retrieve citation from the Phase A ground truth through the
resolver. Reports the resolution-method distribution (exact / vector /
unresolved), overall and per Gesetz, plus the confidence scores of any
vector-fallback hits so the confidence floor can be calibrated against
real data.

This answers the open question from the smoke test: do real citations
ever need the vector fallback, or does the exact-match path with
paragraph-level normalisation already resolve everything valid? The
answer decides whether the vector fallback stays (and at what floor) or
is removed as unnecessary complexity.

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

from app.retrieval.application.norm_retrieval_service import (  # noqa: E402
    NormRetrievalService,
)
from app.retrieval.domain.entities import NormWithSource  # noqa: E402
from app.retrieval.infrastructure.e5_embedder import E5Embedder  # noqa: E402
from app.retrieval.infrastructure.faiss_norm_index import (  # noqa: E402
    FaissNormIndex,
)
from app.retrieval.infrastructure.gesetz_xml_loader import (  # noqa: E402
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

    print("Building index...")
    paragraphs = load_all_gesetze(xml_dir)
    embedder = E5Embedder()
    passages = [f"{p.title}. {p.text}" if p.title else p.text for p in paragraphs]
    embeddings = embedder.embed_passages(passages)
    index = FaissNormIndex(paragraphs, embeddings)
    service = NormRetrievalService(index, embedder, paragraphs)
    print(f"  index size={index.size()}\n")

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
    for method in ("exact", "vector", "none"):
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
        line = "  ".join(f"{m}={counts.get(m, 0)}" for m in ("exact", "vector", "none"))
        print(f"  {gesetz:<10} {line}")

    # Vector-fallback hits: show scores for floor calibration.
    vector_hits = [(c, r) for c, r in results if r.method == "vector"]
    if vector_hits:
        print("\n" + "=" * 70)
        print("Vector-fallback hits (for confidence floor calibration)")
        print("=" * 70)
        for citation, result in sorted(vector_hits, key=lambda x: x[1].confidence or 0):
            print(
                f"  {citation:<28} -> {result.paragraph_key:<14} "
                f"conf={result.confidence:.3f}"
            )

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
