"""Evaluation script for Triage catalog identification.

v3: Catalog matching as primary metric. Norm extraction is no longer
the responsibility of the Triage step (handled by RAG layer downstream).

Per-document metrics:
- Catalog Recall: fraction of expected catalog_ids correctly assigned
- Catalog Precision: fraction of assigned catalog_ids that were expected
- Distractor Hit: whether any assigned catalog is a distractor
- Einwendungs-Typ Match: dominant expected type present in actual types
- Argument Count in Range: count within expected min/max
- Verified Rate: original_zitat substring presence after whitespace normalization

Aggregated:
- Macro-average per metric across document types
- Per-catalog precision/recall over all documents (incl. distractor diagnosis)
"""

import json
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from citizen_objections_rag.triage.catalog import KATALOG
from citizen_objections_rag.triage.prompts import ARGUMENT_EXTRACTION_PROMPT

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE = Path("extraction_evaluation")
DATA_TYP2 = BASE / "data" / "typ2"
DATA_TYP1 = BASE / "data" / "typ1"
DATA_MIXED = BASE / "data" / "mixed"
CATALOG_GT_PATH = BASE / "ground_truth" / "catalog_definition.json"
RESULTS_PATH = BASE / "results"
RESULTS_PATH.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_whitespace(s: str) -> str:
    """Collapse all whitespace runs to single spaces."""
    return " ".join(s.split())


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# ---------------------------------------------------------------------------
# Extraction Schema (must mirror Triage output contract)
# ---------------------------------------------------------------------------

class ExtrahiertesArgumentSchema(BaseModel):
    argument_text: str
    original_zitat: str
    catalog_id: str | None
    einwendungs_typ: str
    zitierte_normen: list[str]


class ExtractionResult(BaseModel):
    argumente: list[ExtrahiertesArgumentSchema]


def extract(text: str) -> ExtractionResult | None:
    """Run the Triage LLM call on a single document.

    Returns None on API failure so the eval loop can continue.
    """
    catalog_entries = "\n".join(
        f"- {e.catalog_id}: {e.beschreibung}" for e in KATALOG.values()
    )
    prompt = ARGUMENT_EXTRACTION_PROMPT.prompt.format(
        catalog_entries=catalog_entries,
        einwendung_text=text,
    )
    try:
        response = client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
            response_format=ExtractionResult,
        )
        return response.choices[0].message.parsed
    except Exception as e:
        print(f"  ERROR: API call failed: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Catalog GT Loader
# ---------------------------------------------------------------------------

with open(CATALOG_GT_PATH, encoding="utf-8") as f:
    catalog_data = json.load(f)

KATALOG_DEFINITION: dict = catalog_data["katalog"]
ASSIGNMENTS: list = catalog_data["expected_catalog_assignment"]
GT_BY_DOC: dict = {entry["dokument"]: entry for entry in ASSIGNMENTS}


def _compute_distractor_ids() -> set[str]:
    """Catalogs not referenced in any expected_catalog_ids.

    Computed dynamically so changes to the GT propagate automatically.
    """
    all_expected = set()
    for entry in ASSIGNMENTS:
        all_expected.update(entry.get("expected_catalog_ids", []))
    return set(KATALOG_DEFINITION.keys()) - all_expected


DISTRACTOR_IDS = _compute_distractor_ids()
print(f"Distractor catalogs (no expected occurrence): {sorted(DISTRACTOR_IDS)}\n")


# ---------------------------------------------------------------------------
# Result Dataclass
# ---------------------------------------------------------------------------

@dataclass
class DocumentResult:
    """Eval result for a single document on the catalog-matching task."""

    doc_id: str
    doc_type: str

    # Expected (from GT)
    expected_catalog_ids: set[str] = field(default_factory=set)
    expected_einwendungs_typ: str | None = None
    expected_argument_count_min: int = 0
    expected_argument_count_max: int = 0

    # Actual (from extraction)
    actual_catalog_ids: set[str] = field(default_factory=set)
    actual_einwendungs_typen: set[str] = field(default_factory=set)
    argument_count: int = 0
    verified_count: int = 0

    error: bool = False

    @property
    def missing_catalog_ids(self) -> set[str]:
        return self.expected_catalog_ids - self.actual_catalog_ids

    @property
    def hallucinated_catalog_ids(self) -> set[str]:
        return self.actual_catalog_ids - self.expected_catalog_ids

    @property
    def hit_distractors(self) -> set[str]:
        return self.actual_catalog_ids & DISTRACTOR_IDS

    @property
    def catalog_recall(self) -> float:
        """Vacuously 1.0 when no expectations (TYP_1)."""
        if not self.expected_catalog_ids:
            return 1.0
        return len(self.expected_catalog_ids & self.actual_catalog_ids) / len(
            self.expected_catalog_ids
        )

    @property
    def catalog_precision(self) -> float | None:
        """Undefined when nothing was assigned (returns None).

        Zero when assignments were made but none matched expectations.
        """
        if not self.actual_catalog_ids:
            return None
        return len(self.expected_catalog_ids & self.actual_catalog_ids) / len(
            self.actual_catalog_ids
        )

    @property
    def einwendungs_typ_match(self) -> bool:
        """For TYP_1 (no expected type): success means zero arguments.

        For TYP_2/Mixed: expected type must appear at least once in actual types.
        """
        if self.expected_einwendungs_typ is None:
            return self.argument_count == 0
        return self.expected_einwendungs_typ in self.actual_einwendungs_typen

    @property
    def argument_count_in_range(self) -> bool:
        return (
            self.expected_argument_count_min
            <= self.argument_count
            <= self.expected_argument_count_max
        )

    @property
    def verified_rate(self) -> float:
        """Vacuously 1.0 when there are no arguments to verify."""
        if self.argument_count == 0:
            return 1.0
        return self.verified_count / self.argument_count


# ---------------------------------------------------------------------------
# Evaluate single document
# ---------------------------------------------------------------------------

def evaluate_document(doc_id: str, doc_type: str, text: str) -> DocumentResult:
    """Run extraction on a document and compare against GT."""
    gt_entry = GT_BY_DOC.get(doc_id)
    if gt_entry is None:
        print(f"  WARNING: no GT for {doc_id}", file=sys.stderr)
        gt_entry = {}

    expected_catalog_ids = set(gt_entry.get("expected_catalog_ids", []))
    expected_einwendungs_typ = gt_entry.get("expected_einwendungs_typ")
    arg_count_range = gt_entry.get("expected_argument_count_range", [0, 0])

    result = DocumentResult(
        doc_id=doc_id,
        doc_type=doc_type,
        expected_catalog_ids=expected_catalog_ids,
        expected_einwendungs_typ=expected_einwendungs_typ,
        expected_argument_count_min=arg_count_range[0],
        expected_argument_count_max=arg_count_range[1],
    )

    extraction = extract(text)
    time.sleep(0.5)

    if extraction is None:
        result.error = True
        return result

    args = extraction.argumente

    actual_catalog_ids = {arg.catalog_id for arg in args if arg.catalog_id is not None}
    actual_einwendungs_typen = {arg.einwendungs_typ for arg in args}

    norm_text = normalize_whitespace(text)
    verified_count = sum(
        1 for arg in args
        if normalize_whitespace(arg.original_zitat) in norm_text
    )

    result.actual_catalog_ids = actual_catalog_ids
    result.actual_einwendungs_typen = actual_einwendungs_typen
    result.argument_count = len(args)
    result.verified_count = verified_count

    return result


# ---------------------------------------------------------------------------
# Run evaluation
# ---------------------------------------------------------------------------

results: list[DocumentResult] = []

for path in sorted(DATA_TYP2.glob("*.txt")):
    print(f"TYP_2: {path.stem}...")
    results.append(evaluate_document(path.stem, "typ2", path.read_text(encoding="utf-8")))

for path in sorted(DATA_TYP1.glob("*.txt")):
    print(f"TYP_1: {path.stem}...")
    results.append(evaluate_document(path.stem, "typ1", path.read_text(encoding="utf-8")))

for path in sorted(DATA_MIXED.glob("*.txt")):
    print(f"Mixed: {path.stem}...")
    results.append(evaluate_document(path.stem, "mixed", path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Summary per doc_type
# ---------------------------------------------------------------------------

def summarize(label: str, subset: list[DocumentResult]) -> None:
    if not subset:
        return
    valid = [r for r in subset if not r.error]
    if not valid:
        print(f"\n{label}: all errors")
        return

    recalls = [r.catalog_recall for r in valid]
    precisions = [r.catalog_precision for r in valid if r.catalog_precision is not None]
    typ_matches = [r.einwendungs_typ_match for r in valid]
    count_in_range = [r.argument_count_in_range for r in valid]
    verified_rates = [r.verified_rate for r in valid if r.argument_count > 0]
    distractor_hits = sum(1 for r in valid if r.hit_distractors)

    print(f"\n{'=' * 80}")
    print(f"{label} (n={len(subset)}, errors={len(subset) - len(valid)})")
    print(f"  Catalog Recall:       {_avg(recalls):.2%}")
    if precisions:
        print(f"  Catalog Precision:    {_avg(precisions):.2%}  (n={len(precisions)} with assignments)")
    else:
        print(f"  Catalog Precision:    N/A (no assignments)")
    print(f"  Einwendungs-Typ:      {sum(typ_matches) / len(typ_matches):.2%}  ({sum(typ_matches)}/{len(typ_matches)})")
    print(f"  Arg-Count in Range:   {sum(count_in_range) / len(count_in_range):.2%}  ({sum(count_in_range)}/{len(count_in_range)})")
    if verified_rates:
        print(f"  Verified-Rate:        {_avg(verified_rates):.2%}  (n={len(verified_rates)} with args)")
    print(f"  Distractor-Hits:      {distractor_hits}/{len(valid)}  (target: 0)")

    print(f"\n  {'Doc':<32} {'Rec':>5} {'Prec':>6} {'Typ':>4} {'Range':>6} {'Distr':>10}")
    print(f"  {'-' * 70}")
    for r in subset:
        if r.error:
            print(f"  {r.doc_id:<32} ERR")
            continue
        prec = f"{r.catalog_precision:.0%}" if r.catalog_precision is not None else "  -"
        distr_marker = ",".join(sorted(r.hit_distractors)) if r.hit_distractors else "-"
        typ_marker = "OK" if r.einwendungs_typ_match else "X"
        range_marker = "OK" if r.argument_count_in_range else "X"
        print(
            f"  {r.doc_id:<32} "
            f"{r.catalog_recall:>5.0%} "
            f"{prec:>6} "
            f"{typ_marker:>4} "
            f"{range_marker:>6} "
            f"{distr_marker:>10}"
        )


typ2 = [r for r in results if r.doc_type == "typ2"]
typ1 = [r for r in results if r.doc_type == "typ1"]
mixed = [r for r in results if r.doc_type == "mixed"]

summarize("TYP_2 (formal legal)", typ2)
summarize("TYP_1 (personal statement)", typ1)
summarize("Mixed", mixed)


# ---------------------------------------------------------------------------
# Per-Catalog confusion-style aggregation
# ---------------------------------------------------------------------------

print(f"\n{'=' * 80}")
print("Per-Catalog Diagnostik (über alle Dokumente)")
print(f"{'=' * 80}")

per_catalog: dict = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
for r in results:
    if r.error:
        continue
    for cid in r.expected_catalog_ids & r.actual_catalog_ids:
        per_catalog[cid]["tp"] += 1
    for cid in r.expected_catalog_ids - r.actual_catalog_ids:
        per_catalog[cid]["fn"] += 1
    for cid in r.actual_catalog_ids - r.expected_catalog_ids:
        per_catalog[cid]["fp"] += 1

print(f"\n  {'Catalog':<8} {'TP':>4} {'FP':>4} {'FN':>4} {'Recall':>8} {'Precision':>11}  Note")
print(f"  {'-' * 66}")
for cid in sorted(KATALOG_DEFINITION.keys()):
    stats = per_catalog[cid]
    tp, fp, fn = stats["tp"], stats["fp"], stats["fn"]
    recall = tp / (tp + fn) if (tp + fn) > 0 else math.nan
    precision = tp / (tp + fp) if (tp + fp) > 0 else math.nan
    is_distr = "DISTRACTOR" if cid in DISTRACTOR_IDS else ""
    recall_str = f"{recall:.0%}" if not math.isnan(recall) else "  -"
    precision_str = f"{precision:.0%}" if not math.isnan(precision) else "  -"
    print(f"  {cid:<8} {tp:>4} {fp:>4} {fn:>4} {recall_str:>8} {precision_str:>11}  {is_distr}")


# ---------------------------------------------------------------------------
# Save detailed results
# ---------------------------------------------------------------------------

timestamp = datetime.now().strftime("%Y%m%d_%H%M")
output_path = RESULTS_PATH / f"catalog_eval_results_{timestamp}.json"

output = [
    {
        "doc_id": r.doc_id,
        "doc_type": r.doc_type,
        "error": r.error,
        "expected_catalog_ids": sorted(r.expected_catalog_ids),
        "actual_catalog_ids": sorted(r.actual_catalog_ids),
        "missing": sorted(r.missing_catalog_ids),
        "hallucinated": sorted(r.hallucinated_catalog_ids),
        "hit_distractors": sorted(r.hit_distractors),
        "expected_einwendungs_typ": r.expected_einwendungs_typ,
        "actual_einwendungs_typen": sorted(r.actual_einwendungs_typen),
        "einwendungs_typ_match": r.einwendungs_typ_match,
        "argument_count": r.argument_count,
        "expected_argument_count_range": [
            r.expected_argument_count_min,
            r.expected_argument_count_max,
        ],
        "argument_count_in_range": r.argument_count_in_range,
        "verified_count": r.verified_count,
        "verified_rate": round(r.verified_rate, 4),
        "catalog_recall": round(r.catalog_recall, 4),
        "catalog_precision": (
            round(r.catalog_precision, 4) if r.catalog_precision is not None else None
        ),
    }
    for r in results
]

with open(output_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\nResults saved to {output_path}")