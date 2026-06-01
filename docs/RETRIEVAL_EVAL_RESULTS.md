# Retrieval Evaluation Results

Workflow and measurement log for the Retrieval bounded context, which resolves canonical norm citations from Triage to their source Gesetzestext. Companion to PREPROCESSING_EVAL_RESULTS.md (which covers the Triage-side norm extraction and assignment). Decisions are recorded in ADR-020 (separate bounded context) and ADR-021 (exact-match-only resolution).

---

## Current State

- Date: 2026-05-27
- Iteration: 14
- Bounded context: Retrieval (`src/app/retrieval/`), separated per ADR-020 from the context that consumes the resolved norms (Briefing, formerly ResponseDrafting, renamed per ADR-022).
- Production resolution strategy: exact-match only, per ADR-021. Vector fallback measured as unnecessary and removed from the production path.
- Corpus: nine local gesetze-im-internet.de XML files, 1254 paragraphs total, representing the current Behörde state.

---

## Iteration 14: NormResolution with Hybrid Retrieval (pre-registered)

### Pre-Registration

This iteration was the first run under the process changes from LESSONS_LEARNED_EXPERIMENTS.md. The plan (iteration_14_plan.md) was written and committed before implementation, with two pre-registered hypotheses:

- H-A: exact-match alone resolves 80 to 90% of canonical citations.
- H-B: vector-similarity fallback recovers the remainder, lifting overall recall to at least 95%.

Predicted overall resolution recall: at least 95%.

### Component Build and Validation

**XML loader (`GesetzXMLLoader`)**. Parses the gesetze-im-internet.de format into per-paragraph GesetzParagraph entities. Validated against all nine statute files:

| Statute | Paragraphs | With title |
|---------|-----------|-----------|
| BauGB | 288 | 288 |
| BauNVO | 37 | 37 |
| BImSchG | 110 | 110 |
| BNatSchG | 87 | 87 |
| EnWG | 301 | 301 |
| VwGO | 203 | 3 |
| WaStrG | 56 | 55 |
| WHG | 136 | 136 |
| WPG | 36 | 36 |
| Total | 1254 | |

Frame norms, tables of contents, structural (Gliederung) norms, appendices, and repealed paragraphs are filtered out. Statute abbreviation is taken from amtabk with jurabk fallback. No duplicate canonical keys across the corpus, so the exact-match index is clean.

The VwGO title gap (3 of 203) was investigated and confirmed correct: the VwGO carries no paragraph headings in the source for most of its paragraphs (verified against § 54 and § 55, which have only an enbez and body text, no titel element). This is a property of the statute, not a loader defect. Exact-match resolution does not depend on the title, so titleless VwGO paragraphs resolve normally.

**Embedder (`E5Embedder`)**. Wraps multilingual-e5-large with the required query/passage prefixes and L2 normalisation for cosine-equivalent inner-product search. Local inference, no external API, consistent with the EU-sovereignty posture.

**Vector index (`FaissNormIndex`)**. faiss-cpu IndexFlatIP over the paragraph embeddings, with Gesetz-suffix-filtered top-k search to prevent a section number in one statute matching a query for another.

**Resolver (`NormRetrievalService`)**. Hybrid orchestration: exact-match dictionary keyed on the paragraph-level canonical key, vector fallback on a miss with a confidence floor.

### Setup Smoke Test

Per the provider-compatibility lesson, a smoke test ran the real e5 model and the full index over seven probe citations before the eval. Findings:

- All exact-match probes (§ 9 WHG, § 9 BauGB, § 1 BauGB, § 42 VwGO) resolved sub-millisecond with correct text. Gesetz isolation confirmed: § 9 WHG and § 9 BauGB return different paragraphs.
- The sub-paragraph probe (§ 9 Abs. 1 Nr. 1 WHG) drilled to § 9 WHG via exact-match.
- The titleless VwGO probe (§ 42 VwGO) resolved correctly, confirming the title gap is harmless for exact-match.
- The forced-fallback probe (§ 999 WHG, a non-existent paragraph) resolved via vector to § 105 WHG with cosine 0.801, a confident-looking but wrong match just above the 0.80 floor. This was the first signal that the floor was too low and that the fallback produces false positives on out-of-corpus citations.

### Measurement

eval_retrieval_recall ran every must_retrieve citation from the Phase A ground truth through the resolver. 25 unique citations across six statutes.

**Resolution method distribution:**

| Method | Count | Share |
|--------|-------|-------|
| exact | 25 / 25 | 100.0% |
| vector | 0 / 25 | 0.0% |
| unresolved | 0 / 25 | 0.0% |

**Per-Gesetz:**

| Gesetz | exact | vector | unresolved |
|--------|-------|--------|-----------|
| BNatSchG | 3 | 0 | 0 |
| BauGB | 13 | 0 | 0 |
| BauNVO | 3 | 0 | 0 |
| EnWG | 2 | 0 | 0 |
| WHG | 3 | 0 | 0 |
| WPG | 1 | 0 | 0 |

Overall resolution recall: 25/25, 100.0%.

### Predicted vs Measured

| Hypothesis | Predicted | Measured |
|------------|-----------|----------|
| H-A (exact-match share) | 80 to 90% | 100% |
| H-B (vector fallback lift) | recovers remainder to >= 95% | fallback unused (0%) |
| Overall recall | >= 95% | 100% |

H-A was too conservative. H-B was unnecessary: there was no remainder for the fallback to recover. The exact-match path alone exceeded the combined prediction.

### Finding

The paragraph-level normalisation in the exact-match path absorbs the entire granularity drift that the vector fallback was designed for. A citation such as "§ 9 Abs. 1 Nr. 1 WHG" reduces to the key "§ 9 WHG", which is present in the index. Because both the norm_extractor (validated in Iterations 9 and 12) and the XML loader produce clean canonical "§ N Gesetz" forms, every valid citation hits an exact key, and the Gesetz suffix is consistent across all six tested statutes (no cross-statute key collisions, no suffix mismatches).

The vector fallback is therefore not merely unused but a liability: the § 999 WHG smoke-test case showed it returns a confident-wrong match (§ 105 WHG at 0.801) for an out-of-corpus citation, where an honest "not found" is the safer behaviour in a legal context.

### Decision

Exact-match-only resolution in production (ADR-021). The vector fallback, the E5Embedder, and the FaissNormIndex are removed from the production path and retained as reversible experimental reference under `experiments/`. The decision is reversible: if production data later exhibits genuine drift that exact-match misses, the hybrid path can be reinstated with a floor calibrated above the observed 0.801 false-positive level.

---

## Process Notes

This iteration followed the LESSONS_LEARNED_EXPERIMENTS.md changes:

- Pre-registration (iteration_14_plan.md) written and committed before code, with explicit hypotheses and a stop rule.
- Provider-compatibility smoke test (the e5 model load plus probe resolution) run before the full eval, which surfaced the 0.801 false-positive early.
- Predicted-versus-measured comparison recorded explicitly above.
- A negative result (vector fallback unnecessary) documented as a first-class outcome and used to simplify the production design, mirroring the v3 tagged-context rejection in Iteration 13.

Outstanding process items deferred to the next iteration: the embedding index build (when the vector path is exercised experimentally) had no progress reporting and no on-disk persistence, so each run re-embedded the full corpus on CPU. Since the production path no longer embeds, this is now only relevant to experimental reruns; if those become frequent, index persistence and a progress bar should be added.

---

## Open Issues

- The resolution recall is measured at paragraph granularity. A citation more specific than a paragraph resolves to the full paragraph text, which is acceptable for the Behörde use case (the Sachbearbeiter sees the whole provision) but means sub-paragraph precision is not separately measured.
- The 25-citation test set covers six of the nine statutes. BImSchG, VwGO, and WaStrG citations did not appear in the Phase A must_retrieve sets and are therefore not exercised by the recall measurement, though the loader validated all nine and the smoke test exercised one VwGO citation.
- Coupling to the pipeline: the Coordinator must collect canonical citations from the Triage output, pass them to the Retrieval resolve step, then map the resolved norms into the Briefing context's `ResolvedNormEntry` and pass them to the deterministic Briefing assembly (ADR-022). This wiring is the next implementation step.