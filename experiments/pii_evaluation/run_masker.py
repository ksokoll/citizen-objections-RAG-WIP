"""Run the PresidioMasker over the evaluation corpus and serialize results.

Reads every .txt document from the extraction_evaluation corpus, runs the
PresidioMasker, and writes per-document results (masked text plus entity
counts) to results.json. This is the slow step (it loads the spaCy model);
the comparison against the ground truth is done separately by evaluate.py.

Usage:
    python experiments/pii_evaluation/run_masker.py
"""

from __future__ import annotations

import json
from pathlib import Path

from app.document_ingestion.presidio_masker import PresidioMasker

# The corpus lives in the extraction_evaluation experiment; the PII evaluation
# reuses it rather than duplicating the documents.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORPUS_DIR = _REPO_ROOT / "experiments" / "extraction_evaluation" / "data"
_GROUND_TRUTH = Path(__file__).resolve().parent / "ground_truth.json"
_RESULTS = Path(__file__).resolve().parent / "results.json"


def main() -> None:
    """Run the masker over every corpus document and serialize the results.

    The set of documents is taken from the ground-truth file, so the run and
    the evaluation operate on exactly the same documents.

    Raises:
        FileNotFoundError: If the ground-truth file or a referenced document
            is missing.
    """
    ground_truth = json.loads(_GROUND_TRUTH.read_text(encoding="utf-8"))
    masker = PresidioMasker()

    results: dict[str, dict[str, object]] = {}
    for entry in ground_truth["documents"]:
        relative_path = entry["file"]
        document_path = _CORPUS_DIR / relative_path
        if not document_path.exists():
            raise FileNotFoundError(f"Corpus document not found: {document_path}")

        text = document_path.read_text(encoding="utf-8")
        masking_result = masker.mask(text)
        results[relative_path] = {
            "masked_text": masking_result.text,
            "entity_counts": masking_result.entity_counts,
        }
        print(f"masked {relative_path}: {masking_result.entity_counts}")

    _RESULTS.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nWrote {len(results)} results to {_RESULTS}")


if __name__ == "__main__":
    main()