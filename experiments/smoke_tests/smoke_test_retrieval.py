"""Smoke test for the Retrieval chain against the real corpus.

Run from the repository root:
    python smoke_test_retrieval.py <path-to-xml-directory>

Loads all nine statutes and resolves a set of probe citations covering:
    - exact-match hits
    - sub-paragraph citations that drill down to the paragraph
    - Gesetz isolation (same section number in two statutes)
    - an absent paragraph (unresolved)
    - an unparseable citation (unresolved)

Resolution is exact-match only (ADR-021); the probes confirm the
paragraph-level normalisation and Gesetz isolation behave on the real
corpus. The E5Embedder and FaissNormIndex now live under
experiments/vector_retrieval_reference (Round 20, M2) and are not exercised
here.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from app.retrieval.service import (  # noqa: E402
    NormRetrievalService,
)
from app.retrieval.gesetz_xml_loader import (  # noqa: E402
    load_corpus,
)

# Probe citations. Each tuple is (citation, expectation note).
_PROBES: list[tuple[str, str]] = [
    ("§ 9 WHG", "exact hit WHG"),
    ("§ 9 Abs. 1 Nr. 1 WHG", "sub-paragraph drills to § 9 WHG"),
    ("§ 9 BauGB", "exact hit BauGB, must not return WHG § 9"),
    ("§ 1 BauGB", "exact hit BauGB"),
    ("§ 42 VwGO", "exact hit VwGO (no title in source)"),
    ("§ 999 WHG", "absent paragraph, unresolved"),
    ("kaputte Zeichenkette ohne Norm", "parse fail, unresolved"),
]


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python smoke_test_retrieval.py <path-to-xml-directory>")
        sys.exit(1)

    xml_dir = Path(sys.argv[1])
    if not xml_dir.exists():
        print(f"Directory not found: {xml_dir}")
        sys.exit(1)

    print("Loading statute corpus...")
    corpus = load_corpus(xml_dir)
    paragraphs = corpus.paragraphs
    print(f"  {len(paragraphs)} paragraphs loaded")

    service = NormRetrievalService(corpus)
    print(f"  exact-match index built, size={len(paragraphs)}\n")

    print("Resolving probe citations:\n")
    for citation, note in _PROBES:
        t0 = time.perf_counter()
        result = service.resolve([citation])[0]
        elapsed_ms = (time.perf_counter() - t0) * 1000
        conf = f"{result.confidence:.3f}" if result.confidence is not None else "n/a"
        text_preview = result.source_text[:60].replace("\n", " ")
        print(f"  {citation!r}  ({note})")
        print(
            f"    resolved={result.resolved} method={result.method} "
            f"key={result.paragraph_key!r} conf={conf} {elapsed_ms:.0f}ms"
        )
        if result.resolved:
            print(f"    text: {text_preview}...")
        print()


if __name__ == "__main__":
    main()
