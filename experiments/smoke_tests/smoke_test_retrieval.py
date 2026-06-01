"""Smoke test for the full Retrieval chain against the real corpus.

Run from the repository root:
    python smoke_test_retrieval.py <path-to-xml-directory>

Loads the real multilingual-e5-large model, builds the FAISS index over
all nine statutes, and resolves a set of probe citations covering:
    - exact-match hits
    - sub-paragraph citations that drill down to the paragraph
    - Gesetz isolation (same section number in two statutes)
    - a forced vector-fallback case
    - an unresolvable citation

The vector-fallback probes print their real cosine scores so the
confidence floor in NormRetrievalService can be calibrated against actual
data rather than a guessed value.

This is the provider-compatibility / setup smoke test mandated by
iteration_14_plan.md. First run downloads the e5 model (about 2 GB).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from app.retrieval.application.norm_retrieval_service import (  # noqa: E402
    NormRetrievalService,
)
from app.retrieval.infrastructure.e5_embedder import E5Embedder  # noqa: E402
from app.retrieval.infrastructure.faiss_norm_index import (  # noqa: E402
    FaissNormIndex,
)
from app.retrieval.infrastructure.gesetz_xml_loader import (  # noqa: E402
    load_all_gesetze,
)

# Probe citations. Each tuple is (citation, expectation note).
_PROBES: list[tuple[str, str]] = [
    ("§ 9 WHG", "exact hit WHG"),
    ("§ 9 Abs. 1 Nr. 1 WHG", "sub-paragraph drills to § 9 WHG"),
    ("§ 9 BauGB", "exact hit BauGB, must not return WHG § 9"),
    ("§ 1 BauGB", "exact hit BauGB"),
    ("§ 42 VwGO", "exact hit VwGO (no title in source)"),
    ("§ 999 WHG", "absent paragraph, vector fallback or unresolved"),
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
    paragraphs = load_all_gesetze(xml_dir)
    print(f"  {len(paragraphs)} paragraphs loaded")

    print("Loading e5 model (first run downloads ~2 GB)...")
    t0 = time.perf_counter()
    embedder = E5Embedder()
    print(f"  model loaded in {time.perf_counter() - t0:.1f}s")

    print("Embedding corpus (this is the one-time index build)...")
    t0 = time.perf_counter()
    # Embed title plus text where a title exists, else text alone. Title
    # adds signal for the vector fallback; absence (e.g. VwGO) is fine.
    passages = [f"{p.title}. {p.text}" if p.title else p.text for p in paragraphs]
    embeddings = embedder.embed_passages(passages)
    print(f"  embedded {len(embeddings)} paragraphs in {time.perf_counter() - t0:.1f}s")

    index = FaissNormIndex(paragraphs, embeddings)
    service = NormRetrievalService(index, embedder, paragraphs)
    print(f"  index built, size={index.size()}\n")

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
