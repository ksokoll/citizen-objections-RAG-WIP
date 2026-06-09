"""Evaluate masker results against the PII ground truth (Weg A).

Compares results.json (produced by run_masker.py) against ground_truth.json
on two axes:

- Recall: every token in names_must_mask must be absent from the masked text.
  Presence is checked with word boundaries, so a name token is only counted
  as still present when it appears as a standalone word, not as a substring of
  an unrelated word (e.g. "Stein" inside "Steinbruch" does not count as a
  leaked "Stein"). A name still present as a word is a recall miss.
- Precision: every term in must_survive must still be present in the masked
  text. This is a plain substring check, because must_survive terms are often
  multi-word with punctuation (e.g. "Kanzlei Franken & Stein"); spelling must
  match the document exactly, which is maintained in the ground truth.

The two axes are reported separately because they capture opposite failure
modes and must not be averaged into a single opaque number. Recall is the
safety-critical axis (a leaked name), precision the utility axis (destroyed
context for the downstream Triage).

Usage:
    python experiments/pii_evaluation/evaluate.py
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_DIR = Path(__file__).resolve().parent
_GROUND_TRUTH = _DIR / "ground_truth.json"
_RESULTS = _DIR / "results.json"


@dataclass
class DocumentReport:
    """Per-document evaluation outcome.

    Attributes:
        file: Relative path of the document.
        recall_total: Number of name tokens that should have been masked.
        recall_hits: Number of those tokens actually absent from masked text.
        leaked_names: Name tokens still present as words in the masked text.
        precision_total: Number of terms that should have survived.
        precision_hits: Number of those terms actually present.
        destroyed_terms: must_survive terms missing from the masked text.
    """

    file: str
    recall_total: int
    recall_hits: int
    leaked_names: list[str] = field(default_factory=list)
    precision_total: int = 0
    precision_hits: int = 0
    destroyed_terms: list[str] = field(default_factory=list)


def _is_present_as_word(token: str, text: str) -> bool:
    """Return whether token appears as a standalone word in text.

    Uses word boundaries so that a name token is not matched as a substring of
    an unrelated longer word. Falls back to a plain substring check if the
    token contains characters that cannot form a regex word boundary (e.g. a
    trailing hyphen), which keeps the check safe for unusual tokens.

    Args:
        token: The name token to look for.
        text: The masked text to search.

    Returns:
        True if the token is present as a word, False otherwise.
    """
    pattern = r"\b" + re.escape(token) + r"\b"
    try:
        return re.search(pattern, text) is not None
    except re.error:
        return token in text


def _evaluate_document(
    entry: dict[str, object], masked_text: str
) -> DocumentReport:
    """Evaluate one document's masked text against its ground-truth entry.

    Args:
        entry: Ground-truth entry with names_must_mask and must_survive.
        masked_text: The masked text produced by the masker.

    Returns:
        A DocumentReport with recall and precision outcomes.
    """
    names_must_mask = list(entry.get("names_must_mask", []))
    must_survive = list(entry.get("must_survive", []))

    leaked = [
        name for name in names_must_mask if _is_present_as_word(name, masked_text)
    ]
    destroyed = [term for term in must_survive if term not in masked_text]

    return DocumentReport(
        file=str(entry["file"]),
        recall_total=len(names_must_mask),
        recall_hits=len(names_must_mask) - len(leaked),
        leaked_names=leaked,
        precision_total=len(must_survive),
        precision_hits=len(must_survive) - len(destroyed),
        destroyed_terms=destroyed,
    )


def _print_report(reports: list[DocumentReport]) -> None:
    """Print per-document and aggregate recall and precision.

    Args:
        reports: Per-document evaluation reports.
    """
    recall_total = sum(r.recall_total for r in reports)
    recall_hits = sum(r.recall_hits for r in reports)
    precision_total = sum(r.precision_total for r in reports)
    precision_hits = sum(r.precision_hits for r in reports)

    print("Per-document results:\n")
    for r in reports:
        recall_pct = (
            100.0 * r.recall_hits / r.recall_total if r.recall_total else 100.0
        )
        precision_pct = (
            100.0 * r.precision_hits / r.precision_total
            if r.precision_total
            else 100.0
        )
        print(
            f"  {r.file:32} recall {r.recall_hits}/{r.recall_total} "
            f"({recall_pct:5.1f}%)  precision {r.precision_hits}/"
            f"{r.precision_total} ({precision_pct:5.1f}%)"
        )
        if r.leaked_names:
            print(f"      LEAKED names: {r.leaked_names}")
        if r.destroyed_terms:
            print(f"      DESTROYED terms: {r.destroyed_terms}")

    overall_recall = 100.0 * recall_hits / recall_total if recall_total else 100.0
    overall_precision = (
        100.0 * precision_hits / precision_total if precision_total else 100.0
    )
    print("\nAggregate:")
    print(f"  Recall   (names masked):    {recall_hits}/{recall_total} "
          f"({overall_recall:.1f}%)")
    print(f"  Precision (terms survived): {precision_hits}/{precision_total} "
          f"({overall_precision:.1f}%)")


def main() -> None:
    """Load ground truth and results, evaluate, and print the report.

    Raises:
        FileNotFoundError: If results.json is missing (run run_masker.py first).
    """
    if not _RESULTS.exists():
        raise FileNotFoundError(
            f"{_RESULTS} not found. Run run_masker.py first."
        )

    ground_truth = json.loads(_GROUND_TRUTH.read_text(encoding="utf-8"))
    results = json.loads(_RESULTS.read_text(encoding="utf-8"))

    reports: list[DocumentReport] = []
    for entry in ground_truth["documents"]:
        relative_path = str(entry["file"])
        if relative_path not in results:
            raise FileNotFoundError(
                f"No result for {relative_path}; re-run run_masker.py."
            )
        masked_text = str(results[relative_path]["masked_text"])
        reports.append(_evaluate_document(entry, masked_text))

    _print_report(reports)


if __name__ == "__main__":
    main()