"""Hybrid Norm Coverage Eval Experiment, v3.

Extends the v2 Hybrid (regex citation list as orientation header hint)
with two additional deterministic preprocessing layers, each individually
togglable so that the effect of each layer can be isolated via ablation.

Three layers, all deterministic (no LLM in the preprocessing loop, no
schema modifications):

    USE_HEADER_HINT: Inherited from v2. A clearly delimited block at
        the top of the input listing the canonical citations as
        "Orientierungshilfe". Targets the original_zitat field by
        instructing the LLM to widen its span so relevant §-citations
        fall inside.

    USE_INLINE_TAGS: New in v3. After norm_extractor identifies §-
        citation positions, the wrapper inserts <NORM>...</NORM> tags
        around each citation in the body text (reverse-order insertion
        to keep upstream positions valid). The LLM sees the citations
        directly in the text flow rather than only as a separate list
        at the top, which addresses H2 (narrow zitate) by making the
        citations visually salient where they occur.

    USE_FEW_SHOT: New in v3. Two synthetic exemplary argument
        extractions are prepended to the prompt before the einspruch-
        text. The examples come from non-test legal domains (Bavarian
        nature conservation, North-Rhine Westphalia road law) so no
        data leakage from the test corpus. The examples demonstrate
        wide zitat spans that include the relevant §-citation and the
        verbatim-substring constraint.

By toggling the three flags independently, this script supports the
v3a / v3b / v3c ablation variants:

    v3a (tags only):       USE_HEADER_HINT=False, USE_INLINE_TAGS=True,  USE_FEW_SHOT=False
    v3b (header + tags):   USE_HEADER_HINT=True,  USE_INLINE_TAGS=True,  USE_FEW_SHOT=False
    v3c (full stack):      USE_HEADER_HINT=True,  USE_INLINE_TAGS=True,  USE_FEW_SHOT=True

Encapsulation discipline matches the v2 script. No production code
under src/app/triage/ is modified. The wrapper sits between the eval
loop and TriageService.

Result files carry a suffix that encodes the active toggles, so the
ablation runs do not overwrite each other.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Project-root path hook for src/ imports.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

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


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Layer toggles for ablation. Set independently to isolate each layer's
# contribution. The result filename encodes the active set so multiple
# ablation runs can coexist without overwriting each other.
USE_HEADER_HINT = True
USE_INLINE_TAGS = True
USE_FEW_SHOT = False
MODEL_NAME = "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Layer constants
# ---------------------------------------------------------------------------

_HEADER_OPEN = (
    "=== DETERMINISTISCH EXTRAHIERTE NORMEN ALS HILFE "
    "(NICHT ALS EINSPRUCHS-TEXT ANALYSIEREN) ===\n"
)
_HEADER_CLOSE = "\n=== EINSPRUCHS-TEXT (HIER ARGUMENTE EXTRAHIEREN) ===\n\n"

_HEADER_INSTRUCTION = (
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
    "sein.\n\n"
    "ZUSÄTZLICH: Im Volltext wurden die identifizierten Citations mit "
    "<NORM>...</NORM> Tags umrahmt, damit du sie im Textfluss leichter "
    "erkennen kannst. Die Tags sind Teil des verbatim-Texts und dürfen "
    "in deinen original_zitat-Spans enthalten sein.\n"
)

# Two synthetic few-shot examples drawn from legal domains not present
# in the test corpus (Bavarian nature conservation, North-Rhine
# Westphalia road law). The exemplary zitate are intentionally wide
# enough to include the §-citation alongside the argument text.
_FEW_SHOT_PREAMBLE = (
    "=== BEISPIELE ZUR ORIENTIERUNG (nicht den Test-Text betreffend) ===\n\n"
    "Beispiel 1 (Naturschutzrecht, Bayern):\n"
    "Eingabe-Auszug: 'Die geplante Erweiterung des Steinbruchs verletzt "
    "die Schutzgebietsausweisung nach <NORM>Art. 23 BayNatSchG</NORM>, "
    "ohne dass eine Befreiung erteilt wurde.'\n"
    "Erwartetes original_zitat (verbatim-Substring): "
    "'verletzt die Schutzgebietsausweisung nach <NORM>Art. 23 BayNatSchG</NORM>, "
    "ohne dass eine Befreiung erteilt wurde'\n\n"
    "Beispiel 2 (Straßenrecht, NRW):\n"
    "Eingabe-Auszug: 'Eine Widmung der Anliegerstraße ist nicht erfolgt "
    "(<NORM>§ 6 StrWG NRW</NORM>), gleichwohl wird sie im Erschließungs"
    "konzept als gewidmet behandelt.'\n"
    "Erwartetes original_zitat (verbatim-Substring): "
    "'Eine Widmung der Anliegerstraße ist nicht erfolgt "
    "(<NORM>§ 6 StrWG NRW</NORM>), gleichwohl wird sie im Erschließungs"
    "konzept als gewidmet behandelt'\n\n"
    "=== ENDE DER BEISPIELE ===\n\n"
)


# ---------------------------------------------------------------------------
# Preprocessing helpers
# ---------------------------------------------------------------------------


def _insert_inline_tags(text: str) -> str:
    """Insert <NORM>...</NORM> tags around each §-citation in the text.

    Runs the deterministic norm_extractor to identify citation positions,
    then inserts tags in reverse-position order so earlier positions
    remain valid as later positions are mutated. The citation text
    between the tags is byte-identical to the source span, which keeps
    the norm_extractor regex matchable after tag insertion and lets the
    LLM's chosen original_zitat substring-match against the tagged text.

    Args:
        text: The cleaned einspruchs-text.

    Returns:
        The same text with <NORM>...</NORM> tags surrounding each
        identified citation. If no citations are found, the text is
        returned unchanged.
    """
    norms = extract_norms(text)
    if not norms:
        return text

    # Sort by descending start position so insertions do not invalidate
    # later positions. Filter out norms with overlapping ranges to keep
    # the tagging non-nested. Position fields named per norm_extractor's
    # ExtrahierteNorm dataclass conventions.
    sorted_norms = sorted(norms, key=lambda n: n.start, reverse=True)

    result = text
    last_end_seen: int | None = None
    for norm in sorted_norms:
        if last_end_seen is not None and norm.end > last_end_seen:
            # Overlap with a more-rightward citation already tagged.
            # Skip to keep the markup well-formed.
            continue
        last_end_seen = norm.start
        result = result[: norm.start] + "<NORM>" + result[norm.start : norm.end] + "</NORM>" + result[norm.end :]

    return result


def _build_header_block(canonical_citations: list[str]) -> str:
    """Build the header-hint block listing canonical citations."""
    if not canonical_citations:
        return ""
    norms_list = "\n".join(f"- {c}" for c in canonical_citations)
    return (
        _HEADER_OPEN
        + _HEADER_INSTRUCTION.format(norms=norms_list)
        + _HEADER_CLOSE
    )


# ---------------------------------------------------------------------------
# Hybrid v3 wrapper
# ---------------------------------------------------------------------------


class HybridV3Wrapper:
    """Wraps TriageService with three togglable deterministic layers.

    Each layer can be enabled or disabled independently via the module-
    level USE_* flags. The wrapper composes the layers in a fixed order:

        Few-shot preamble (if enabled)
            +
        Header hint block with canonical citation list (if enabled)
            +
        Body text, optionally inline-tagged at §-citation positions

    No production code is modified. TriageService receives a composed
    string that begins (where applicable) with examples and the citation
    hint, followed by the einspruch-text (possibly with inline tags),
    and is otherwise unmodified.

    Attributes:
        _base: The wrapped TriageService instance.
    """

    def __init__(self, base_service: TriageService) -> None:
        """Initialize the wrapper around an existing TriageService.

        Args:
            base_service: The production TriageService instance to delegate to.
        """
        self._base = base_service

    def triage(self, text: str):
        """Compose the configured preprocessing layers, then delegate.

        The body text is left untouched if both USE_INLINE_TAGS and the
        norm_extractor returns no citations. The header and few-shot
        layers are no-ops if their respective flags are disabled.

        Args:
            text: The cleaned einspruchs-text passed in by the eval loop.

        Returns:
            The TriageResult produced by the wrapped service.
        """
        body = _insert_inline_tags(text) if USE_INLINE_TAGS else text

        header = ""
        if USE_HEADER_HINT:
            norms = extract_norms(text)
            canonical = sorted({n.canonical() for n in norms})
            header = _build_header_block(canonical)

        few_shot = _FEW_SHOT_PREAMBLE if USE_FEW_SHOT else ""

        composed = few_shot + header + body
        return self._base.triage(composed)


# ---------------------------------------------------------------------------
# Result persistence with toggle suffix
# ---------------------------------------------------------------------------


def _variant_suffix() -> str:
    """Build a filename suffix encoding the active toggles.

    Examples:
        all on  -> "header_tags_fewshot"
        v3a     -> "tags"
        v3b     -> "header_tags"
        all off -> "baseline_via_v3"

    Returns:
        Compact lowercase string suitable for a filename component.
    """
    parts: list[str] = []
    if USE_HEADER_HINT:
        parts.append("header")
    if USE_INLINE_TAGS:
        parts.append("tags")
    if USE_FEW_SHOT:
        parts.append("fewshot")
    if not parts:
        return "baseline_via_v3"
    return "_".join(parts)


def save_results_v3(results: list[DocResult], model_name: str) -> Path:
    """Persist results with a v3 variant tag in the filename."""
    RESULTS_PATH.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    safe_model = sanitize_model_name(model_name)
    suffix = _variant_suffix()
    output_path = (
        RESULTS_PATH
        / f"norm_coverage_eval_{safe_model}_v3_{suffix}_{timestamp}.json"
    )

    output = {
        "model": model_name,
        "experiment": f"hybrid_v3_{suffix}",
        "toggles": {
            "USE_HEADER_HINT": USE_HEADER_HINT,
            "USE_INLINE_TAGS": USE_INLINE_TAGS,
            "USE_FEW_SHOT": USE_FEW_SHOT,
        },
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
    """Run the v3 hybrid eval with the configured toggles."""
    load_dotenv()

    try:
        llm = build_llm_client(MODEL_NAME)
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    base_service = TriageService(llm=llm)
    service = HybridV3Wrapper(base_service)

    suffix = _variant_suffix()
    print(
        f"Running norm-coverage evaluation (model={MODEL_NAME}, "
        f"mode=HYBRID v3 [{suffix}])..."
    )
    print(
        f"  Toggles: header={USE_HEADER_HINT}, "
        f"tags={USE_INLINE_TAGS}, few_shot={USE_FEW_SHOT}"
    )
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

    output_path = save_results_v3(results, MODEL_NAME)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()