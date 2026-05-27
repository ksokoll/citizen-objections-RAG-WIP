"""Norm Extractor Benchmark.

Compares three deterministic norm-extraction approaches on the Phase A
evaluation corpus, plus a False-Positive-resilience pass on the TYP_1
informal citizen letters.

The three extractors:
    A) Custom: the project's own norm_extractor (9-Gesetz whitelist,
       i.V.m. chain handling, canonical-form rendering).
    B) OpenLegalData Regex: rule-based extraction from the
       legal-reference-extraction package, used in production at
       de.openlegaldata.io. Permissive license for the Regex component.
    C) OpenLegalData Transformer: EuroBERT-210m fine-tune from the
       openlegaldata/legal-reference-extraction-base-de model on
       HuggingFace. Distributed under CC BY-NC 4.0; download is opt-in
       via the RUN_TRANSFORMER flag below.

Two evaluation regimes:
    Phase A docs (7 TYP_2 + 3 Mixed): primary recall and precision
        measurement against the must_retrieve subset of the ground
        truth used in the Phase A Hybrid evaluation.
    TYP_1 docs (10 informal citizen letters): false-positive
        resilience. Expected extraction count is zero; any non-empty
        output is a hallucination signal.

Two comparison modes per extractor:
    Raw: every citation the extractor emits, regardless of Gesetz.
        Shows out-of-the-box capability.
    Whitelisted to 9 Gesetz: post-filter to BAUGB, BAUNVO, BIMSCHG,
        BNATSCHG, ENWG, VWGO, WASTRG, WHG, WPG. Fair apples-to-apples
        comparison against Custom which is whitelist-constrained.

License note for Transformer mode:
    The openlegaldata/legal-reference-extraction-base-de model is
    licensed CC BY-NC 4.0. This benchmark constitutes research and
    evaluation use, which falls within the non-commercial scope. For
    production deployment in a commercial setting, the Custom
    extractor or the OpenLegalData Regex variant would be the
    relevant choices.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv

# Project-root path hook so the script can be invoked from anywhere.
# Mirrors the pattern used in norm_coverage_eval and norm_coverage_eval_hybrid.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from app.triage.norm_extractor import extract_norms  # noqa: E402


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# When True, the Transformer-mode extractor is loaded and benchmarked.
# Setting this to False keeps the run lightweight and skips the EuroBERT
# download (about 800 MB on first run).
RUN_TRANSFORMER = True

# The nine Gesetz keys that the Custom extractor supports. The benchmark
# uses this set to compute the "Whitelisted" comparison columns for
# OpenLegalData outputs.
WHITELIST_GESETZE: set[str] = {
    "BauGB",
    "BauNVO",
    "BImSchG",
    "BNatSchG",
    "EnWG",
    "VwGO",
    "WaStrG",
    "WHG",
    "WPG",
}

BASE = Path("experiments/extraction_evaluation")
DATA_TYP1 = BASE / "data" / "typ1"
DATA_TYP2 = BASE / "data" / "typ2"
DATA_MIXED = BASE / "data" / "mixed"
GT_DIR = BASE / "ground_truth" / "retrieval_gt"
RESULTS_PATH = BASE / "results"


# ---------------------------------------------------------------------------
# Extractor protocol
# ---------------------------------------------------------------------------


class Extractor(Protocol):
    """Common interface every benchmark extractor implements.

    Each extractor consumes the raw cleaned text and produces a list of
    canonical citation strings in the project's standard format (for
    example "§ 9 Abs. 1 Nr. 1 WHG"). Adapters wrap the upstream library
    outputs into this contract.
    """

    name: str

    def extract(self, text: str) -> list[str]: ...


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class CustomExtractor:
    """Adapter for the project's own norm_extractor."""

    name = "Custom"

    def extract(self, text: str) -> list[str]:
        norms = extract_norms(text)
        return [n.canonical() for n in norms]


class OpenLegalDataRegexExtractor:
    """Adapter for the OpenLegalData Regex-mode extractor.

    The package is installed as legal-reference-extraction on PyPI but
    imports as refex. The orchestrator exposes a CitationExtractor whose
    extract() method returns a Result object containing a list of
    Citation entries, each with a .type ("law" or "case") and a .span
    holding the matched .text. We parse the span text into the project's
    canonical form so string equality against the ground truth works.
    """

    name = "OLD-Regex"

    def __init__(self) -> None:
        from refex.orchestrator import CitationExtractor

        self._extractor = CitationExtractor()

    def extract(self, text: str) -> list[str]:
        results: list[str] = []
        result = self._extractor.extract(text)
        for citation in result.citations:
            if getattr(citation, "type", None) != "law":
                continue
            span_text = getattr(getattr(citation, "span", None), "text", None)
            if span_text is None:
                continue
            canonical = _parse_span_to_canonical(span_text)
            if canonical is not None:
                results.append(canonical)
        return results


class OpenLegalDataTransformerExtractor:
    """Adapter for the OpenLegalData EuroBERT-210m fine-tune.

    Loaded lazily because the HuggingFace download happens on first
    instantiation. The model is licensed CC BY-NC 4.0; non-commercial
    research use is the relevant scope for this benchmark.
    """

    name = "OLD-Transformer"

    def __init__(self) -> None:
        from transformers import pipeline

        self._pipeline = pipeline(
            task="ner",
            model="openlegaldata/legal-reference-extraction-base-de",
            aggregation_strategy="simple",
        )

    def extract(self, text: str) -> list[str]:
        results: list[str] = []
        for entity in self._pipeline(text):
            if entity.get("entity_group") != "LAW":
                continue
            canonical = _transformer_entity_to_canonical(entity, text)
            if canonical is not None:
                results.append(canonical)
        return results


# ---------------------------------------------------------------------------
# Canonical form mapping helpers
# ---------------------------------------------------------------------------


def _parse_span_to_canonical(span_text: str) -> str | None:
    """Parse a citation span text into the project's canonical form.

    Used by both the OpenLegalData Regex adapter and the EuroBERT
    Transformer adapter. Inputs look like "§ 42 VwGO" or
    "§ 9 Abs. 1 Nr. 1 WHG". The pattern recognises section, optional
    Absatz, optional Satz, optional Nummer, and a Gesetz abbreviation.

    Returns None if the span cannot be parsed into a known structure,
    in which case the citation is dropped from the comparison.
    """
    match = _PARAGRAPH_TOKEN_PATTERN.search(span_text)
    if match is None:
        return None

    parts: list[str] = [f"§ {match.group('section')}"]
    if match.group("paragraph"):
        parts.append(f"Abs. {match.group('paragraph')}")
    if match.group("sentence"):
        parts.append(f"S. {match.group('sentence')}")
    if match.group("number"):
        parts.append(f"Nr. {match.group('number')}")
    parts.append(match.group("law"))
    return " ".join(parts)


_PARAGRAPH_TOKEN_PATTERN = re.compile(
    r"§\s*(?P<section>\d+[a-z]?)"
    r"(?:\s*Abs\.\s*(?P<paragraph>\d+))?"
    r"(?:\s*S\.\s*(?P<sentence>\d+))?"
    r"(?:\s*Nr\.\s*(?P<number>\d+))?"
    r"\s*(?P<law>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß]+)"
)


def _transformer_entity_to_canonical(
    entity: dict[str, Any], text: str
) -> str | None:
    """Render a Transformer NER span into the canonical form.

    The EuroBERT fine-tune outputs character spans tagged "LAW". We
    delegate the span-text parsing to the shared helper so that both
    OpenLegalData adapters use the same canonical-form pipeline.

    Args:
        entity: A token-classification entity dict from the HuggingFace
            pipeline (with start, end, word, score fields).
        text: The full input text, used to recover the exact span if
            the entity's word field is whitespace-normalised.

    Returns:
        Canonical citation string, or None if the span cannot be
        re-parsed into a known structure.
    """
    start = entity.get("start")
    end = entity.get("end")
    if start is None or end is None:
        span_text = entity.get("word", "")
    else:
        span_text = text[start:end]

    return _parse_span_to_canonical(span_text)


# ---------------------------------------------------------------------------
# Ground truth and comparison helpers
# ---------------------------------------------------------------------------


def normalize(citation: str) -> str:
    """Collapse whitespace for tolerant string comparison."""
    return " ".join(citation.split())


def load_gt(doc_id: str) -> dict | None:
    """Load ground truth JSON. Returns None if missing.

    TYP_1 documents have no Phase A GT; they are evaluated for
    false-positive resilience and the GT is implicitly empty.
    """
    path = GT_DIR / f"{doc_id}.json"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def expected_must_retrieve_citations(gt: dict) -> set[str]:
    """Aggregate all must_retrieve citation strings across arguments."""
    citations: set[str] = set()
    for arg in gt.get("expected_arguments", []):
        for entry in arg["expected_norms"]["must_retrieve"]:
            citations.add(normalize(entry["citation"]))
    return citations


def whitelisted(citations: set[str]) -> set[str]:
    """Filter a citation set to entries whose Gesetz is in the whitelist.

    The Gesetz is recovered by taking the last whitespace-separated
    token of the canonical string. Matches are case-sensitive against
    WHITELIST_GESETZE.
    """
    result: set[str] = set()
    for c in citations:
        tokens = c.split()
        if tokens and tokens[-1] in WHITELIST_GESETZE:
            result.add(c)
    return result


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ExtractorResult:
    """Per-extractor output for one document.

    Holds both the raw extraction set and timing information. The
    whitelisted view is derived on demand via whitelisted() so that
    the comparison columns stay consistent across extractors.
    """

    extractor_name: str
    citations: set[str] = field(default_factory=set)
    elapsed_ms: float = 0.0
    error: str | None = None

    @property
    def citations_whitelisted(self) -> set[str]:
        return whitelisted(self.citations)


@dataclass
class DocBenchmark:
    """Per-document benchmark result across all extractors.

    Attributes:
        doc_id: Document identifier (e.g. "einspruch_14").
        doc_type: One of "typ1", "typ2", "mixed".
        expected: GT must_retrieve set (empty for typ1).
        results: Mapping from extractor name to its result.
    """

    doc_id: str
    doc_type: str
    expected: set[str] = field(default_factory=set)
    results: dict[str, ExtractorResult] = field(default_factory=dict)

    def precision_recall_f1(
        self, extracted: set[str]
    ) -> tuple[float | None, float | None, float | None]:
        """Compute precision, recall, and F1 against the expected set.

        Returns:
            Tuple of (precision, recall, f1). Components are None when
            the divisor is zero, matching the convention used in the
            Phase A eval reporting.
        """
        if not extracted and not self.expected:
            return None, None, None
        tp = len(extracted & self.expected)
        precision = tp / len(extracted) if extracted else None
        recall = tp / len(self.expected) if self.expected else None
        if precision is None or recall is None:
            f1 = 0.0 if (precision is None) and recall is not None else None
        elif (precision + recall) == 0:
            f1 = 0.0
        else:
            f1 = 2 * precision * recall / (precision + recall)
        return precision, recall, f1


# ---------------------------------------------------------------------------
# Benchmark execution
# ---------------------------------------------------------------------------


def run_extractor(
    extractor: Extractor, text: str
) -> ExtractorResult:
    """Run one extractor on one text, capturing timing and errors."""
    result = ExtractorResult(extractor_name=extractor.name)
    start = time.perf_counter()
    try:
        citations = extractor.extract(text)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        result.elapsed_ms = (time.perf_counter() - start) * 1000
        return result
    result.elapsed_ms = (time.perf_counter() - start) * 1000
    result.citations = {normalize(c) for c in citations}
    return result


def benchmark_doc(
    doc_id: str,
    doc_type: str,
    text: str,
    extractors: list[Extractor],
) -> DocBenchmark:
    """Run all extractors on one document and assemble the result."""
    bench = DocBenchmark(doc_id=doc_id, doc_type=doc_type)

    if doc_type != "typ1":
        gt = load_gt(doc_id)
        if gt is not None:
            bench.expected = expected_must_retrieve_citations(gt)

    for ext in extractors:
        bench.results[ext.name] = run_extractor(ext, text)

    return bench


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _format_metric(value: float | None) -> str:
    return f"{value:.0%}" if value is not None else "N/A"


def print_phase_a_table(
    label: str, results: list[DocBenchmark], extractor_names: list[str]
) -> None:
    """Per-document precision/recall/F1 table for Phase A subsets.

    Two columns per extractor: Raw and Whitelisted-to-9. Times in ms.
    """
    print(f"\n{'=' * 110}")
    print(f"{label} (n={len(results)})")
    print(f"{'=' * 110}")

    header_cols = ["Doc", "Exp"]
    for name in extractor_names:
        header_cols.extend([f"{name}-Raw P/R/F", f"{name}-WL P/R/F", f"ms"])
    print("\n  " + " | ".join(f"{c:<22}" for c in header_cols))
    print("  " + "-" * 110)

    for bench in results:
        row = [bench.doc_id, str(len(bench.expected))]
        for name in extractor_names:
            r = bench.results[name]
            if r.error:
                row.extend(["ERR", "ERR", f"{r.elapsed_ms:.1f}"])
                continue
            p, rec, f1 = bench.precision_recall_f1(r.citations)
            p_w, rec_w, f1_w = bench.precision_recall_f1(r.citations_whitelisted)
            row.append(
                f"{_format_metric(p)}/{_format_metric(rec)}/{_format_metric(f1)}"
            )
            row.append(
                f"{_format_metric(p_w)}/{_format_metric(rec_w)}/{_format_metric(f1_w)}"
            )
            row.append(f"{r.elapsed_ms:.1f}")
        print("  " + " | ".join(f"{c:<22}" for c in row))

    print(
        "\n  Legend: Exp=expected count, "
        "Raw=all citations, WL=whitelisted to 9 Gesetze, ms=elapsed milliseconds"
    )


def print_phase_a_aggregate(
    label: str, results: list[DocBenchmark], extractor_names: list[str]
) -> None:
    """Macro-average precision/recall/F1 per extractor over the subset."""
    valid = [r for r in results if r.expected]
    if not valid:
        return

    print(f"\n  {label} (Macro-Average over {len(valid)} docs with GT):")
    for name in extractor_names:
        precisions_r, recalls_r, f1s_r = [], [], []
        precisions_w, recalls_w, f1s_w = [], [], []
        times = []
        for bench in valid:
            r = bench.results[name]
            if r.error:
                continue
            p, rec, f1 = bench.precision_recall_f1(r.citations)
            p_w, rec_w, f1_w = bench.precision_recall_f1(r.citations_whitelisted)
            if p is not None:
                precisions_r.append(p)
            if rec is not None:
                recalls_r.append(rec)
            if f1 is not None:
                f1s_r.append(f1)
            if p_w is not None:
                precisions_w.append(p_w)
            if rec_w is not None:
                recalls_w.append(rec_w)
            if f1_w is not None:
                f1s_w.append(f1_w)
            times.append(r.elapsed_ms)

        def _avg(xs: list[float]) -> str:
            return f"{sum(xs) / len(xs):.1%}" if xs else "N/A"

        mean_time = sum(times) / len(times) if times else 0
        median_time = sorted(times)[len(times) // 2] if times else 0
        print(f"    {name}:")
        print(
            f"      Raw         P={_avg(precisions_r)} "
            f"R={_avg(recalls_r)} F1={_avg(f1s_r)}"
        )
        print(
            f"      Whitelisted P={_avg(precisions_w)} "
            f"R={_avg(recalls_w)} F1={_avg(f1s_w)}"
        )
        print(f"      Time        mean={mean_time:.1f}ms median={median_time:.1f}ms")


def print_typ1_fp_resilience(
    results: list[DocBenchmark], extractor_names: list[str]
) -> None:
    """False-positive resilience report for TYP_1 informal letters.

    TYP_1 docs have no expected citations. Any non-empty output is
    a false-positive signal. We report total FP count and per-doc
    breakdown for each extractor in both Raw and Whitelisted modes.
    """
    if not results:
        return

    print(f"\n{'=' * 110}")
    print(
        f"TYP_1 False-Positive Resilience (n={len(results)} informal "
        "citizen letters, expected=0 citations)"
    )
    print(f"{'=' * 110}")

    for name in extractor_names:
        raw_total = 0
        wl_total = 0
        per_doc: list[tuple[str, int, int]] = []
        for bench in results:
            r = bench.results[name]
            if r.error:
                continue
            raw_total += len(r.citations)
            wl_total += len(r.citations_whitelisted)
            per_doc.append((bench.doc_id, len(r.citations), len(r.citations_whitelisted)))

        print(f"\n  {name}:")
        print(f"    Total FP (Raw):         {raw_total}")
        print(f"    Total FP (Whitelisted): {wl_total}")
        non_zero = [(d, raw, wl) for d, raw, wl in per_doc if raw > 0 or wl > 0]
        if non_zero:
            print(f"    Docs with non-empty extraction:")
            for d, raw, wl in non_zero:
                print(f"      {d:<25} Raw={raw} WL={wl}")


def print_edge_cases(
    results: list[DocBenchmark], extractor_names: list[str]
) -> None:
    """Per-doc citations that one extractor finds but others miss.

    Highlights where the three approaches diverge meaningfully:
    citations unique to one extractor, citations missed by exactly one.
    """
    if len(extractor_names) < 2:
        return

    print(f"\n{'=' * 110}")
    print("Edge Cases (citations found by some extractors but not others)")
    print(f"{'=' * 110}")

    for bench in results:
        if not bench.expected:
            continue
        sets = {
            name: bench.results[name].citations_whitelisted
            for name in extractor_names
            if not bench.results[name].error
        }
        if len(sets) < 2:
            continue

        all_extracted = set().union(*sets.values())
        per_extractor_only = {
            name: s - set().union(*[s2 for n2, s2 in sets.items() if n2 != name])
            for name, s in sets.items()
        }

        has_diff = any(unique for unique in per_extractor_only.values())
        if not has_diff:
            continue

        print(f"\n  {bench.doc_id} ({bench.doc_type}):")
        for name, unique in per_extractor_only.items():
            if not unique:
                continue
            print(f"    Only in {name}:")
            for c in sorted(unique):
                in_gt = " (TP)" if c in bench.expected else " (FP)"
                print(f"      ~ {c}{in_gt}")


def print_extractor_limitation_coverage(
    results: list[DocBenchmark], extractor_names: list[str]
) -> None:
    """Whether any extractor catches the documented extractor_limitation cases.

    Phase A GT and earlier work identified citation types outside any
    extractor's whitelist or pattern set: TA Lärm references, DIN
    standards, Anlage X to BauGB, FFH-Richtlinie, Landesverordnungen.
    This section reports raw citation outputs from each extractor on
    the docs known to contain such cases, so we can see whether
    OpenLegalData broadens the coverage.
    """
    relevant_docs = {"einspruch_13_mixed", "einspruch_14", "einspruch_17"}
    relevant = [b for b in results if b.doc_id in relevant_docs]
    if not relevant:
        return

    print(f"\n{'=' * 110}")
    print("Extractor-Limitation Coverage (docs known to contain non-whitelist citations)")
    print(f"{'=' * 110}")

    for bench in relevant:
        print(f"\n  {bench.doc_id}:")
        for name in extractor_names:
            r = bench.results[name]
            if r.error:
                print(f"    {name}: ERR {r.error}")
                continue
            outside_wl = r.citations - r.citations_whitelisted
            if outside_wl:
                print(f"    {name} raw additions beyond 9-Gesetz whitelist:")
                for c in sorted(outside_wl):
                    print(f"      + {c}")
            else:
                print(f"    {name}: no citations outside whitelist")


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------


def save_results(
    phase_a: list[DocBenchmark],
    typ1: list[DocBenchmark],
    extractor_names: list[str],
) -> Path:
    """Persist per-doc benchmark results to a timestamped JSON file."""
    RESULTS_PATH.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    output_path = RESULTS_PATH / f"norm_extractor_benchmark_{timestamp}.json"

    def _serialize_bench(b: DocBenchmark) -> dict[str, Any]:
        return {
            "doc_id": b.doc_id,
            "doc_type": b.doc_type,
            "expected": sorted(b.expected),
            "results": {
                name: {
                    "citations": sorted(b.results[name].citations),
                    "citations_whitelisted": sorted(
                        b.results[name].citations_whitelisted
                    ),
                    "elapsed_ms": round(b.results[name].elapsed_ms, 2),
                    "error": b.results[name].error,
                }
                for name in extractor_names
            },
        }

    output = {
        "timestamp": timestamp,
        "extractors": extractor_names,
        "whitelist_gesetze": sorted(WHITELIST_GESETZE),
        "phase_a": [_serialize_bench(b) for b in phase_a],
        "typ1": [_serialize_bench(b) for b in typ1],
    }

    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)

    return output_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_extractors() -> list[Extractor]:
    """Construct the configured set of extractor adapters.

    Loads OpenLegalData Regex unconditionally and the Transformer only
    when RUN_TRANSFORMER is True. Failures during construction are
    surfaced clearly so the user knows whether a missing dependency
    or download error caused the gap.

    Returns:
        Ordered list of constructed extractors.
    """
    extractors: list[Extractor] = [CustomExtractor()]

    try:
        extractors.append(OpenLegalDataRegexExtractor())
    except ImportError as exc:
        print(
            f"WARNING: OpenLegalData Regex unavailable ({exc}). "
            "Install with: pip install legal-reference-extraction",
            file=sys.stderr,
        )

    if RUN_TRANSFORMER:
        try:
            extractors.append(OpenLegalDataTransformerExtractor())
        except ImportError as exc:
            print(
                f"WARNING: OpenLegalData Transformer unavailable ({exc}). "
                "Install with: pip install transformers torch",
                file=sys.stderr,
            )
        except Exception as exc:
            print(
                f"WARNING: OpenLegalData Transformer init failed: {exc}",
                file=sys.stderr,
            )

    return extractors


def main() -> None:
    """Run the full norm-extractor benchmark and print/save results."""
    load_dotenv()

    extractors = build_extractors()
    if len(extractors) < 2:
        print(
            "Only one extractor available; benchmark needs at least two for "
            "meaningful comparison. Install missing dependencies and rerun.",
            file=sys.stderr,
        )
        sys.exit(1)

    extractor_names = [e.name for e in extractors]

    print(
        "Running norm-extractor benchmark with extractors: "
        f"{', '.join(extractor_names)}"
    )
    print()

    phase_a: list[DocBenchmark] = []

    for path in sorted(DATA_TYP2.glob("einspruch_*.txt")):
        print(f"  TYP_2: {path.stem}...")
        text = path.read_text(encoding="utf-8")
        phase_a.append(benchmark_doc(path.stem, "typ2", text, extractors))

    for path in sorted(DATA_MIXED.glob("einspruch_*_mixed.txt")):
        print(f"  Mixed: {path.stem}...")
        text = path.read_text(encoding="utf-8")
        phase_a.append(benchmark_doc(path.stem, "mixed", text, extractors))

    typ1: list[DocBenchmark] = []
    for path in sorted(DATA_TYP1.glob("einspruch_*.txt")):
        print(f"  TYP_1: {path.stem}...")
        text = path.read_text(encoding="utf-8")
        typ1.append(benchmark_doc(path.stem, "typ1", text, extractors))

    typ2_only = [b for b in phase_a if b.doc_type == "typ2"]
    mixed_only = [b for b in phase_a if b.doc_type == "mixed"]

    print_phase_a_table("TYP_2 (formal legal documents)", typ2_only, extractor_names)
    print_phase_a_aggregate("TYP_2", typ2_only, extractor_names)
    print_phase_a_table("Mixed (formal + personal header)", mixed_only, extractor_names)
    print_phase_a_aggregate("Mixed", mixed_only, extractor_names)
    print_phase_a_aggregate("Overall Phase A (TYP_2 + Mixed)", phase_a, extractor_names)

    print_typ1_fp_resilience(typ1, extractor_names)
    print_edge_cases(phase_a, extractor_names)
    print_extractor_limitation_coverage(phase_a, extractor_names)

    output_path = save_results(phase_a, typ1, extractor_names)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()