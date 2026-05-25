"""Evaluation script for deterministic norm extraction.

v2: Uses the type-tagged ground truth (paragraph_norm filter). Earlier
runs of v1 mixed paragraph norms with non-norm entries (court decisions,
administrative guidelines, statute mentions without paragraphs), which
suppressed recall artificially.

The primary recall metric now compares the extractor output against
GT entries with type == "paragraph_norm" only. A secondary diagnostic
block tracks how many extractor_limitation entries exist in the GT
(documents the Phase 2 roadmap: FFH-Richtlinie, TA Lärm, DIN standards,
Anlage-X, Landesverordnungen).

Per-document metrics (against paragraph_norm subset):
- Recall: |extracted ∩ expected| / |expected|, vacuously 1.0 if no expectations
- Precision: |extracted ∩ expected| / |extracted|, vacuously 1.0 if nothing extracted
- GT-Loss: norms in GT but missed by extractor (false negatives, concrete misses)
- Overcount: norms found by extractor but not in GT (plausibility check)

Aggregated:
- Mean recall and precision across documents
- Total TP, GT-Loss, Overcount counts
- Loss-Diagnostik: every missed paragraph_norm with source document
- Overcount-Diagnostik: every extra extracted norm with source document
- Limitation-Diagnostik: every extractor_limitation entry from the GT,
  grouped by document, with anmerkung notes for the Phase 2 roadmap
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.triage.norm_extractor import extract_canonical_norms

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE = Path(__file__).parent.parent
DATA_TYP2 = BASE / "data" / "typ2"
DATA_MIXED = BASE / "data" / "mixed"
GT_PATH = BASE / "ground_truth" / "typ2.json"
RESULTS_PATH = BASE / "results"
RESULTS_PATH.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_norm_string(s: str) -> str:
    """Collapse whitespace for robust string-set comparison."""
    return " ".join(s.split())


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# ---------------------------------------------------------------------------
# Ground truth loader
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GroundTruthEntry:
    """Single GT entry, partitioned by type."""

    norm: str
    type: str
    fundstelle: str | None
    anmerkung: str | None


@dataclass
class DocumentGT:
    """All GT entries for one document, partitioned by type."""

    doc_id: str
    paragraph_norms: set[str]
    extractor_limitations: list[GroundTruthEntry]
    other_entries: list[GroundTruthEntry]


def _load_gt() -> dict[str, DocumentGT]:
    """Load type-tagged GT and partition entries by their type field.

    Returns a mapping from doc_id to DocumentGT. Each DocumentGT separates
    paragraph_norms (set of normalized strings, used for primary recall)
    from extractor_limitations (kept for diagnostic reporting) and other
    entries (gesetz mentions, court decisions, guidelines).
    """
    with open(GT_PATH, encoding="utf-8") as f:
        documents = json.load(f)

    by_doc: dict[str, DocumentGT] = {}
    for entry in documents:
        doc_id = entry.get("dokument")
        if doc_id is None:
            continue

        explizit = entry.get("explizit_zitierte_normen", [])
        paragraph_norms: set[str] = set()
        limitations: list[GroundTruthEntry] = []
        other: list[GroundTruthEntry] = []

        for item in explizit:
            if not isinstance(item, dict) or "norm" not in item:
                continue
            gt_entry = GroundTruthEntry(
                norm=item["norm"],
                type=item.get("type", "UNTAGGED"),
                fundstelle=item.get("fundstelle"),
                anmerkung=item.get("anmerkung"),
            )

            if gt_entry.type == "paragraph_norm":
                paragraph_norms.add(_normalize_norm_string(gt_entry.norm))
            elif gt_entry.type == "extractor_limitation":
                limitations.append(gt_entry)
            else:
                other.append(gt_entry)

        by_doc[doc_id] = DocumentGT(
            doc_id=doc_id,
            paragraph_norms=paragraph_norms,
            extractor_limitations=limitations,
            other_entries=other,
        )

    return by_doc


GT_BY_DOC = _load_gt()

total_paragraph = sum(len(gt.paragraph_norms) for gt in GT_BY_DOC.values())
total_limitations = sum(len(gt.extractor_limitations) for gt in GT_BY_DOC.values())
total_other = sum(len(gt.other_entries) for gt in GT_BY_DOC.values())

print(f"Loaded ground truth for {len(GT_BY_DOC)} documents")
print(f"  paragraph_norm:                 {total_paragraph}")
print(f"  extractor_limitation:           {total_limitations}")
print(f"  other (Gesetz/Urteil/Leitfaden): {total_other}\n")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class NormExtractionResult:
    """Eval result for norm extraction on a single document.

    Measured against the paragraph_norm subset of the GT only.
    """

    doc_id: str
    doc_type: str
    extracted: set[str]
    expected: set[str]

    @property
    def true_positives(self) -> set[str]:
        return self.extracted & self.expected

    @property
    def gt_loss(self) -> set[str]:
        return self.expected - self.extracted

    @property
    def overcount(self) -> set[str]:
        return self.extracted - self.expected

    @property
    def recall(self) -> float:
        if not self.expected:
            return 1.0
        return len(self.true_positives) / len(self.expected)

    @property
    def precision(self) -> float:
        if not self.extracted:
            return 1.0
        return len(self.true_positives) / len(self.extracted)


# ---------------------------------------------------------------------------
# Per-document evaluation
# ---------------------------------------------------------------------------

def evaluate_document(doc_id: str, doc_type: str, text: str) -> NormExtractionResult:
    """Extract norms from text and compare against paragraph_norm GT subset."""
    extracted_raw = extract_canonical_norms(text)
    extracted = {_normalize_norm_string(n) for n in extracted_raw}

    gt = GT_BY_DOC.get(doc_id)
    expected = gt.paragraph_norms if gt is not None else set()

    return NormExtractionResult(
        doc_id=doc_id,
        doc_type=doc_type,
        extracted=extracted,
        expected=expected,
    )


# ---------------------------------------------------------------------------
# Run evaluation
# ---------------------------------------------------------------------------

results: list[NormExtractionResult] = []

for path in sorted(DATA_TYP2.glob("*.txt")):
    results.append(evaluate_document(path.stem, "typ2", path.read_text(encoding="utf-8")))

for path in sorted(DATA_MIXED.glob("*.txt")):
    results.append(evaluate_document(path.stem, "mixed", path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Summary per doc_type
# ---------------------------------------------------------------------------

def summarize(label: str, subset: list[NormExtractionResult]) -> None:
    if not subset:
        return

    recalls = [r.recall for r in subset if r.expected]
    precisions = [r.precision for r in subset if r.extracted]

    total_tp = sum(len(r.true_positives) for r in subset)
    total_loss = sum(len(r.gt_loss) for r in subset)
    total_overcount = sum(len(r.overcount) for r in subset)

    print(f"\n{'=' * 80}")
    print(f"{label} (n={len(subset)}, paragraph_norm only)")
    if recalls:
        print(f"  Mean Recall:     {_avg(recalls):.2%}  (n={len(recalls)} with paragraph_norm GT)")
    if precisions:
        print(f"  Mean Precision:  {_avg(precisions):.2%}  (n={len(precisions)} with extractions)")
    print(f"  Total TP:        {total_tp}")
    print(f"  Total Loss:      {total_loss}  (paragraph_norm in GT, missed by extractor)")
    print(f"  Total Overcount: {total_overcount}  (extracted, not in paragraph_norm GT)")

    print(f"\n  {'Doc':<32} {'Rec':>6} {'Prec':>6} {'Expect':>7} {'Found':>6} {'TP':>4} {'Loss':>5} {'Over':>5}")
    print(f"  {'-' * 78}")
    for r in subset:
        rec = f"{r.recall:.0%}" if r.expected else "  -"
        prec = f"{r.precision:.0%}" if r.extracted else "  -"
        print(
            f"  {r.doc_id:<32} "
            f"{rec:>6} "
            f"{prec:>6} "
            f"{len(r.expected):>7} "
            f"{len(r.extracted):>6} "
            f"{len(r.true_positives):>4} "
            f"{len(r.gt_loss):>5} "
            f"{len(r.overcount):>5}"
        )


typ2 = [r for r in results if r.doc_type == "typ2"]
mixed = [r for r in results if r.doc_type == "mixed"]

summarize("TYP_2 (formal legal)", typ2)
summarize("Mixed", mixed)


# ---------------------------------------------------------------------------
# Loss-Diagnostik
# ---------------------------------------------------------------------------

print(f"\n{'=' * 80}")
print("Loss-Diagnostik: welche paragraph_norm Einträge gehen verloren?")
print(f"{'=' * 80}")

all_losses: list[tuple[str, str]] = []
for r in results:
    for norm in sorted(r.gt_loss):
        all_losses.append((r.doc_id, norm))

if all_losses:
    losses_by_norm: dict[str, list[str]] = defaultdict(list)
    for doc_id, norm in all_losses:
        losses_by_norm[norm].append(doc_id)

    print(f"\n  {len(all_losses)} Verluste über {len({d for d, _ in all_losses})} Dokumente")
    print(f"  ({len(losses_by_norm)} distinct norms)\n")
    for norm in sorted(losses_by_norm.keys()):
        docs = ", ".join(losses_by_norm[norm])
        print(f"  {norm:<40} → {docs}")
else:
    print("\n  Keine Verluste. Extractor hat alle paragraph_norm Einträge gefunden.")


# ---------------------------------------------------------------------------
# Overcount-Diagnostik
# ---------------------------------------------------------------------------

print(f"\n{'=' * 80}")
print("Overcount-Diagnostik: was findet der Extractor zusätzlich?")
print(f"{'=' * 80}")

all_overcounts: list[tuple[str, str]] = []
for r in results:
    for norm in sorted(r.overcount):
        all_overcounts.append((r.doc_id, norm))

if all_overcounts:
    over_by_norm: dict[str, list[str]] = defaultdict(list)
    for doc_id, norm in all_overcounts:
        over_by_norm[norm].append(doc_id)

    print(f"\n  {len(all_overcounts)} Overcounts über {len({d for d, _ in all_overcounts})} Dokumente")
    print(f"  ({len(over_by_norm)} distinct norms)\n")
    for norm in sorted(over_by_norm.keys()):
        docs = ", ".join(over_by_norm[norm])
        print(f"  {norm:<40} → {docs}")
else:
    print("\n  Kein Overcount. Extractor hat nur paragraph_norm Einträge gefunden.")


# ---------------------------------------------------------------------------
# Limitation-Diagnostik: documented Phase 2 roadmap
# ---------------------------------------------------------------------------

print(f"\n{'=' * 80}")
print("Limitation-Diagnostik: dokumentierte extractor_limitation Einträge")
print(f"{'=' * 80}")

total_limitation_entries = 0
for doc_id in sorted(GT_BY_DOC.keys()):
    gt = GT_BY_DOC[doc_id]
    if not gt.extractor_limitations:
        continue
    print(f"\n  {doc_id}: {len(gt.extractor_limitations)} Einträge")
    for entry in gt.extractor_limitations:
        total_limitation_entries += 1
        note = f"  ({entry.anmerkung})" if entry.anmerkung else ""
        print(f"    - {entry.norm}{note}")

if total_limitation_entries == 0:
    print("\n  Keine extractor_limitation Einträge in der GT.")
else:
    print(f"\n  Gesamt: {total_limitation_entries} dokumentierte Limitations (Phase 2 Roadmap)")


# ---------------------------------------------------------------------------
# Save detailed results
# ---------------------------------------------------------------------------

timestamp = datetime.now().strftime("%Y%m%d_%H%M")
output_path = RESULTS_PATH / f"norm_extraction_eval_{timestamp}.json"

output = [
    {
        "doc_id": r.doc_id,
        "doc_type": r.doc_type,
        "expected_paragraph_norms": sorted(r.expected),
        "extracted": sorted(r.extracted),
        "true_positives": sorted(r.true_positives),
        "gt_loss": sorted(r.gt_loss),
        "overcount": sorted(r.overcount),
        "recall": round(r.recall, 4),
        "precision": round(r.precision, 4),
    }
    for r in results
]

with open(output_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\nResults saved to {output_path}")