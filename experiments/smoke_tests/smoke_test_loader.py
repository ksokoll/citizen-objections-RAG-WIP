"""Smoke test for the Gesetz XML loader against the real statute files.

Run from the repository root:
    python smoke_test_loader.py <path-to-xml-directory>

Prints per-statute paragraph counts, a sample of canonical keys with
titles, and flags any statute that parsed to zero paragraphs (a likely
sign of a structural deviation in that file).
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

# Make the src-layout package importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from app.retrieval.infrastructure.gesetz_xml_loader import (  # noqa: E402
    load_all_gesetze,
    load_gesetz,
)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python smoke_test_loader.py <path-to-xml-directory>")
        sys.exit(1)

    xml_dir = Path(sys.argv[1])
    if not xml_dir.exists():
        print(f"Directory not found: {xml_dir}")
        sys.exit(1)

    xml_files = sorted(xml_dir.glob("*.xml"))
    print(f"Found {len(xml_files)} XML files in {xml_dir}\n")

    # Per-file breakdown.
    for xml_path in xml_files:
        paragraphs = load_gesetz(xml_path)
        if not paragraphs:
            print(f"  WARNING {xml_path.name}: 0 paragraphs parsed")
            continue
        gesetz = paragraphs[0].gesetz
        titled = sum(1 for p in paragraphs if p.title)
        print(
            f"  {xml_path.name}: gesetz={gesetz!r}, "
            f"{len(paragraphs)} paragraphs, {titled} with title"
        )

    # Combined load plus a sample.
    print("\nLoading all together...")
    all_paragraphs = load_all_gesetze(xml_dir)
    print(f"Total paragraphs across all statutes: {len(all_paragraphs)}\n")

    by_gesetz = Counter(p.gesetz for p in all_paragraphs)
    print("Paragraphs per Gesetz:")
    for gesetz, count in sorted(by_gesetz.items()):
        print(f"  {gesetz}: {count}")

    print("\nSample of first 15 canonical keys:")
    for p in all_paragraphs[:15]:
        title_preview = p.title[:50] if p.title else "(no title)"
        print(f"  {p.canonical_key:<20} | {title_preview}")

    # Quick sanity: are there duplicate canonical keys (would break exact-match)?
    key_counts = Counter(p.canonical_key for p in all_paragraphs)
    duplicates = {k: c for k, c in key_counts.items() if c > 1}
    if duplicates:
        print(f"\nWARNING: {len(duplicates)} duplicate canonical keys:")
        for key, count in list(duplicates.items())[:10]:
            print(f"  {key}: {count} times")
    else:
        print("\nNo duplicate canonical keys (exact-match index will be clean).")


if __name__ == "__main__":
    main()