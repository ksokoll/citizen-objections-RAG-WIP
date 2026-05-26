"""Hybrid Norm Coverage Eval Experiment.

Tests whether providing the deterministic norm_extractor output as a
non-binding orientation hint in the LLM prompt helps smaller models choose
wider original_zitat spans that include the relevant § citations. Wider
zitate close more position-based assignments under Option Y, which would
reduce the model-dependent assignment loss observed in the baseline runs.

The hint targets the field 'original_zitat' which the LLM actually controls,
not 'zitierte_normen' which is populated deterministically by the position-
based assignment in TriageService._build_extrahiertes_argument. The list of
detected norms is framed as orientation help, not as authoritative truth,
because the regex coverage is not provably exhaustive and the LLM should
retain its own judgment.

Encapsulated experiment: no production code (src/app/triage/) is modified.
The wrapper class HybridTriageWrapper prepends a clearly delimited hint
block in front of the einspruchs-text before passing it to TriageService.
The production prompt and schema remain unchanged. All evaluation helpers
(DocResult, evaluate_doc, all reporters) are imported from the baseline
norm_coverage_eval module.

Design choices recorded:
    Hint target: original_zitat (field the LLM controls). Telling the LLM
        to widen its zitat-span so the relevant § citation falls inside is
        an indirect lever on the Option Y position-based assignment.
    Hint framing: orientation help, not constraint. The list is presented
        as possibly incomplete so the LLM does not over-restrict itself.
    Hint payload: canonical citation strings only, no positions or context
        snippets. Volltext follows the hint, so the LLM can localize each
        citation in context if needed.
    Verification: argument_verified (ADR-006 Layer 1) still works because
        the original text remains a substring of the augmented text. Any
        original_zitat the LLM selects from the einspruch will still match
        via substring.

Results are written to the same results/ directory as baseline runs, with
a "_hybrid" suffix in the filename to keep comparisons unambiguous.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# Project-root path hook (same rationale as baseline script): make src/ importable
# regardless of where the script is invoked from. Mirrors the baseline so that
# both scripts behave identically when run from the repo root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

# Sibling import. Both scripts live in experiments/extraction_evaluation/script/,
# so the baseline module is on sys.path when this script runs from there. All
# evaluation helpers are reused unchanged; only the service factory differs.
from norm_coverage_eval import (  # noqa: E402
    DATA_MIXED,
    DATA_TYP2,
    RATE_LIMIT_SLEEP_S,
    RESULTS_PATH,
    DocResult,
    build_llm_client,
    evaluate_doc,
    print_aggregate,
    print_bottleneck_analysis,
    print_citation_frequency,
    print_doc_table,
    print_zero_extraction_diagnostics,
    sanitize_model_name,
)

from app.triage.norm_extractor import extract_norms  # noqa: E402
from app.triage.service import TriageService  # noqa: E402

import json  # noqa: E402
from datetime import datetime  # noqa: E402


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# When False, this script runs the baseline TriageService unchanged and
# produces a result file that should match the baseline run for the same
# model. Useful for sanity-checking the wrapper plumbing before assigning
# results to the hint vs. baseline comparison.
USE_HYBRID = True
MODEL_NAME = "gpt-5.5"


# ---------------------------------------------------------------------------
# Hybrid wrapper
# ---------------------------------------------------------------------------


class HybridTriageWrapper:
    """Wraps TriageService to inject a regex-derived norm hint into the prompt.

    The wrapper sits between the eval loop and the production TriageService.
    For each document, it runs extract_norms on the clean text first, then
    prepends a delimited hint block listing the canonical citations before
    calling the underlying service. The production prompt template and
    Pydantic schema are not modified; only the input text is augmented.

    The hint instructs the LLM to widen its 'original_zitat' selection so
    that the relevant § citation falls inside the verbatim zitat span. This
    targets the field the LLM actually controls in the production schema.
    The downstream Option Y position-based assignment then has a larger
    span to overlap citation positions against. The list is framed as an
    orientation aid (possibly incomplete), not as a hard constraint, so
    the LLM is not pushed into over-relying on the regex output.

    Attributes:
        _base: The wrapped TriageService instance whose triage() is called.
    """

    _HINT_HEADER = (
        "=== DETERMINISTISCH EXTRAHIERTE NORMEN ALS HILFE "
        "(NICHT ALS EINSPRUCHS-TEXT ANALYSIEREN) ===\n"
    )
    _HINT_FOOTER = "\n=== EINSPRUCHS-TEXT (HIER ARGUMENTE EXTRAHIEREN) ===\n\n"

    _INSTRUCTION = (
        "Im untenstehenden Einspruchs-Text wurden durch eine deterministische "
        "Regex-Analyse die folgenden Paragraphen-Zitate identifiziert. Die "
        "Liste dient als Orientierungshilfe und ist möglicherweise nicht "
        "vollständig:\n\n"
        "{norms}\n\n"
        "ANWEISUNG zur Wahl des Feldes 'original_zitat':\n"
        "1. Wenn ein extrahiertes Argument auf eine § Citation Bezug nimmt, "
        "wähle deinen Zitat-Span so, dass die zugehörige Citation im "
        "verbatim-Zitat enthalten ist. Schneide den Span nicht so, dass die "
        "§ Citation aus dem Zitat herausfällt.\n"
        "2. Die obige Liste kann als Anhaltspunkt dienen, wo solche "
        "Citations im Text stehen. Sie ersetzt jedoch keine eigene "
        "Beurteilung. Du kannst auch Citations berücksichtigen, die im "
        "Text vorkommen, aber nicht in der Liste enthalten sind.\n"
        "3. Die übrigen Anforderungen an 'original_zitat' bleiben "
        "unverändert: das Zitat muss ein exakter Substring des Volltexts "
        "sein.\n"
    )

    def __init__(self, base_service: TriageService) -> None:
        """Initialize the wrapper around an existing TriageService.

        Args:
            base_service: The production TriageService instance to delegate to.
        """
        self._base = base_service

    def triage(self, text: str):
        """Augment the input text with a norms hint, then delegate to base.

        If the deterministic extractor finds no citations, the input is
        passed through unchanged. The wrapper adds no value in that case
        and skipping the augmentation keeps prompt token usage minimal.

        Args:
            text: The cleaned einspruchs-text passed in by the eval loop.

        Returns:
            The TriageResult produced by the wrapped service. The return
            type intentionally mirrors TriageService.triage so that the
            eval helpers can consume it without changes.
        """
        norms = extract_norms(text)
        canonical = sorted({n.canonical() for n in norms})

        if not canonical:
            return self._base.triage(text)

        hint_body = self._INSTRUCTION.format(
            norms="\n".join(f"- {c}" for c in canonical)
        )
        augmented = (
            self._HINT_HEADER
            + hint_body
            + self._HINT_FOOTER
            + text
        )
        return self._base.triage(augmented)


# ---------------------------------------------------------------------------
# Result persistence (hybrid-tagged filenames)
# ---------------------------------------------------------------------------


def save_results_hybrid(results: list[DocResult], model_name: str) -> Path:
    """Persist results with a hybrid tag in the filename.

    Mirrors the baseline save_results structure so result files are
    schema-compatible. The only differences are the filename suffix and
    a top-level "experiment" key in the JSON so downstream comparison
    scripts can distinguish runs without parsing the filename.

    Args:
        results: Per-document evaluation results from the eval loop.
        model_name: Model identifier used for this run.

    Returns:
        The path of the written JSON file.
    """
    RESULTS_PATH.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    safe_model = sanitize_model_name(model_name)
    experiment_tag = "hybrid" if USE_HYBRID else "baseline_via_hybrid_script"
    output_path = (
        RESULTS_PATH
        / f"norm_coverage_eval_{safe_model}_{experiment_tag}_{timestamp}.json"
    )

    output = {
        "model": model_name,
        "experiment": experiment_tag,
        "timestamp": timestamp,
        "documents": [
            {
                "doc_id": r.doc_id,
                "doc_type": r.doc_type,
                "error": r.error,
                "error_msg": r.error_msg,
                "expected_count": len(r.expected),
                "assigned_count": len(r.extracted_assigned),
                "doc_level_count": len(r.extracted_doc_level),
                "argument_count": r.argument_count,
                "argument_verified_count": r.argument_verified_count,
                "zitat_lengths": r.zitat_lengths,
                "zitate_with_paragraph_count": r.zitate_with_paragraph_count,
                "expected": sorted(r.expected),
                "extracted_assigned": sorted(r.extracted_assigned),
                "extracted_doc_level": sorted(r.extracted_doc_level),
                "tp_assigned": sorted(r.tp_assigned),
                "fp_assigned": sorted(r.fp_assigned),
                "fn_assigned": sorted(r.fn_assigned),
                "fn_lost_by_assignment": sorted(r.fn_lost_by_assignment),
                "fn_truly_missing": sorted(r.fn_truly_missing),
                "assignment_loss": sorted(r.assignment_loss),
                "precision_assigned": (
                    round(r.precision_assigned, 4)
                    if r.precision_assigned is not None
                    else None
                ),
                "recall_assigned": (
                    round(r.recall_assigned, 4)
                    if r.recall_assigned is not None
                    else None
                ),
                "f1_assigned": (
                    round(r.f1_assigned, 4) if r.f1_assigned is not None else None
                ),
                "precision_doc": (
                    round(r.precision_doc, 4)
                    if r.precision_doc is not None
                    else None
                ),
                "recall_doc": (
                    round(r.recall_doc, 4) if r.recall_doc is not None else None
                ),
                "notes": r.notes,
            }
            for r in results
        ],
    }

    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)

    return output_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the hybrid eval and print/save results."""
    load_dotenv()

    try:
        llm = build_llm_client(MODEL_NAME)
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    base_service = TriageService(llm=llm)
    service = HybridTriageWrapper(base_service) if USE_HYBRID else base_service

    mode = "HYBRID (with regex norm hint)" if USE_HYBRID else "BASELINE (no hint)"
    print(f"Running norm-coverage evaluation (model={MODEL_NAME}, mode={mode})...")
    print()

    results: list[DocResult] = []

    for path in sorted(DATA_TYP2.glob("einspruch_*.txt")):
        print(f"  TYP_2: {path.stem}...")
        text = path.read_text(encoding="utf-8")
        results.append(evaluate_doc(path.stem, "typ2", text, service))
        time.sleep(RATE_LIMIT_SLEEP_S)

    for path in sorted(DATA_MIXED.glob("einspruch_*_mixed.txt")):
        print(f"  Mixed: {path.stem}...")
        text = path.read_text(encoding="utf-8")
        results.append(evaluate_doc(path.stem, "mixed", text, service))
        time.sleep(RATE_LIMIT_SLEEP_S)

    typ2 = [r for r in results if r.doc_type == "typ2"]
    mixed = [r for r in results if r.doc_type == "mixed"]

    print_doc_table("TYP_2 (formal legal documents)", typ2)
    print_aggregate("TYP_2", typ2)
    print_doc_table("Mixed (formal + personal header)", mixed)
    print_aggregate("Mixed", mixed)
    print()
    print_aggregate("Overall (TYP_2 + Mixed)", results)

    print_bottleneck_analysis(results)
    print_zero_extraction_diagnostics(results)
    print_citation_frequency(results)

    output_path = save_results_hybrid(results, MODEL_NAME)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()