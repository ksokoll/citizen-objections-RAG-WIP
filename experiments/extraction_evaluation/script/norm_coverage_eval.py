"""Norm Coverage Evaluation (Phase A) with multi-model support.

Document-level evaluation of whether the deterministic norm_extractor captures
all paragraph citations that appear verbatim in the source text. Compares
against the GT's must_retrieve set (verbatim_in_text entries only).

Two recall metrics per document:

    recall_assigned: Set union of zitierte_normen across all extracted_arguments
        from the production TriageService pipeline. Reflects what reaches
        downstream consumers, which is what production-relevant retrieval
        depends on.

    recall_doc_level: Set of all canonical norms that extract_norms finds
        on the full clean_text, ignoring argument-position assignment.
        Reflects what the deterministic extractor sees in the document
        before the Option Y position-based assignment filters it.

The gap between the two metrics is the "assignment loss": norms that exist
in the text and are found by the extractor, but fall outside the LLM's
chosen original_zitat ranges and are therefore not assigned to any argument.

If recall_doc_level is high but recall_assigned is low, the bottleneck is
the argument-norm-assignment, not the norm extractor itself.

Per-document argument diagnostics:
    argument_count: how many ExtrahiertesArgument the pipeline produced.
        Zero means the LLM returned an empty argumente list, which usually
        means the document was classified as having no legal substance.
    argument_verified_count: subset where original_zitat is a verifiable
        substring of clean_text (ADR-006 Layer 1).
    zitat_lengths: list of original_zitat character lengths. Short zitate
        often fail to include the §-citation that would be needed for
        Option Y position-based norm assignment.
    zitate_with_paragraph_count: how many zitate contain a "§" character.
        Together with argument_count, this disambiguates the two failure
        modes when assigned_count is zero: "no arguments extracted" vs
        "arguments extracted but zitate too narrow to include any norm".

Multi-model support: change the MODEL_NAME constant to switch between
OpenAI (gpt-*) and Anthropic (claude-*) models. Result files are named
with the model identifier for easy comparison across runs. OpenAI uses
native beta structured outputs; Anthropic uses tool-use to enforce the
schema (no native Pydantic parse equivalent yet).

Scope: TYP_2 (einspruch_14 to einspruch_20) and Mixed (einspruch_11_mixed
to einspruch_13_mixed).

Output: stdout summary plus JSON results file under
experiments/extraction_evaluation/results/.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

# Make src/ importable when invoked from the project root, regardless of
# whether the app package has been installed via `pip install -e .`. The
# proper long-term fix is the editable install, which removes the need
# for this hook entirely. Until then, this hook lets the script run
# standalone from anywhere.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from app.triage.norm_extractor import extract_norms  # noqa: E402
from app.triage.service import TriageService  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The model to run the evaluation against. Change this between runs to
# compare models. Family is detected by prefix:
#   gpt-*    -> OpenAI Chat Completions (native parse() via beta API)
#   claude-* -> Anthropic Messages (tool use with enforced schema)
#
# Tested model strings:
#   "gpt-4o-mini"
#   "gpt-4o"
#   "claude-sonnet-4-6"
#   "o3-mini"
#   "gpt-5.5"
MODEL_NAME = "gpt-4o-mini"

BASE = Path("experiments/extraction_evaluation")
DATA_TYP2 = BASE / "data" / "typ2"
DATA_MIXED = BASE / "data" / "mixed"
GT_DIR = BASE / "ground_truth" / "retrieval_gt"
RESULTS_PATH = BASE / "results"

RATE_LIMIT_SLEEP_S = 0.5
ANTHROPIC_MAX_TOKENS = 8192


# ---------------------------------------------------------------------------
# Model family detection
# ---------------------------------------------------------------------------

# OpenAI exposes several families with different name prefixes. The flagship
# gpt-4* and gpt-5* lines and the o-series reasoning models (o1, o3, o4) all
# route through the OpenAI client.
_OPENAI_PREFIXES: tuple[str, ...] = ("gpt-", "o1", "o3", "o4")
_ANTHROPIC_PREFIXES: tuple[str, ...] = ("claude-",)

# Models that do not accept a custom temperature value and require the
# default (typically 1). The o-series reasoning models and the GPT-5 family
# fall into this bucket as of May 2026. Passing temperature=0 to these
# models returns HTTP 400 with "Unsupported value: 'temperature' does not
# support 0 with this model".
_NO_CUSTOM_TEMPERATURE_PREFIXES: tuple[str, ...] = (
    "o1",
    "o3",
    "o4",
    "gpt-5",
)


def _is_openai_model(model: str) -> bool:
    """Return True if the model name belongs to the OpenAI family."""
    return any(model.startswith(p) for p in _OPENAI_PREFIXES)


def _is_anthropic_model(model: str) -> bool:
    """Return True if the model name belongs to the Anthropic family."""
    return any(model.startswith(p) for p in _ANTHROPIC_PREFIXES)


def _supports_custom_temperature(model: str) -> bool:
    """Return True if the model accepts a non-default temperature value.

    OpenAI's reasoning models and the GPT-5 family hard-error on any
    temperature setting other than the model default. For these models the
    eval client must omit the temperature parameter entirely.
    """
    return not any(model.startswith(p) for p in _NO_CUSTOM_TEMPERATURE_PREFIXES)


# ---------------------------------------------------------------------------
# LLM clients
# ---------------------------------------------------------------------------


class EvalLLMClient(Protocol):
    """Common interface satisfied by both provider-specific eval clients."""

    def parse(
        self, prompt: str, response_format: type[BaseModel]
    ) -> BaseModel: ...


class OpenAIEvalClient:
    """Wraps openai.OpenAI for the production TriageService contract.

    Uses the beta structured outputs endpoint, which returns parsed Pydantic
    instances directly. Bypasses app.services.llm.OpenAIClient to keep the
    eval decoupled from the production wrapper.
    """

    def __init__(self, client: OpenAI, model: str) -> None:
        self._client = client
        self._model = model

    def parse(
        self, prompt: str, response_format: type[BaseModel]
    ) -> BaseModel:
        # Build kwargs incrementally so the temperature parameter can be
        # omitted entirely for reasoning models and the GPT-5 family, which
        # reject any non-default temperature with HTTP 400.
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": response_format,
        }
        if _supports_custom_temperature(self._model):
            kwargs["temperature"] = 0
        response = self._client.beta.chat.completions.parse(**kwargs)
        return response.choices[0].message.parsed


class AnthropicEvalClient:
    """Wraps anthropic.Anthropic with tool-use for structured outputs.

    Anthropic has no native Pydantic parse equivalent. Structured output is
    enforced via tool use: the Pydantic JSON schema becomes the tool's
    input_schema, tool_choice forces the model to call exactly this tool,
    and the tool_use block's input dict is validated back into the Pydantic
    model. This pattern is the documented Anthropic recipe for guaranteed
    structured output.
    """

    _TOOL_NAME = "submit_structured_output"

    def __init__(self, client: Any, model: str) -> None:
        """Initialize with an existing Anthropic client and target model.

        Args:
            client: Instantiated anthropic.Anthropic client. Typed as Any
                to avoid the unconditional anthropic import at module load.
            model: Model identifier (e.g. 'claude-sonnet-4-6').
        """
        self._client = client
        self._model = model

    def parse(
        self, prompt: str, response_format: type[BaseModel]
    ) -> BaseModel:
        schema = response_format.model_json_schema()
        response = self._client.messages.create(
            model=self._model,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
            tools=[
                {
                    "name": self._TOOL_NAME,
                    "description": (
                        "Submit the structured analysis output matching "
                        "the required JSON schema."
                    ),
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": self._TOOL_NAME},
        )
        for block in response.content:
            if (
                getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == self._TOOL_NAME
            ):
                return response_format.model_validate(block.input)
        raise RuntimeError(
            f"Anthropic response did not contain expected tool_use block "
            f"for {self._TOOL_NAME}. Content: {response.content}"
        )


def build_llm_client(model_name: str) -> EvalLLMClient:
    """Construct the right EvalLLMClient for the configured model.

    Args:
        model_name: Model identifier; family is detected by prefix.

    Returns:
        An EvalLLMClient (OpenAI or Anthropic) ready to call parse().

    Raises:
        RuntimeError: If the required API key for the model family is missing.
        ValueError: If the model name does not match a known family.
    """
    if _is_openai_model(model_name):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set in environment.")
        return OpenAIEvalClient(client=OpenAI(api_key=api_key), model=model_name)

    if _is_anthropic_model(model_name):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set in environment.")
        # Conditional import: only loaded when running a Claude model so the
        # script does not hard-require the anthropic package for OpenAI runs.
        from anthropic import Anthropic

        return AnthropicEvalClient(client=Anthropic(api_key=api_key), model=model_name)

    raise ValueError(
        f"Unknown model family for {model_name!r}. "
        f"Expected prefix in {_OPENAI_PREFIXES + _ANTHROPIC_PREFIXES}."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def normalize(citation: str) -> str:
    """Collapse whitespace for tolerant string comparison."""
    return " ".join(citation.split())


def load_gt(doc_id: str) -> dict | None:
    """Load ground truth JSON for a document. Returns None if missing."""
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


def sanitize_model_name(model: str) -> str:
    """Make a model identifier safe for use in a filename."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", model)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class DocResult:
    """Per-document evaluation result with both assignment- and doc-level metrics."""

    doc_id: str
    doc_type: str
    error: bool = False
    error_msg: str | None = None
    expected: set[str] = field(default_factory=set)

    # What zitierte_normen the production pipeline assigned to arguments.
    extracted_assigned: set[str] = field(default_factory=set)

    # What extract_norms finds on clean_text without argument-position filter.
    extracted_doc_level: set[str] = field(default_factory=set)

    # Argument-level diagnostics for disambiguating failure modes.
    argument_count: int = 0
    argument_verified_count: int = 0
    zitat_lengths: list[int] = field(default_factory=list)
    zitate_with_paragraph_count: int = 0

    notes: str = ""

    # ----- Assignment-based metrics (production behavior) -----

    @property
    def tp_assigned(self) -> set[str]:
        return self.expected & self.extracted_assigned

    @property
    def fp_assigned(self) -> set[str]:
        return self.extracted_assigned - self.expected

    @property
    def fn_assigned(self) -> set[str]:
        return self.expected - self.extracted_assigned

    @property
    def precision_assigned(self) -> float | None:
        if not self.extracted_assigned:
            return None
        return len(self.tp_assigned) / len(self.extracted_assigned)

    @property
    def recall_assigned(self) -> float | None:
        if not self.expected:
            return None
        return len(self.tp_assigned) / len(self.expected)

    @property
    def f1_assigned(self) -> float | None:
        """Harmonic mean of precision and recall.

        Semantics for the edge cases:
            - recall is None (no expectations): F1 is vacuously undefined,
              return None. Excluded from macro-average.
            - precision is None (no extractions) but recall is defined: the
              model failed to extract anything when something was expected.
              F1 = 0 (not None). Counts in the macro-average as a true zero.
            - both defined but sum to 0: F1 = 0.
        """
        p, r = self.precision_assigned, self.recall_assigned
        if r is None:
            return None
        if p is None:
            return 0.0
        if (p + r) == 0:
            return 0.0
        return 2 * p * r / (p + r)

    # ----- Doc-level metrics (norm_extractor only) -----

    @property
    def tp_doc(self) -> set[str]:
        return self.expected & self.extracted_doc_level

    @property
    def fp_doc(self) -> set[str]:
        return self.extracted_doc_level - self.expected

    @property
    def fn_doc(self) -> set[str]:
        return self.expected - self.extracted_doc_level

    @property
    def precision_doc(self) -> float | None:
        if not self.extracted_doc_level:
            return None
        return len(self.tp_doc) / len(self.extracted_doc_level)

    @property
    def recall_doc(self) -> float | None:
        if not self.expected:
            return None
        return len(self.tp_doc) / len(self.expected)

    # ----- Bottleneck diagnostics -----

    @property
    def assignment_loss(self) -> set[str]:
        """Norms found doc-level but not assigned to any argument."""
        return self.extracted_doc_level - self.extracted_assigned

    @property
    def fn_lost_by_assignment(self) -> set[str]:
        """False negatives that the extractor DID find but assignment lost."""
        return self.fn_assigned & self.extracted_doc_level

    @property
    def fn_truly_missing(self) -> set[str]:
        """False negatives that the extractor itself failed to find."""
        return self.fn_assigned - self.extracted_doc_level


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate_doc(
    doc_id: str,
    doc_type: str,
    text: str,
    service: TriageService,
) -> DocResult:
    """Run the Triage pipeline plus a direct extract_norms pass on the text."""
    result = DocResult(doc_id=doc_id, doc_type=doc_type)

    gt = load_gt(doc_id)
    if gt is None:
        result.error = True
        result.error_msg = f"GT file not found: {GT_DIR / (doc_id + '.json')}"
        return result

    result.expected = expected_must_retrieve_citations(gt)
    result.notes = gt.get("notes", "")

    # Doc-level extraction: norm_extractor over full clean_text.
    try:
        doc_level_norms = extract_norms(text)
    except Exception as exc:
        result.error = True
        result.error_msg = f"extract_norms failed: {exc}"
        return result
    result.extracted_doc_level = {normalize(n.canonical()) for n in doc_level_norms}

    # Assigned: production TriageService pipeline.
    try:
        triage_result = service.triage(text)
    except Exception as exc:
        result.error = True
        result.error_msg = f"TriageService.triage failed: {exc}"
        return result

    arguments = triage_result.extracted_arguments
    assigned: set[str] = set()
    for arg in arguments:
        for norm in arg.zitierte_normen:
            assigned.add(normalize(norm))
    result.extracted_assigned = assigned

    # Argument-level diagnostics for failure-mode disambiguation.
    result.argument_count = len(arguments)
    result.argument_verified_count = sum(1 for arg in arguments if arg.argument_verified)
    result.zitat_lengths = [len(arg.original_zitat) for arg in arguments]
    result.zitate_with_paragraph_count = sum(
        1 for arg in arguments if "§" in arg.original_zitat
    )

    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_doc_table(label: str, results: list[DocResult]) -> None:
    """Print per-document table with both assignment and doc-level recall."""
    print(f"\n{'=' * 100}")
    print(f"{label} (n={len(results)})")
    print(f"{'=' * 100}")

    print(
        f"\n  {'Doc':<25} {'Exp':>4} {'Args':>4} {'Asg':>4} {'Doc':>4} "
        f"{'TP(A)':>5} {'FN(A)':>5} {'Rec(A)':>7} {'Rec(D)':>7} {'Loss':>5}"
    )
    print(f"  {'-' * 90}")

    for r in results:
        if r.error:
            print(f"  {r.doc_id:<25} ERR: {r.error_msg}")
            continue
        rec_a = f"{r.recall_assigned:.0%}" if r.recall_assigned is not None else "N/A"
        rec_d = f"{r.recall_doc:.0%}" if r.recall_doc is not None else "N/A"
        print(
            f"  {r.doc_id:<25} "
            f"{len(r.expected):>4} "
            f"{r.argument_count:>4} "
            f"{len(r.extracted_assigned):>4} "
            f"{len(r.extracted_doc_level):>4} "
            f"{len(r.tp_assigned):>5} "
            f"{len(r.fn_assigned):>5} "
            f"{rec_a:>7} "
            f"{rec_d:>7} "
            f"{len(r.assignment_loss):>5}"
        )

    print(
        "\n  Legend: Exp=expected, Args=#arguments-extracted, Asg=#assigned-norms, "
        "Doc=#doc-level-extracted,"
    )
    print(
        "          Rec(A)=assigned recall, Rec(D)=doc-level recall, "
        "Loss=|doc-level - assigned|"
    )


def print_aggregate(label: str, results: list[DocResult]) -> None:
    """Print macro-average metrics for both assignment and doc-level."""
    valid = [r for r in results if not r.error]
    if not valid:
        return

    p_assigned = [r.precision_assigned for r in valid if r.precision_assigned is not None]
    r_assigned = [r.recall_assigned for r in valid if r.recall_assigned is not None]
    f1_assigned = [r.f1_assigned for r in valid if r.f1_assigned is not None]

    p_doc = [r.precision_doc for r in valid if r.precision_doc is not None]
    r_doc = [r.recall_doc for r in valid if r.recall_doc is not None]

    print(f"\n  {label} (Macro-Average):")
    print(f"    Assignment-based (production pipeline):")
    if p_assigned:
        print(
            f"      Precision: {sum(p_assigned)/len(p_assigned):.1%} "
            f"(over {len(p_assigned)} docs with extractions)"
        )
    if r_assigned:
        print(f"      Recall:    {sum(r_assigned)/len(r_assigned):.1%}")
    if f1_assigned:
        print(
            f"      F1:        {sum(f1_assigned)/len(f1_assigned):.1%} "
            f"(zero-extraction docs counted as F1=0)"
        )
    print(f"    Doc-level (extractor only, no assignment):")
    if p_doc:
        print(f"      Precision: {sum(p_doc)/len(p_doc):.1%}")
    if r_doc:
        print(f"      Recall:    {sum(r_doc)/len(r_doc):.1%}")
    if r_assigned and r_doc:
        gap = (sum(r_doc)/len(r_doc)) - (sum(r_assigned)/len(r_assigned))
        print(f"    Assignment gap (Doc - Assigned recall): {gap:+.1%}")


def print_bottleneck_analysis(results: list[DocResult]) -> None:
    """Per-doc breakdown distinguishing assignment-loss from extractor-loss."""
    valid_with_misses = [
        r for r in results if not r.error and r.fn_assigned
    ]
    if not valid_with_misses:
        return

    print(f"\n{'=' * 100}")
    print("Bottleneck Analysis (per document with missing citations)")
    print(f"{'=' * 100}")
    print("\n  For each missed citation, categorize the cause:")
    print("  LOST_BY_ASSIGNMENT: extractor found it, but not assigned to any argument.")
    print("  TRULY_MISSING:     extractor did not find it in clean_text at all.")

    for r in valid_with_misses:
        print(f"\n  {r.doc_id} ({r.doc_type}):")
        if r.fn_lost_by_assignment:
            print(f"    LOST_BY_ASSIGNMENT ({len(r.fn_lost_by_assignment)}):")
            for citation in sorted(r.fn_lost_by_assignment):
                print(f"      ~ {citation}")
        if r.fn_truly_missing:
            print(f"    TRULY_MISSING ({len(r.fn_truly_missing)}):")
            for citation in sorted(r.fn_truly_missing):
                print(f"      - {citation}")
        if r.fp_assigned:
            print(f"    SPURIOUS in assignment ({len(r.fp_assigned)}):")
            for citation in sorted(r.fp_assigned):
                print(f"      + {citation}")


def print_zero_extraction_diagnostics(results: list[DocResult]) -> None:
    """Show argument-level diagnostics for docs where assigned norms is zero.

    Disambiguates the two failure modes:
        H1 (no arguments): argument_count = 0. LLM returned an empty list,
            likely classifying the document as having no legal substance.
        H2 (narrow zitate): argument_count > 0 but zitate_with_paragraph_count
            is low or zero. LLM extracted arguments but chose original_zitat
            ranges that do not include any §-citation, so the Option Y
            position-based assignment cannot attach norms.
    """
    valid = [r for r in results if not r.error]
    zero_assignment = [
        r for r in valid if not r.extracted_assigned and r.expected
    ]
    if not zero_assignment:
        return

    print(f"\n{'=' * 100}")
    print("Zero-Extraction Diagnostics (docs with no assigned norms despite expectations)")
    print(f"{'=' * 100}")

    print(
        f"\n  {'Doc':<25} {'Args':>5} {'Verif':>5} {'ZitW§':>6} "
        f"{'MeanZL':>7} {'MaxZL':>6} {'Hypothesis':<20}"
    )
    print(f"  {'-' * 90}")

    for r in zero_assignment:
        mean_zl = (
            sum(r.zitat_lengths) / len(r.zitat_lengths) if r.zitat_lengths else 0
        )
        max_zl = max(r.zitat_lengths) if r.zitat_lengths else 0
        if r.argument_count == 0:
            hypothesis = "H1: no arguments"
        elif r.zitate_with_paragraph_count == 0:
            hypothesis = "H2: zitate w/o §"
        else:
            hypothesis = "H3: mixed/partial"
        print(
            f"  {r.doc_id:<25} "
            f"{r.argument_count:>5} "
            f"{r.argument_verified_count:>5} "
            f"{r.zitate_with_paragraph_count:>6} "
            f"{mean_zl:>7.0f} "
            f"{max_zl:>6} "
            f"{hypothesis:<20}"
        )

    print(
        "\n  Legend: Args=#arguments extracted by LLM, Verif=#argument_verified=True,"
    )
    print(
        "          ZitW§=#zitate containing §, MeanZL=mean zitat char-length, "
        "MaxZL=max zitat length"
    )


def print_citation_frequency(results: list[DocResult]) -> None:
    """Citations ranked by miss frequency, split by miss type."""
    valid = [r for r in results if not r.error]
    if not valid:
        return

    lost_counter: Counter[str] = Counter()
    truly_missing_counter: Counter[str] = Counter()
    for r in valid:
        for citation in r.fn_lost_by_assignment:
            lost_counter[citation] += 1
        for citation in r.fn_truly_missing:
            truly_missing_counter[citation] += 1

    if truly_missing_counter:
        print(f"\n{'=' * 100}")
        print("Most frequent TRULY_MISSING citations (extractor failed to find)")
        print(f"{'=' * 100}")
        for citation, count in truly_missing_counter.most_common(15):
            print(f"  {count:>2}x  {citation}")

    if lost_counter:
        print(f"\n{'=' * 100}")
        print("Most frequent LOST_BY_ASSIGNMENT citations (extractor OK, assignment fails)")
        print(f"{'=' * 100}")
        for citation, count in lost_counter.most_common(15):
            print(f"  {count:>2}x  {citation}")


def save_results(results: list[DocResult], model_name: str) -> Path:
    """Persist per-doc results to a timestamped JSON file with model in name."""
    RESULTS_PATH.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    safe_model = sanitize_model_name(model_name)
    output_path = (
        RESULTS_PATH / f"norm_coverage_eval_{safe_model}_{timestamp}.json"
    )

    output = {
        "model": model_name,
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
    """Run the full norm-coverage evaluation and print/save results."""
    load_dotenv()

    try:
        llm = build_llm_client(MODEL_NAME)
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    service = TriageService(llm=llm)

    results: list[DocResult] = []

    print(f"Running norm-coverage evaluation (model={MODEL_NAME})...")
    print()

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

    output_path = save_results(results, MODEL_NAME)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()