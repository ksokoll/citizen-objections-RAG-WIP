"""Evaluation script for deterministic norm extraction.

Measures the recall of triage.norm_extractor against the human-annotated
ground truth (explizit_zitierte_normen only, NOT inferred norms).

Unlike extraction_evaluation.py, this script does NOT call any LLM. The
norm extractor is deterministic and runs in milliseconds, so the eval is
free to run in CI on every commit.

Per-document metrics:
- Recall: |extracted ∩ expected| / |expected|, vacuously 1.0 if no expectations
- Precision: |extracted ∩ expected| / |extracted|, vacuously 1.0 if nothing extracted
- GT-Loss: norms in GT but missed by extractor (false negatives, concrete misses)
- Overcount: norms found by extractor but not in GT (plausibility check, not a hard error)

Aggregated:
- Mean recall and precision across documents
- Total TP, GT-Loss, and Overcount counts
- Loss-Diagnostik: explicit list of every missed GT norm with source document
- Overcount-Diagnostik: explicit list of every extra extracted norm

Loss-Diagnostik is useful for spotting systematic gaps (FFH-Richtlinie not
in whitelist, §§-chains over 10 char filler). Overcount catches both
extractor false-positives and GT annotator oversights.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from citizen_objections_rag.triage.norm_extractor import extract_canonical_norms

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
    """Collapse whitespace for robust string-set comparison.

    Ensures '§ 8 Abs. 2 BauGB' and '§ 8 Abs.  2 BauGB' compare equal.
    Both extractor output and GT pass through this before set operations.
    """
    return " ".join(s.split())


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# ---------------------------------------------------------------------------
# Ground truth loader
# ---------------------------------------------------------------------------

def _load_gt() -> dict[str, set[str]]:
    """Load ground truth: doc_id -> normalized set of expected norm strings.

    Tolerates both list-of-docs and dict-with-documents-key shapes. Each
    document's explizit_zitierte_normen entries may be plain strings or
    objects with a 'norm' key (the latter also carries fundstelle).
    """
    with open(GT_PATH, encoding="utf-8") as f:
        gt_data = json.load(f)

    documents = gt_data if isinstance(gt_data, list) else gt_data.get("documents", [])

    by_doc: dict[str, set[str]] = {}
    for entry in documents:
        doc_id = entry.get("doc_id") or entry.get("dokument")
        if doc_id is None:
            continue

        explizit = entry.get("explizit_zitierte_normen", [])

        norms: set[str] = set()
        for item in explizit:
            if isinstance(item, str):
                norms.add(_normalize_norm_string(item))
            elif isinstance(item, dict) and "norm" in item:
                norms.add(_normalize_norm_string(item["norm"]))

        by_doc[doc_id] = norms

    return by_doc


GT_NORMS = _load_gt()
print(f"Loaded ground truth for {len(GT_NORMS)} documents")
print(f"Total expected norms: {sum(len(norms) for norms in GT_NORMS.values())}\n")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class NormExtractionResult:
    """Eval result for norm extraction on a single document."""

    doc_id: str
    doc_type: str
    extracted: set[str]
    expected: set[str]

    @property
    def true_positives(self) -> set[str]:
        return self.extracted & self.expected

    @property
    def gt_loss(self) -> set[str]:
        """Norms in GT but missed by extractor (false negatives)."""
        return self.expected - self.extracted

    @property
    def overcount(self) -> set[str]:
        """Norms found by extractor but not in GT.

        Not necessarily errors: may indicate GT annotator oversights of norms
        that genuinely appear in the source text.
        """
        return self.extracted - self.expected

    @property
    def recall(self) -> float:
        """Vacuously 1.0 when no GT expectations."""
        if not self.expected:
            return 1.0
        return len(self.true_positives) / len(self.expected)

    @property
    def precision(self) -> float:
        """Vacuously 1.0 when nothing extracted."""
        if not self.extracted:
            return 1.0
        return len(self.true_positives) / len(self.extracted)


# ---------------------------------------------------------------------------
# Per-document evaluation
# ---------------------------------------------------------------------------

def evaluate_document(doc_id: str, doc_type: str, text: str) -> NormExtractionResult:
    """Run extractor on text, normalize results, compare against GT."""
    extracted_raw = extract_canonical_norms(text)
    extracted = {_normalize_norm_string(n) for n in extracted_raw}
    expected = GT_NORMS.get(doc_id, set())

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
    print(f"{label} (n={len(subset)})")
    if recalls:
        print(f"  Mean Recall:    {_avg(recalls):.2%}  (n={len(recalls)} with GT)")
    if precisions:
        print(f"  Mean Precision: {_avg(precisions):.2%}  (n={len(precisions)} with extractions)")
    print(f"  Total TP:       {total_tp}")
    print(f"  Total Loss:     {total_loss}  (norms in GT, missed by extractor)")
    print(f"  Total Overcount: {total_overcount}  (norms extracted, not in GT)")

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
# Loss-Diagnostik: which norms are systematically missed?
# ---------------------------------------------------------------------------

print(f"\n{'=' * 80}")
print("Loss-Diagnostik: welche Normen gehen verloren?")
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
        docs = losses_by_norm[norm]
        doc_list = ", ".join(docs)
        print(f"  {norm:<40} → {doc_list}")
else:
    print("\n  Keine Verluste. Extractor hat alle GT-Normen gefunden.")


# ---------------------------------------------------------------------------
# Overcount-Diagnostik: which extra norms is the extractor picking up?
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
        docs = over_by_norm[norm]
        doc_list = ", ".join(docs)
        print(f"  {norm:<40} → {doc_list}")
else:
    print("\n  Kein Overcount. Extractor hat nur GT-Normen gefunden.")


# ---------------------------------------------------------------------------
# Save detailed results
# ---------------------------------------------------------------------------

timestamp = datetime.now().strftime("%Y%m%d_%H%M")
output_path = RESULTS_PATH / f"norm_extraction_eval_{timestamp}.json"

output = [
    {
        "doc_id": r.doc_id,
        "doc_type": r.doc_type,
        "expected": sorted(r.expected),
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