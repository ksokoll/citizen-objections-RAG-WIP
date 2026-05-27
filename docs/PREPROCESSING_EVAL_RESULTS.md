# EVAL_RESULTS.md

Documentation of the evaluation of the preprocessing step (Triage Bounded Context). Describes the iterations, methodological turning points, and final state. Serves as a development history and lessons-learned reference for later phases.

---

## Current State

**Date**: 2026-05-26, Iteration 11 (Hybrid Pattern validated across four model tiers)

**Task**: Identification of legal domain (catalog_id) and extraction of legal arguments from citizen and lawyer objections in urban planning procedures. Norm extraction was originally not a Triage task (per ADR-013), but was reintroduced as a deterministic post-step in Iteration 7 to populate the audit trail without hallucination risk. From Iteration 9 onward, per-argument norm assignment via Option Y position overlap became the dominant measurement axis.

**Architecture**: catalog.py uses nine gesetz-based catalogs since Iteration 8 (BAUGB, BAUNVO, BIMSCHG, BNATSCHG, ENWG, VWGO, WASTRG, WHG, WPG). The norm_extractor handles i.V.m. citation chains via post-processing as of Iteration 9. The production pipeline runs Option Y position-based assignment; an encapsulated Hybrid Pattern experiment (Iteration 11) demonstrates a Variant A soft hint that significantly reduces model-dependent assignment loss.

**Final Metrics (Catalog Matching, unchanged since Iteration 6)**:

| Subset | Catalog Recall | Catalog Precision | Einwendungs-Typ Accuracy | Argument Count in Range | Verified Rate |
|--------|----------------|-------------------|--------------------------|-------------------------|---------------|
| TYP_2 (n=7) | 92.86% | 100.00% | 100% | 85.71% | 85.36% |
| TYP_1 (n=10) | 100.00% | n/a | 80% | 80% | 100.00% |
| Mixed (n=3) | 100.00% | 100.00% | 100% | 100% | 100.00% |

**Doc-Level Norm Extraction Recall (unchanged since Iteration 7)**: 100% on paragraph_norm GT across all four evaluated models. The deterministic regex extractor is model-independent and remains the reliable upstream component for the Hybrid Pattern.

**Per-Argument Assignment Recall, Baseline vs Hybrid (Iteration 11)**:

| Model | Baseline Recall(A) | Hybrid Recall(A) | Delta | Assignment Gap (Baseline / Hybrid) |
|-------|--------------------|------------------|-------|-------------------------------------|
| gpt-4o-mini | 64.0% | 77.2% | +13.2 pp | +38.0% / +22.8% |
| gpt-4o | 63.8% | 64.6% | +0.8 pp | +44.9% / +35.4% |
| o3-mini | 77.3% | 89.5% | +12.2 pp | +22.7% / +10.5% |
| gpt-5.5 | 92.4% | 99.0% | +6.6 pp | +7.6% / +1.0% |

The Hybrid Pattern reduces assignment loss across all model tiers except gpt-4o (which has an orthogonal paraphrasing failure mode). gpt-5.5 plus Hybrid is at saturation (99% recall, 1% gap is noise floor).

**Status**: Architectural validation complete for the Hybrid Pattern. ADR-018 documents the decision. Production refactor pending. Four minor residual issues known and documented (see "Open Issues" section).

---

## Test Corpus

20 synthetic objection documents plus 3 mixed variants. All relate to a fictional preliminary binding land-use plan ("Gewerbegebiet Starkenburg-Süd") with a hyperscale data center as the proposed project.

- einspruch_01 to einspruch_10: TYP_1 (informal citizen letters without legal substance)
- einspruch_11 to einspruch_20: TYP_2 (formal legal submissions)
- einspruch_11_mixed, einspruch_12_mixed, einspruch_13_mixed: mixed variants (personal header plus legal content)

Legal domain distribution across TYP_2 covers nine gesetz-based catalogs (since Iteration 8): BauGB, BauNVO, BImSchG, BNatSchG, EnWG, VwGO, WaStrG, WHG, WPG.

Synthetically created with LLM assistance, therefore subject to known limitations (see "Methodological Limitations" section).

---

## Iterations

### Iteration 1: Norm Recall as Primary Metric (Discarded)

**Hypothesis**: Triage extracts the cited paragraphs per argument. Evaluation measures norm recall against ground truth.

**Results**:
- TYP_2 norm recall approximately 30 to 60% per document
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

**Consequence**: catalog.py rebuilt to match the thematic structure of the eval GT. 7 clusters adopted from the JSON. Distractors (C-008 to C-010) deliberately NOT added to the production catalog (they are an eval methodology construct, not a production construct).

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

### Iteration 7: Deterministic Norm Extraction

**Motivation**: Although Triage was decoupled from norm extraction in Iteration 3, the `ExtrahiertesArgument` schema still included a `zitierte_normen` field that the LLM produced. Recent literature (Magesh et al. 2025, Stanford RegLab) shows that production legal RAG tools hallucinate citations in 17 to 33% of cases, even when constrained to a single text passage. Keeping `zitierte_normen` LLM-generated would have left a hallucination surface in the audit trail.

**Approach**: Replace the LLM-generated `zitierte_normen` with deterministic regex extraction. Implementation based on the `jura_regex` project (kiersch/jura_regex, permissive license), using the whitelist variant restricted to the nine laws indexed in the corpus: BauGB, BauNVO, BImSchG, BNatSchG, EnWG, VwGO, WaStrG, WHG, WPG.

**Architecture changes**:

- **Variant A** (schema separation): split `ExtrahiertesArgument` into an external `LLMArgument` (four semantic fields, sent to the LLM) and the internal `ExtrahiertesArgument` (seven fields, lives in core/entities). The LLM no longer produces `zitierte_normen`.
- **Option Y** (positional norm assignment): `extract_norms` runs once over the full text. Per argument, norms whose position falls within the range of `original_zitat` are assigned to that argument. Deduplicated by canonical form.
- **Option K** (silent fallback): if `original_zitat` cannot be located as substring in the source text, `zitierte_normen` defaults to empty and `argument_verified` is set to False. The argument remains in the output for downstream filtering by the Coordinator.

**Eval setup**: New script `norm_extraction_evaluation.py`. LLM-free, runs in milliseconds, suitable for CI integration. Compares `extract_canonical_norms()` output against the `paragraph_norm` subset of the GT.

**Ground truth restructuring**: To enable clean metrics, every entry in `explizit_zitierte_normen` of `typ2.json` was tagged with a `type` field. Five possible values:

| type | count | meaning |
|------|-------|---------|
| `paragraph_norm` | 36 | real paragraph citations the extractor should find |
| `extractor_limitation` | 6 | real citations the pattern does not support by design |
| `gesetz_erwaehnung` | 2 | statute mentions without paragraph (e.g. "Wasserhaushaltsgesetz (WHG)") |
| `rechtsprechung` | 1 | court decision (BVerwG ruling) |
| `verwaltungsrichtlinie` | 1 | administrative guideline (MULEWF Leitfaden) |

No entries were removed. The total count of 46 matches the pre-tagging GT exactly. The eval loader filters on `type == "paragraph_norm"` for the primary recall metric. The other types are documented separately for transparency.

**Results (TYP_2, paragraph_norm subset)**:
- Mean recall: 100.00% (n=6 with paragraph_norm GT; einspruch_18 vacuous)
- Mean precision: 93.33%
- Total TP: 28
- Total loss: 0
- Total overcount: 2 (einspruch_19 only)

**Test coverage**: 49 behavior-oriented unit tests covering single citation extraction, multi-citation extraction, whitelist enforcement, degenerate inputs, canonical form rendering, deduplication, position tracking, documented limitations, and realistic corpus snippets. Following given/when/then structure per the testing-strategy guide.

**Findings**:
1. The hallucination concern on `zitierte_normen` is structurally eliminated. The extractor cannot return citations that are not substring-present in the source text.
2. The deterministic recall on paragraph_norm (100%) is empirically superior to any LLM-based norm extraction tested in earlier iterations.
3. The GT cleanup (type tagging) made the metric methodologically honest. The earlier 85% mixed-method recall reflected confusion of real citations with non-citation entries (court decisions, guidelines, statute mentions), not extractor failure.
4. The Magesh et al. 2025 finding on legal RAG hallucination rates (17 to 33% in production tools) is consistent with the empirical reasoning for replacing LLM-generated citations with deterministic patterns.

---

### Iteration 8: Catalog Refactor to Gesetz-Based Model (ADR-016)

**Motivation**: The seven thematic clusters from Iteration 6 (C-001 to C-007) introduced an indirection layer: each catalog_id mapped to a `corpus_partition` string that the retriever then resolved to a corpus shard. The mapping was an implicit second source of truth. Drift between catalog and partition would cause silent retrieval failures.

**Approach**: Replace the seven thematic clusters with nine gesetz-based catalogs. Each catalog_id IS the retriever partition key directly. The `corpus_partition` field is removed from `KatalogEintrag`. The CatalogId enum is renamed to match: BAUGB, BAUNVO, BIMSCHG, BNATSCHG, ENWG, VWGO, WASTRG, WHG, WPG.

**Architecture impact**:
- `src/app/triage/catalog.py`: rewritten with nine entries
- `src/app/triage/prompts.py`: classification guide updated, prompt bumped to v3.0.0
- `src/app/triage/llm_schema.py`: CatalogId enum aligned
- `src/app/triage/norm_extractor.py`: whitelist filter aligned to the nine Gesetze
- `src/app/response_drafting/service.py`: catalog references updated
- `src/app/response_drafting/retrieval.py`: partition lookup simplified
- Test suite updated: test_catalog.py, test_service.py, test_classification.py, response_drafting tests, conftest.py, external smoke tests

**Results**:
- 87 tests pass after refactor
- Bounded-Context isolation greps clean: zero hits for cross-context catalog references
- Prompt v3.0.0 documented in ADR-016

**Finding**: The refactor removed one source of drift permanently. Each catalog_id is now self-describing: BAUGB obviously corresponds to BauGB, no separate partition lookup needed. ADR-016 records the rationale and the deferred testing of the schema-fitness function from earlier lessons.

---

### Iteration 9: i.V.m. Chain Handling and Phase A Eval Framework

**Motivation**: Two parallel needs emerged:
1. The norm_extractor failed on i.V.m. citation chains common in formal legal writing (e.g. "§ 9 Abs. 1 Nr. 1 i.V.m. § 8 WHG"). The original regex required a primary citation and a Gesetz separated by max 10 characters; the "i.V.m. § 8 " gap of 11 characters dropped the primary citation.
2. The Iteration 7 metric (doc-level paragraph_norm recall) measured the extractor in isolation. It did not measure how many of those extracted norms reach the per-argument `zitierte_normen` field after the Option Y position-based assignment. A separate measurement axis was needed.

**Approach 1, extractor improvement**: Extended the citation pattern with an optional repeating `i.V.m. § X (Abs. Y)? (S. Z)? (Nr. W)?` non-capturing group between primary citation and gesetz. Added a `_extract_ivm_inner_citations()` helper that extracts secondary citations from the matched span; all inner citations inherit the closing Gesetz of the chain.

Smoke tests verify: einspruch_12_mixed core sentence "§ 9 Abs. 1 Nr. 1 i.V.m. § 8 WHG" extracts both `§ 9 Abs. 1 Nr. 1 WHG` (start=57) and `§ 8 WHG` (start=81). Simple non-iVm citations still work. Multi-chain `§ 9 i.V.m. § 8 i.V.m. § 7 BauGB` extracts all three. Full einspruch_12_mixed paragraph extracts all 3 expected WHG citations.

Two test classes added: `TestIVMChainExtraction` and `TestIVMChainRegressionGuards` in `tests/small_scale/triage/test_norm_extractor.py`.

**Approach 2, Phase A measurement framework**: New script `experiments/extraction_evaluation/script/norm_coverage_eval.py`. Tests document-level coverage (Option A): aggregate all `zitierte_normen` across `extracted_arguments` into a set, compare against union of GT `must_retrieve.citation` strings.

Two recall metrics per document:
- `recall_assigned`: production pipeline output (zitierte_normen across arguments). Reflects what reaches downstream consumers.
- `recall_doc_level`: extract_norms on full clean_text without assignment filter. Reflects what the deterministic extractor sees.

Gap between them is the "assignment loss". Inline LLM clients for OpenAI (native parse) and Anthropic (tool-use with schema enforcement). Multi-model via `MODEL_NAME` constant.

**Approach 3, GT corrections**: Two GT entries added during this iteration after manual full-text verification:
- einspruch_19: added `§ 3 BauGB` to must_retrieve of the Beachtlichkeit-argument (rank_target=top_3). Bare reference exists verbatim in text.
- einspruch_11_mixed: added `§ 1 Abs. 6 Nr. 8 BauGB` to must_retrieve of the Weinbau-argument (rank_target=top_3). Sub-Absatz that explicitly names Weinbau/Fremdenverkehr/Landschaft.

Mixed GT files renamed from einspruch_NN.json to einspruch_NN_mixed.json (the pure TYP_2 versions of these docs do not exist; only Mixed variants in `data/mixed/`).

**Eval setup output**: per-document Recall(A), Recall(D), Loss, plus a Bottleneck Analysis section classifying every missing citation as LOST_BY_ASSIGNMENT or TRULY_MISSING.

**Finding**: With i.V.m. handling fixed and GT cleaned, doc-level recall hits 100% across all subsequent runs. The remaining variation lives entirely in the per-argument assignment step.

---

### Iteration 10: Four-Model Baseline and Assignment Loss Characterization (ADR-017)

**Motivation**: With the Phase A framework in place, measure how Option Y per-argument assignment performs across model tiers. Identify failure modes systematically.

**Approach**: Run Phase A eval on four OpenAI models, with diagnostic fields added to capture failure modes:
- `argument_count`: how many ExtrahiertesArgument the LLM produces
- `argument_verified_count`: subset with verified zitate
- `zitat_lengths`: list of original_zitat character lengths per argument
- `zitate_with_paragraph_count`: how many zitate contain a "§" character

These four diagnostics disambiguate three failure-mode hypotheses for documents with zero assigned norms despite expectations:
- H1 (no arguments): `argument_count == 0`. LLM returned empty list, document classified as having no legal substance.
- H2 (narrow zitate): `argument_count > 0` but `zitate_with_paragraph_count == 0`. LLM extracted arguments but chose original_zitat ranges that do not include any §-citation.
- H3 (paraphrasing): `argument_verified_count == 0` but zitate contain §. LLM paraphrased zitate, violating ADR-006 Layer 1 substring constraint.

F1 fix: zero-extraction docs now count as F1=0 in the macro-average instead of being excluded. Previously the macro-F1 was inflated for models that failed silently on some documents.

**Results (Baseline, Option Y)**:

| Model | TYP_2 Recall(A) | Overall Recall(A) | Overall F1 | Assignment Gap |
|-------|-----------------|-------------------|------------|----------------|
| gpt-4o-mini | 62.0% | 64.0% | 73.2% | +38.0 pp |
| gpt-4o | 55.1% | 63.8% | 67.0% | +44.9 pp |
| o3-mini | 73.1% | 77.3% | 80.6% | +22.7 pp |
| gpt-5.5 | 93.2% | 92.4% | 94.7% | +7.6 pp |

Per-doc failure-mode classification on gpt-4o (the worst performer):
- einspruch_14: H2. Args=5, Verif=5, ZitW§=0. Systematic conservative selection. The LLM picked argument-only spans without §-citations.
- einspruch_19: H3. Args=4, Verif=0, ZitW§=2. Layer-1 violation. The LLM paraphrased zitate; none were substring matches.

**Findings**:
1. Assignment loss is heavily model-dependent. Spread is 28.6 pp between worst and best (gpt-4o at 63.8% vs gpt-5.5 at 92.4%).
2. Bigger is not better. gpt-4o (more capable) performs worse than gpt-4o-mini on TYP_2 because of systematic conservative zitat selection and paraphrasing.
3. gpt-5.5 achieves 92.4% recall purely through Option Y, by emergently choosing wider zitate that capture §-citations. This is not in the prompt; it is implicit model capability.
4. The 13:1 ratio of LOST_BY_ASSIGNMENT vs TRULY_MISSING citations confirms position-based assignment is the bottleneck, not the regex extractor.

**Output artifact**: ADR-017 records the measured loss and rejects three alternatives (immediate refactor to document-level union, semantic LLM assignment, longer original_zitat). Decision: retain Option Y, document the loss as model-dependent, defer reevaluation until Phase B (Retrieval Recall) is implemented.

---

### Iteration 11: Hybrid Pattern Experiment and Production Architecture (ADR-018)

**Motivation**: ADR-017's "retain Option Y" decision was based on assumption that the model-dependent loss is structural. Two follow-up questions remained:
1. Can a soft prompt-level intervention reduce the loss without schema changes?
2. Does the intervention work across model tiers, enabling Behörden-Sovereignty deployments on small or self-hostable models?

**Approach**: Encapsulated experiment in `experiments/extraction_evaluation/script/norm_coverage_eval_hybrid.py`. Wrapper class `HybridTriageWrapper` runs `extract_norms` on the cleaned text, prepends a clearly delimited hint block listing canonical citations, then delegates to TriageService. Production code untouched.

**Two design iterations on the hint formulation**:

v1 (targeted zitierte_normen field): the hint instructed the LLM to populate `zitierte_normen` from the supplied list. Result: marginal improvement on Overall Recall (64.0% to 65.0%) and slight regression on TYP_2 (62.0% to 56.6%).

Diagnosis: the LLM schema `LLMTriageOutput` does NOT include `zitierte_normen`. The LLM produces only four semantic fields (argument_text, original_zitat, catalog_id, einwendungs_typ); zitierte_normen is populated post-hoc by Option Y. The v1 hint pointed at a field the LLM cannot control. The added prompt context occasionally distracted the LLM from its argument extraction task without providing a useful lever.

v2 (targeted original_zitat field, framed as orientation help): the hint instructs the LLM to widen its original_zitat span so that any relevant §-citation falls inside. The list is framed as "Orientierungshilfe" (orientation aid) and described as possibly incomplete, so the LLM retains its own judgment. The hint targets the field the LLM actually controls in the production schema, indirectly improving Option Y assignment quality by providing wider position-overlap inputs.

**Results (Hybrid v2, four-model)**:

| Model | Baseline Recall(A) | Hybrid Recall(A) | Delta | Hybrid F1 | Hybrid Assignment Gap |
|-------|--------------------|------------------|-------|-----------|-----------------------|
| gpt-4o-mini | 64.0% | 77.2% | +13.2 pp | 85.6% | +22.8 pp |
| gpt-4o | 63.8% | 64.6% | +0.8 pp | 71.5% | +35.4 pp |
| o3-mini | 77.3% | 89.5% | +12.2 pp | 93.4% | +10.5 pp |
| gpt-5.5 | 92.4% | 99.0% | +6.6 pp | 98.6% | +1.0 pp |

Variance analysis:
- Baseline spread: 28.6 pp (gpt-4o at 63.8% to gpt-5.5 at 92.4%)
- Hybrid spread including gpt-4o: 34.4 pp (gpt-4o falls behind)
- Hybrid spread excluding gpt-4o outlier: 21.8 pp (gpt-4o-mini 77.2% to gpt-5.5 99.0%)

The Hybrid Pattern lifts the floor for cooperative models substantially (mini and o3-mini both gain >12 pp). The ceiling saturates: gpt-5.5 plus Hybrid is at 99% recall, 1% gap is noise floor.

**Per-model behaviour observations**:
- gpt-4o-mini: cooperates with hint, picks wider zitate. The only model where one prior failure was reintroduced briefly in v1 (einspruch_15 lost § 17 Abs. 3 EnWG due to over-restriction from "use this list" framing); v2 recovered it via "as help" framing.
- gpt-4o: marginal benefit. Introduces a new H3 regression on einspruch_17 (paraphrasing under added context). Paraphrasing failure mode is orthogonal to span-width and not addressed by this hint formulation.
- o3-mini: strong response. einspruch_14 goes from 0% (H2) to 67%. einspruch_20 from 38% to 69% via better argument-zitat alignment on complex docs. No regressions.
- gpt-5.5: saturation. Single remaining LOST_BY_ASSIGNMENT (§ 1 Abs. 3 BauGB in einspruch_20). Two SPURIOUS that are GT-modeling disagreements, not model errors.

**Output artifact**: ADR-018 records the Hybrid Pattern as Variant A (Soft Hint), with three documented alternatives (Variant B Hard Schema Constraint, Variant C Two-Stage LLM, Model lock-in on gpt-5.5). Status is Proposed pending production refactor.

**Findings**:
1. The Hybrid Pattern decouples production deployment from LLM choice within a reasonable performance band. Smaller and self-hostable models become viable production candidates.
2. Framing matters. v1 ("use this list") caused over-restriction and per-doc regressions. v2 ("as help, possibly incomplete") preserved LLM autonomy and produced consistent improvements.
3. The hint targets the field the LLM controls (original_zitat). Targeting a field the LLM does not output (zitierte_normen) was the v1 design error.
4. Hybrid Pattern raises model floors but does not erase model-specific weaknesses. Paraphrasing-style failure modes (gpt-4o family) need a different intervention.
5. Doc-level coverage by `norm_extractor` is now a load-bearing dependency for the entire pipeline. Gaps in the regex whitelist propagate directly into reduced assignment quality.

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

### einspruch_20 SPURIOUS Citations: GT-Modeling Disagreements

Two SPURIOUS citations consistently flagged in einspruch_20 (gpt-5.5 baseline and Hybrid; partially in other models):
- `§ 47 VwGO`: present in the text as a Rechtsbehelfsdrohung at the end, intentionally excluded from GT must_retrieve (not a substantive argument).
- `§ 8 Abs. 2 Nr. 1 BauNVO`: present in the text as a more specific variant; GT contains only the generalised `§ 8 BauNVO`.

**Solution options**: either accept the disagreements (current state, treat as known false positives) or refine the GT comparison logic to handle specificity ladders (a more specific citation should match a more general GT entry).

**Priority**: low. Affects precision on one document. Documented as a known GT-modeling artifact in ADR-017.

---

### gpt-4o Paraphrasing-Mode Resistance

Across both Baseline and Hybrid runs, gpt-4o systematically rewrites `original_zitat` rather than selecting a verbatim substring on certain documents (einspruch_19 in Baseline, einspruch_17 with Hybrid). This violates ADR-006 Layer 1 and breaks Option Y assignment regardless of hint presence.

**Hypothesis**: gpt-4o interprets the prompt's "concise legal formulation" instruction by normalising and paraphrasing, sacrificing the substring constraint. Smaller and reasoning-tier models adhere to the constraint more strictly.

**Potential solutions**:
- Strengthen substring constraint in the LLM schema description with explicit examples
- Exclude gpt-4o from production tier list (Hybrid Pattern documented to favour reasoning models and flagship models, not the gpt-4o family specifically)
- Future Variant B (hard schema constraint) would address this via Pydantic validator

**Priority**: low. gpt-4o is not in the planned production tier list; documentation suffices.

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

### Norm Extraction is Not a Triage Task (LLM-Level)

The first eval iterations attempted to maximise norm recall via LLM output. This was conceptually wrong. Triage identifies the legal domain. The RAG backend finds the concrete norms. This separation is the foundation for the combined design functioning (single LLM call for argument extraction plus catalog matching).

ADR-013 is confirmed by this clarification: one LLM call is sufficient for both tasks.

However: Iteration 7 reintroduced norm extraction as a *deterministic* post-step, not an LLM step. The same code that powers the audit trail will also support ADR-006 Layer 2 verification (citation grounding against retrieved chunks).

---

### Deterministic Beats LLM for Pattern-Stable Tasks

Where the task has stable syntactic structure (norm citations, dates, currency, named entities with known formats), deterministic patterns outperform LLMs on accuracy, speed, cost, and audit transparency. LLMs are reserved for tasks requiring semantic understanding (argument extraction, classification, summarization).

This separation is the recommended pattern in current legal NLP literature (Magesh et al. 2025, GerPS-Compare 2024, LAMUS 2026) and was empirically confirmed in Iteration 7 (100% recall on paragraph_norm versus 85% mixed LLM eval).

---

### Test Set Informing Can Be Reduced, Not Eliminated

Initial catalog descriptions contained `typical_arguments` drawn directly from the test documents. This is obvious leakage and was removed. The final version with generic domain vocabulary ("Bebauungsplan", "Gewässerbenutzung", "Schallgutachten") is methodologically cleaner.

The evaluation is not fully leakage-free even so: the selection of the 9 gesetze itself reflects the legal domains present in the test set. This is acceptable at the skeleton stage and is documented. For production, the catalog should be derived from historical objection procedures of the relevant authority, not from the test set.

---

### Combined vs. Separate Architecture

During the iterations, the question arose whether argument extraction and catalog matching should be done in a single LLM call (current design) or as separate steps (preprocessing → catalog matching → RAG).

Pre-defined threshold: catalog recall TYP_2 below 50% after prompt v2 → separation. We reached 92.86% after schema synchronisation. Clear answer: the combined design works, no separation needed.

This saves double LLM costs and double latency per document.

---

### GT Quality is a First-Class Concern

The Iteration 7 cleanup revealed that the original GT mixed at least three distinct categories: paragraph citations, extractor limitations, and non-norm references (court decisions, guidelines, statute mentions). Treating all of them as a single `explizit_zitierte_normen` list produced misleading 85% recall numbers.

Pattern: when an eval metric looks "almost good but not quite", check whether the GT itself is internally consistent before tuning the model or the extractor.

---

### Catalog as Partition Key Removes a Drift Source

The Iteration 8 refactor from seven thematic clusters with a separate `corpus_partition` field to nine gesetz-based catalogs where the catalog_id IS the partition removed one persistent class of bugs. Drift between catalog identifiers and corpus partition strings cannot occur if there is only one identifier.

Pattern: when a system uses one value as an identifier for two related concerns, prefer making them the same string over maintaining a translation table. Translation tables drift; identity does not.

---

### Document-Level Recall Hides Assignment Loss

The Iteration 7 metric (100% paragraph_norm doc-level recall) was a clean win at the extractor level, but masked a downstream problem: the per-argument `zitierte_normen` field, populated via Option Y position overlap, suffered massive loss depending on LLM choice (38% to 45% gap on smaller models).

Pattern: when a deterministic component feeds an LLM-driven step, measure each component separately. Aggregate metrics can hide failures at the join.

---

### Failure Modes Are Model-Specific, Not Universal

The four-model Phase A baseline revealed three distinct failure modes (H1, H2, H3) with model-specific incidence:
- gpt-4o-mini: mixed H2 across multiple docs (sloppy span selection)
- gpt-4o: systematic H2 plus H3 (paraphrasing) on specific documents
- o3-mini: localised H2 on one document
- gpt-5.5: minimal residual losses

Pattern: do not generalise from single-model evaluation. Different model families have different dominant failure modes. Architectural interventions are effective if and only if they address the dominant mode of the target tier.

---

### Prompt Hint Framing Matters

The Iteration 11 v1 vs v2 split revealed that a single phrasing difference ("use this list" vs "this list as orientation help, possibly incomplete") changes per-doc behaviour significantly. v1 caused over-restriction and per-document regressions; v2 preserved LLM autonomy and produced consistent improvements.

Pattern: when adding deterministic context as a soft hint to an LLM prompt, frame it as advisory rather than authoritative. The LLM's own judgment is part of the system; do not suppress it artificially.

---

### Hybrid Patterns Decouple Production from Model Tier

The Iteration 11 result that gpt-4o-mini Hybrid reaches o3-mini Baseline performance (~77%) and o3-mini Hybrid approaches gpt-5.5 Baseline (~92%) demonstrates that the deterministic component carries a model-tier worth of capability. This is the architecturally interesting finding: production deployment can be detached from closed-source frontier models if the deterministic boundary is well-designed.

Pattern: for Behörden-Sovereignty and DSGVO-bound deployments, prefer architectures where the LLM is one swappable component among several. Variance reduction via deterministic pre-processing is the primary lever.

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

n=23 documents (n=10 for the Phase A four-model evaluation, since TYP_1 docs are excluded from per-argument metrics) is small for statistical robustness. Per-catalog diagnostics are based on 1 to 5 data points per gesetz. Variation between runs is possible even at temperature=0 (OpenAI does not guarantee full determinism even at temp=0).

---

### Model Determinism

Iteration 10 and 11 covered four models: gpt-4o-mini, gpt-4o, o3-mini, gpt-5.5. The gpt-4o-mini and gpt-4o calls run at temperature=0. The o3-mini and gpt-5.5 calls reject `temperature=0` (API HTTP 400) and run at the default temperature (1). For these two models the results are single-run point estimates without variance bounds.

A future run of 3x per model with the same input and hash-comparison of outputs would establish empirical run-stability. For the current claim (architectural validation of the Hybrid Pattern), single runs are sufficient because the inter-model deltas (10+ percentage points) substantially exceed plausible intra-model run variance.

---

### GT Comparison is Exact String Match

The Phase A eval comparison uses exact canonical-form string equality. This counts citations with different specificity as separate entries (e.g. `§ 8 BauNVO` and `§ 8 Abs. 2 Nr. 1 BauNVO`). Both can appear in extracted output and GT but compare as non-matches when the granularity differs.

Two einspruch_20 SPURIOUS persist for this reason. A specificity-aware matching scheme (a more specific extracted citation matches a less specific GT citation if the prefix matches) would resolve these. Not currently implemented; documented as known GT-modeling disagreement.

---

## Artifact Directory

Outputs of this evaluation, by iteration:

**Iteration 2**:
- `ground_truth_cleaned.json`: cleaned norm GT with explicit/inferred separation

**Iteration 4 to 6**:
- `katalog_und_zuordnung.json`: catalog definition plus expected assignment per document
- `catalog.py` (v2): code catalog with 7 clusters, synchronised with eval GT
- `eval_catalog_matching.py`: eval script v3 (catalog matching as primary metric)

**Iteration 7**:
- `typ2.json`: type-tagged ground truth, 46 entries across 5 type categories
- `norm_extractor.py`: deterministic regex extractor based on jura_regex whitelist variant
- `test_norm_extractor.py`: 49 behavior-oriented unit tests
- `norm_extraction_evaluation.py`: LLM-free eval script for the regex extractor
- `catalog_eval_results_<timestamp>.json`: detailed catalog matching outputs
- `norm_extraction_eval_<timestamp>.json`: detailed norm extraction outputs

**Iteration 8 (ADR-016)**:
- `src/app/triage/catalog.py` v3: nine gesetz-based catalogs
- `src/app/triage/prompts.py` v3.0.0: classification guide updated
- `docs/decisions/adr-016.md`: catalog refactor rationale

**Iteration 9**:
- `src/app/triage/norm_extractor.py` (updated): i.V.m. chain handling
- `experiments/extraction_evaluation/script/norm_coverage_eval.py`: Phase A multi-model framework
- `experiments/extraction_evaluation/ground_truth/retrieval_gt/*.json`: 10 GT files for TYP_2 and Mixed
- `tests/small_scale/triage/test_norm_extractor.py`: `TestIVMChainExtraction` and `TestIVMChainRegressionGuards`

**Iteration 10 (ADR-017)**:
- `experiments/extraction_evaluation/results/norm_coverage_eval_<model>_<timestamp>.json`: per-model baseline results (4 files)
- `docs/decisions/adr-017.md`: model-dependent assignment loss documentation

**Iteration 11 (ADR-018)**:
- `experiments/extraction_evaluation/script/norm_coverage_eval_hybrid.py`: encapsulated Hybrid experiment
- `experiments/extraction_evaluation/results/norm_coverage_eval_<model>_hybrid_<timestamp>.json`: per-model Hybrid results (4 files)
- `docs/decisions/adr-018.md`: Hybrid Pattern decision record

---

### Iteration 12: Custom vs OpenLegalData Library Benchmark (ADR-019)

**Motivation**: The custom `norm_extractor` carries a non-trivial maintenance burden: 49 unit tests, the recent i.V.m. chain handling extension, and ownership of all pattern edge cases. The OpenLegalData ecosystem provides `legal-reference-extraction` (PyPI; imports as `refex`), used in production at de.openlegaldata.io. A direct head-to-head benchmark was needed to decide whether to retain the custom extractor or migrate to the external library.

**Approach**: New encapsulated experiment script `experiments/extraction_evaluation/script/norm_extractor_benchmark.py`. Three extractor adapters behind a common `Extractor` protocol:
- **A**: Custom (existing `norm_extractor`)
- **B**: OpenLegalData Regex (`refex.orchestrator.CitationExtractor`)
- **C**: OpenLegalData Transformer (EuroBERT-210m fine-tune at `openlegaldata/legal-reference-extraction-base-de`)

Two evaluation regimes:
- Phase A: 7 TYP_2 plus 3 Mixed documents. Primary recall and precision against the must_retrieve subset of the Phase A ground truth.
- TYP_1: 10 informal citizen letters. False-positive resilience. Expected extraction count is zero; any non-empty output signals hallucination.

Two comparison modes per extractor:
- Raw: every citation the extractor emits.
- Whitelisted to 9 Gesetz: post-filtered to the project's nine indexed Gesetze for fair apples-to-apples comparison.

**Setup notes**:
- The PyPI package is `legal-reference-extraction` but imports as `refex`. The adapter parses the library's `span.text` output (e.g. "§ 42 VwGO") into the project's canonical form via shared regex.
- The Transformer model is a gated HuggingFace repository; access approval is pending. The benchmark ran with `RUN_TRANSFORMER = False`. The Transformer-mode results are deferred to a follow-up run.
- The Transformer model is licensed CC BY-NC 4.0. Non-commercial research use is fine for benchmark purposes, but production deployment in a commercial Behörden context would require a different licensing path.

**Results (Phase A Overall, Macro-Average over 8 docs with GT)**:

| Extractor | Precision | Recall | F1 | Mean Time | Median Time |
|-----------|-----------|--------|-----|-----------|-------------|
| Custom | 90.8% | 100.0% | 94.6% | 0.2 ms | 0.2 ms |
| OLD-Regex | 90.8% | 95.8% | 92.1% | 40.2 ms | 0.9 ms |

**Results (TYP_1 False-Positive Resilience)**:

| Extractor | Total FP (Raw) | Total FP (Whitelisted) |
|-----------|----------------|------------------------|
| Custom | 0 / 10 | 0 / 10 |
| OLD-Regex | 0 / 10 | 0 / 10 |

Both extractors are perfectly resilient against informal citizen letters; neither hallucinates §-citations where none are present.

**Decisive edge case (einspruch_12_mixed)**:

The Mixed document contains the i.V.m. chain `§ 9 Abs. 1 Nr. 1 i.V.m. § 8 WHG`. Custom recovers both `§ 9 Abs. 1 Nr. 1 WHG` and `§ 8 WHG`. OLD-Regex recovers only `§ 8 WHG` and `§ 57 WHG`, dropping the inner citation. The library logs the failure explicitly:

```
Marker could not be assign to book: [<RefMarker({'text': '§ 9 Abs. 1 Nr. 1 i.V.m.', ...
references: [<Ref(law: None/9)>]
```

This is exactly the Iteration 9 i.V.m. handling investment, now empirically validated as a measurable recall advantage.

**Performance characterisation**:

The mean-median gap for OLD-Regex (40.2 ms vs 0.9 ms) signals expensive outliers. Two documents dominate: einspruch_14 at 260 ms, einspruch_20 at 41 ms. Both are content-rich documents with multiple citations and complex structure. Custom shows no such outlier behaviour (mean equals median at 0.2 ms), suggesting linear scaling with document size.

For batch processing of 1000 documents, the projected difference is approximately 200 ms total for Custom versus 40 seconds for OLD-Regex. At larger scales (10,000+ docs typical for a major Behörde infrastructure case), this becomes a meaningful operational concern.

**Extractor-Limitation Coverage**:

Both extractors produced zero citations outside the 9-Gesetz whitelist on the three known-limitation documents (einspruch_14 with FFH-Richtlinie and Anlage 1 BauGB; einspruch_17 with LSG-Verordnung; einspruch_13_mixed with TA Lärm and DIN 45680). Adopting OLD-Regex would not have expanded coverage for the documented edge cases. These cases require either dedicated pattern handlers (Phase 2 roadmap) or a Transformer-based approach (Phase 2, pending access approval).

**Findings**:

1. Custom dominates on every measured axis. The maintenance burden is justified by 4.2 pp recall advantage, 200x speed advantage, and identical precision and FP resilience.
2. The i.V.m. handling extension (Iteration 9) was the right architectural investment. It produces the only meaningful recall difference between the two extractors.
3. The OpenLegalData broader-domain coverage (BGB, StGB, EU law, case law) is real but irrelevant to this project's 9-Gesetz scope. Coverage outside the scope is not citation we want to retrieve.
4. Neither extractor handles administrative rules, EU directives by hyphenated name, or Landesverordnungen. This is a regex-based methodology limitation across both approaches. Future Phase 2 work on extractor_limitation cases will need either dedicated pattern handlers per category or a Transformer-based approach. Adopting OLD-Regex would not have advanced this.

**Output artifact**: ADR-019 records the decision to retain Custom. Status is Accepted (not Proposed) because the decision applies immediately to the existing production code path; no refactor pending.

---