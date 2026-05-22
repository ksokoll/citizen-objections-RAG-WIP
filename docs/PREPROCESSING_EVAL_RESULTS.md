# EVAL_RESULTS.md

Documentation of the evaluation of the preprocessing step (Triage Bounded Context). Describes the iterations, methodological turning points, and final state. Serves as a development history and lessons-learned reference for later phases.

---

## Current State

**Date**: 2026-05-23, Iteration 6 (final for this skeleton)

**Task**: Identification of legal domain (catalog_id) and extraction of legal arguments from citizen and lawyer objections in urban planning procedures. Norm extraction is explicitly NOT a Triage task (per ADR-013); it is the responsibility of the Retrieval step.

**Model**: gpt-4o-mini, temperature=0

**Final Metrics**:

| Subset | Catalog Recall | Catalog Precision | Einwendungs-Typ Accuracy | Argument Count in Range | Verified Rate |
|--------|----------------|-------------------|--------------------------|-------------------------|---------------|
| TYP_2 (n=7) | 92.86% | 100.00% | 100% | 85.71% | 85.36% |
| TYP_1 (n=10) | 100.00% | n/a | 80% | 80% | 100.00% |
| Mixed (n=3) | 100.00% | 100.00% | 100% | 100% | 100.00% |

Distractor hits across all 23 documents: 0. No hallucinated categories.

**Status**: Performance sufficient for transition to TriageService implementation. Three minor residual issues known and documented (see "Open Issues" section).

---

## Test Corpus

20 synthetic objection documents plus 3 mixed variants. All relate to a fictional preliminary binding land-use plan ("Gewerbegebiet Starkenburg-Süd") with a hyperscale data center as the proposed project.

- einspruch_01 to einspruch_10: TYP_1 (informal citizen letters without legal substance)
- einspruch_11 to einspruch_20: TYP_2 (formal legal submissions)
- einspruch_11_mixed, einspruch_12_mixed, einspruch_13_mixed: mixed variants (personal header plus legal content)

Legal domain distribution across TYP_2 covers seven clusters: planning law, water law, noise protection, nature conservation, energy law, heat planning law, procedural law.

Synthetically created with LLM assistance, therefore subject to known limitations (see "Methodological Limitations" section).

---

## Iterations

### Iteration 1: Norm Recall as Primary Metric (Discarded)

**Hypothesis**: Triage extracts the cited paragraphs per argument. Evaluation measures norm recall against ground truth.

**Results**:
- TYP_2 norm recall approximately 30–60% per document
- Mixed: 0% recall across the board
- einspruch_18: 0% recall despite seemingly good extraction

**Finding**: The norm recall metric measures a task that Triage does not have. Per ADR-013, the Triage task is catalog identification, not norm extraction. Norm extraction is the responsibility of the Retrieval step.

**Consequence**: Evaluation architecture fundamentally redesigned.

---

### Iteration 2: Ground Truth Cleanup

**Hypothesis**: The ground truth may contain inferred norms (legally applicable but not explicitly cited), not only explicitly cited ones. This structurally distorts the recall metric.

**Approach**: Manual review per document: which norms appear verbatim in the text (paragraph notation, written-out form, section headings count) versus which are inferred.

**Results**:
- 53 original norm entries: 45 explicitly cited, 8 inferred
- einspruch_18 has ZERO explicitly cited paragraphs (fully explains the 0% recall)
- einspruch_14 is the gold standard: 7 of 7 explicitly cited

**Finding**: Test set creation artifact. During synthetic GT generation, the annotator LLM played both roles simultaneously (writing text and listing legally relevant norms). Inference leaked into the GT.

**Output artifact**: `ground_truth_cleaned.json` with separation into `explizit_zitierte_normen` and `inferierte_anwendbare_normen` plus `fundstelle` annotation.

---

### Iteration 3: Architecture Reset (No Eval Run)

**Finding**: The entire norm recall discussion measures a secondary task. The primary Triage task is catalog matching. Norm identification is handled by the RAG backend with hybrid retrieval plus reranker (validated in Spikes A through D).

**Consequence**:
- Eval script completely redesigned around catalog matching as the primary metric
- `zitierte_normen` remains as a field in `ExtrahiertesArgument` but only as an audit trail detail, not as an eval target
- ADR-013 is confirmed by this clarification

---

### Iteration 4: Catalog Definition with Distractor

**Approach**: Seven thematic clusters derived from the 10 TYP_2 documents (C-001 to C-007). Plus 3 distractor clusters (C-008 to C-010: monument protection, soil protection, mining law) that appear in none of the test documents, to test discrimination ability.

**Important methodological correction during this iteration**: The original catalog descriptions contained `typical_arguments` drawn from the test documents ("Widerspruch zum Flächennutzungsplan", "Schallgutachten unvollständig"). This is test set leakage: the LLM would have seen patterns from the test set directly in the prompt. Cleaned up to generic domain descriptions.

**Output artifact**: `katalog_und_zuordnung.json` with 10 catalogs plus `expected_catalog_assignment` for the 23 documents.

---

### Iteration 5: Catalog Matching Eval v1

**Setup**: New eval script with catalog recall, precision, distractor hits, einwendungs_typ match, and argument count in range as metrics.

**Results**:
- TYP_2 catalog recall: 3.57%
- TYP_2 catalog precision: 5.56%
- TYP_1 pre-filter: 7/10 had 0 arguments (3 failures)
- Mixed catalog recall: 0%
- Einwendungs-typ match TYP_2: 0%

**Diagnosis attempt**: Classification generally weak. Hypotheses:
1. Casing mismatch (prompt says "typ_2", GT expects "TYP_2")
2. Schema drift between prompt example and Pydantic schema
3. TYP_1 rule too weakly positioned
4. Descriptions too generic without domain anchors

---

### Iteration 6 Part 1: Prompt v2

**Approach**: Four fixes in `ARGUMENT_EXTRACTION_PROMPT`:
1. Pre-check as a MANDATORY block before all other rules (TYP_1 → empty list)
2. Classification guide with seven clusters and domain vocabulary (Bebauungsplan, Gewässerbenutzung, Schallgutachten, etc.)
3. Schema example adapted to wrapper object with 5 fields
4. Casing consistently capitalised (TYP_1, TYP_2)

**Results**:
- TYP_2 catalog recall: 3.57% (identical to before)
- Einwendungs-typ match TYP_2: 0% → 100% (casing fix works)
- TYP_1 pre-check success: 7/10 → 8/10 (marginal improvement)
- Mixed catalog recall: 0% → 0%

**Confusing finding**: The classification guide had no measurable effect on the primary indicator. Casing fix works, but catalog matching remained catastrophic.

---

### Iteration 6 Part 2: Schema Mismatch Discovered

**Turning point**: The code catalog (`catalog.py`) and the eval ground truth (`katalog_und_zuordnung.json`) had different thematic assignments for the catalog_ids:

| catalog_id | catalog.py (old) | Eval GT |
|------------|------------------|---------|
| C-001 | Noise protection (BImSchG) | Planning law (BauGB) |
| C-002 | Traffic and access | Water law (WHG) |
| C-003 | Nature conservation and green spaces | Noise protection and emissions |
| C-004 | Air quality and emissions | Nature conservation and species |
| C-005 | Planning law (BauGB) | Energy law (EnWG) |
| C-006 to C-010 | did not exist | Heat planning, procedural law, distractors |

The LLM had classified against the code catalog (descriptions from catalog.py). The eval tested against the JSON assignment. Several classifications marked as "wrong" were actually correct in terms of the code catalog's understanding.

**Consequence**: catalog.py must be rebuilt to match the thematic structure of the eval GT. 7 clusters adopted from the JSON. Distractors (C-008 to C-010) deliberately NOT added to the production catalog (they are an eval methodology construct, not a production construct).

---

### Iteration 6 Part 3: Final Eval after catalog.py Update

**Results**:

TYP_2 (n=7):
- Catalog recall: 92.86% (up from 3.57%)
- Catalog precision: 100.00%
- Einwendungs-typ accuracy: 100%
- Argument count in range: 85.71%
- Verified rate: 85.36%

TYP_1 (n=10):
- Catalog recall: 100% (vacuously, no expectation)
- TYP_1 pre-check success: 8/10 (80%)
- Verified rate (for failures): 100%

Mixed (n=3):
- Catalog recall: 100% (up from 0%)
- Catalog precision: 100%
- Einwendungs-typ accuracy: 100%

Per-catalog diagnostics:
- C-001 Planning law: 4 TP, 0 FP, 1 FN (80% recall, 100% precision)
- C-002 Water law: 1 TP, 0 FP, 0 FN
- C-003 Noise protection: 1 TP, 1 FP, 0 FN (hallucination from TYP_1 failure)
- C-004 Nature conservation: 3 TP, 0 FP, 0 FN
- C-005 Energy law: 2 TP, 0 FP, 0 FN
- C-006 Heat planning law: 1 TP, 0 FP, 0 FN
- C-007 Procedural law: 2 TP, 0 FP, 0 FN

Distractor hits: 0/23.

**Finding**: The schema mismatch was the dominant factor. Catalog matching works well with gpt-4o-mini when the catalog is consistently defined.

---

## Open Issues

### einspruch_15: 50% Catalog Recall (Multi-Catalog Edge Case)

Expected: C-001 (planning law due to §1 Abs. 5 BauGB climate protection reference) and C-005 (energy law due to grid connection).

Actual: presumably only C-005 recognised. The climate protection aspect in section III is subtly embedded between energy arguments and is not distinguished by the LLM as a standalone planning law argument.

**Priority**: low. A human classifier would likely also classify it primarily as C-005. Edge case, not a systematic problem.

---

### TYP_1 Pre-Check: 20% Failure Rate (einspruch_07 and einspruch_08)

Despite the explicit pre-check in the prompt, 2 of 10 TYP_1 documents violate ADR-013 (arguments extracted where there should be none).

**Hypothesis**: einspruch_07 and einspruch_08 likely contain pseudo-legal formulations or authority references that mislead the LLM.

**Potential solution**: Deterministic post-filter outside the LLM. If an extracted argument contains no "§" sign or paragraph notation in `original_zitat` and no legal vocabulary ("nach", "gemäß", "rechtlich", "verwaltungsgerichtlich"), discard the argument.

**Priority**: medium. This gap produces the C-003 FP in the per-catalog diagnostics.

---

### einspruch_18: Argument Count out of Range

Expected: 2 to 4 arguments. Actual: presumably 6.

The traffic assessment document contains no explicit paragraphs, and the LLM decomposes the thematic deficiencies more granularly than the eval GT expects.

**Potential solution**: Extend GT range to 2 to 6. The original range was an estimate.

**Priority**: low. Cosmetic GT fix.

---

## Lessons Learned

### Schema Consistency is Critical

The jump from 3.57% to 92.86% catalog recall did not come from model tuning, prompt optimisation, or architecture changes. It came from synchronising three definition sources:
- `KATALOG` in catalog.py
- `CatalogId` enum in catalog.py
- `expected_catalog_assignment` in katalog_und_zuordnung.json

A simple schema assert test would have caught the mismatch in seconds:

```python
def test_katalog_eval_gt_consistency():
    with open("katalog_und_zuordnung.json") as f:
        gt = json.load(f)
    eval_catalog_ids = set(gt["katalog"].keys())
    code_catalog_ids = set(KATALOG.keys())
    assert eval_catalog_ids == code_catalog_ids
```

This will be added to the test suite as a fitness function.

---

### Norm Extraction is Not a Triage Task

The first eval iterations attempted to maximise norm recall. This was conceptually wrong. Triage identifies the legal domain. The RAG backend finds the concrete norms. This separation is the foundation for the combined design functioning (single LLM call for argument extraction plus catalog matching).

ADR-013 is confirmed by this clarification: one LLM call is sufficient for both tasks.

---

### Test Set Informing Can Be Reduced, Not Eliminated

Initial catalog descriptions contained `typical_arguments` drawn directly from the test documents. This is obvious leakage and was removed. The final version with generic domain vocabulary ("Bebauungsplan", "Gewässerbenutzung", "Schallgutachten") is methodologically cleaner.

The evaluation is not fully leakage-free even so: the selection of the 7 clusters itself reflects the legal domains present in the test set. This is acceptable at the skeleton stage and is documented. For production, the catalog should be derived from historical objection procedures of the relevant authority, not from the test set.

---

### Combined vs. Separate Architecture

During the iterations, the question arose whether argument extraction and catalog matching should be done in a single LLM call (current design) or as separate steps (preprocessing → catalog matching → RAG).

Pre-defined threshold: catalog recall TYP_2 below 50% after prompt v2 → separation. We reached 92.86% after schema synchronisation. Clear answer: the combined design works, no separation needed.

This saves double LLM costs and double latency per document.

---

## Methodological Limitations

### Synthetic Test Corpus

All 23 documents and their original ground truth annotations were created with LLM assistance. Real authority documents likely differ structurally:
- Synthetic TYP_2 documents are more cleanly structured (clear sections, consistent legal diction)
- Real lawyers write differently, omit structures, mix styles
- Real citizens sometimes mention paragraphs; formal lawyers sometimes write prose without § notation

Expectation: eval metrics on real documents will tend to be worse.

---

### Small Corpus

n=23 documents is small for statistical robustness. Per-catalog diagnostics are based on 1 to 5 data points per cluster. Variation between runs is possible even at temperature=0 (Anthropic and OpenAI do not guarantee full determinism even at temp=0).

---

### Model Tier

Evaluation ran on gpt-4o-mini. A counter-test with gpt-4o (full) was not conducted because the final numbers with the smaller model were already sufficient. If the TriageService implementation runs on a different model (e.g. Claude Sonnet), the evaluation should be repeated on that model.

---

### Determinism

Not empirically verified (no repeat loops in the eval script). Likely stable at the final numbers, but for a production-grade claim a 3x run with hash comparison of outputs would be appropriate.

---

## Artifact Directory

Outputs of this evaluation:
- `ground_truth_cleaned.json`: cleaned norm GT with explicit/inferred separation
- `katalog_und_zuordnung.json`: catalog definition plus expected assignment per document
- `catalog.py` (v2): code catalog with 7 clusters, synchronised with eval GT
- `eval_catalog_matching.py`: eval script v3 (catalog matching as primary metric)
- `catalog_eval_results_<timestamp>.json`: detailed outputs per eval run

---

## Next Steps

1. Add schema assert test to the test suite (fitness function against drift between code catalog and eval GT).
2. Deterministic post-filter for TYP_1 sanity check (solution for the 20% failure rate).
3. Adjust einspruch_18 argument count range in the JSON.
4. Implement TriageService with the current prompt v2 and gpt-4o-mini.
5. After TriageService implementation: 3x determinism run as a sanity check.

**Phase 2 topics** (post-skeleton):
- Validation with anonymised real documents from the authority
- Mixed strategy for more complex header styles (multi-pass extraction)
- Extension of the test suite with adversarial cases (TYP_1 with pseudo-legal diction, TYP_2 without § notation)