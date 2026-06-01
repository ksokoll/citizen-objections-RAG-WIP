# Lessons Learned: Experiment Execution and Methodology

A reflection on the multi-iteration evaluation work documented in `PREPROCESSING_EVAL_RESULTS.md`, written after Iteration 13 as a process retrospective. The intent is not to relitigate what was measured, but to extract patterns about HOW the experiments were run and what process changes would tighten future iterations.

The structure separates three concerns: what went well, where the work was ad-hoc rather than structured, and what concrete process changes apply to the next iteration onward.

---

## What Worked

**Iterative ADR documentation as the spine**. Each architectural decision produced a numbered ADR. The chain ADR-016 (catalog refactor) through ADR-019 (custom extractor retained) reads as a coherent decision trail, with each ADR referencing predecessors. A future reader can reconstruct the design rationale from the ADRs alone, independent of the longer-form EVAL_RESULTS narrative.

**Diagnostic fields added reactively when needed**. The Args, argument_verified_count, zitat_lengths, and zitate_with_paragraph_count fields were added during Iteration 10 in response to the unexplained zero-extraction cases on gpt-4o (einspruch_14, einspruch_19). The disambiguation between H1, H2, and H3 failure modes only became possible because the diagnostics were operationalised. Reacting to a "we cannot explain this" moment by extending the measurement is the right pattern.

**Negative results documented as first-class outcomes**. The v3 ablation (inline tags) showed clear regression on gpt-4o-mini. Rather than burying the result or attempting to rescue it, Iteration 13 records the failure with diagnostic detail and explicit architectural rejection. Negative results carry the same evidentiary weight as positive ones when reasoning about design alternatives.

**Per-run JSON persistence with toggle encoding in filenames**. Each experimental run produces a JSON artifact whose filename encodes the active configuration (model identifier, experiment variant, timestamp). The toggles dict is also at the JSON top level. Reconstruction of "which parameters produced this result" is possible from the artifact alone, without external context.

**Multi-provider exposure revealed a latent production bug**. The EinwendungsTyp enum casing mismatch (lowercase enum values versus uppercase prompt convention) had existed since Iteration 6 but was masked by OpenAI's lenient parse() coercion. The Mistral run surfaced it via strict Pydantic validation. Provider diversity in evaluation acts as a free integration test against schema looseness. This kind of finding only emerges when the experiment forces a contract that the production code hadn't been challenged on.

---

## Where the Work Was Ad-Hoc

**No pre-registration of hypotheses or stop criteria**. Hypotheses like "Hybrid Pattern lifts low-tier models by 10-15 percentage points" were formulated during analysis, not before measurement. The H1, H2, H3 failure-mode taxonomy was defined retrospectively to explain the zero-extraction cases. This pattern is classical HARKing (Hypothesizing After Results are Known) and inflates apparent effect sizes because the hypothesis is implicitly tuned to the data. A pre-registered hypothesis would have stated "Hybrid Pattern lifts recall by X-Y pp on tier-Z models because of failure-mode-W resolution", and the measurement would then test the prediction rather than generate it.

**Single-run-per-configuration sampling**. Every model and variant combination ran exactly once. For gpt-4o-mini and gpt-4o at temperature=0 the results may be approximately deterministic, but the o-series and gpt-5 family reject custom temperature and run at the default (1.0). Single-run point estimates for non-deterministic configurations carry no variance bounds. The 1.0 pp difference between Mistral 2512 baseline (86.4%) and hybrid (85.4%) is reported as "essentially neutral", but without repeat runs we cannot distinguish it from a true small negative effect.

**Scope creep on the v3 experiment**. v3 was scoped as "extend the v2 Hybrid Pattern with tagged context". During implementation it expanded to three independent layers (header hint, inline tags, few-shot examples) without a pre-defined ablation matrix. The Few-Shot examples were authored using the same inline-tag format as the body augmentation, creating an inadvertent coupling that prevented isolated testing of Few-Shot effects after the Tagged Context approach failed. Pre-defining the ablation matrix and the layer-independence invariants would have caught this design flaw before implementation.

**MODEL_NAME mislabeling caught only by output comparison**. During Iteration 10, a run was labelled "gpt-4o" but had actually been executed against gpt-4o-mini due to an unchanged MODEL_NAME constant. Detection happened post-hoc by comparing numbers to the previous gpt-4o-mini run. A pre-flight sanity check (echo MODEL_NAME prominently in stdout and in JSON) plus a result-file naming convention that makes accidental overwrites visible would have caught this in seconds.

**No stop rule, leading to incremental model and variant accretion**. Iteration 10 started with three models. Iteration 11 added gpt-5.5 and the Hybrid variant. Iteration 13 added Mistral 2411 and 2512 plus three v3 ablation variants. None of these expansions had a pre-stated "Iteration X is complete when we have measured Y, Z" boundary. Each addition felt justifiable in isolation but the cumulative session length grew without explicit budget. Iteration completion criteria defined upfront prevent this drift.

**Reactive provider adaptation**. The EinwendungsTyp casing bug was discovered after a full Mistral baseline run failed. A pre-flight provider-compatibility probe (one sample document through each new provider with the production schema) would have surfaced the issue in seconds rather than after waiting for the rate-limited run to error out on every document. This is a cheap check that should be done before any new provider enters the evaluation matrix.

**Manual aggregation of comparison tables**. All cross-model and cross-variant comparison tables in the EVAL_RESULTS document and in conversation were assembled by hand, line by line, by reading multiple JSON files. The result-file structure supports automated aggregation but no such script existed during the session. Manual table-building is error-prone (typos in transferred numbers, miscoloured cells, missing rows) and scales badly past four or five configurations.

---

## Tooling Gaps Identified

**No git-SHA or script-version in result files**. If the eval script is modified between runs, old result files cannot be tied to the exact code that produced them. Three lines of subprocess code would persist the current git commit short-sha into every JSON output. Retroactively unrecoverable for past runs, but trivial to add going forward.

**No central experiment index**. PREPROCESSING_EVAL_RESULTS.md is a narrative document, not a queryable index. "Show me all runs of model X under variant Y" requires grep or manual file inspection. A simple results manifest (CSV or markdown table) listing every run with model, variant, timestamp, headline metric, and file path would close this gap.

**No automated aggregation script**. As noted above, the JSON files are structured to support aggregation but the aggregation script does not exist. A `aggregate_results.py` reading results/ and producing the comparison matrix plus a results.csv would convert manual table-building into a one-command operation.

**No provider-compatibility smoketest**. Before running a full ten-document eval against a new provider, no checklist verifies that the schema, casing, and structured-output mode work. The first run is implicitly the smoketest, which means a full eval cycle is wasted on the first iteration with a new provider.

**No iteration-plan template**. Each iteration started with intent communicated in conversation and ad-hoc decisions about scope. A reusable plan template (hypothesis, metrics, configurations to test, stop rule, predicted outcomes) would force the upfront thinking that ad-hoc starts skip.

---

## Process Changes for the Next Iteration

### Pre-Experiment Phase

**Iteration plan as a committed artifact**. Before any code change or run, an `iteration_NN_plan.md` is written and committed. It states the hypothesis being tested, the configurations that will be measured, the predicted outcomes per configuration, the stop rule, and the success/failure criteria. The plan should be detailed enough that a different person could execute the iteration from the document alone.

**Ablation matrix explicit before code**. For any experiment with more than two configurations, an N-by-M table is drawn up first. Rows are configurations, columns are independent variables, cells are predicted outcomes. The matrix forces the question "is each layer actually independent" before implementation begins, which catches the kind of layer-coupling bug that occurred in v3.

**Architecture review of any wrapper or adapter**. Before writing code that sits between the eval loop and a production component, a one-paragraph note answers: what does this wrapper control, what stays unchanged, what assumptions does it make about the production component. The HybridV3Wrapper hardcoding tags into Few-Shot examples would have been caught at this stage.

### During-Experiment Phase

**N=3 minimum per configuration when temperature is non-zero**. For models that reject custom temperature (o-series, gpt-5 family) or providers where temperature is not strictly deterministic, three independent runs per configuration produce a mean and a range. The variance bound is then reportable. For the project's typical cost levels this adds at most a few dollars per iteration.

**Provider-compatibility smoketest before full eval**. Before any new provider enters the evaluation matrix, a single-document run with the full production schema validates: enum handling, optional fields, nested model parsing, structured-output mode, rate-limit pacing. Sixty seconds of probe time prevents an hour of error-output decoding.

**Stop rule enforced after each iteration**. The pre-registered iteration plan states when the iteration is done. When the criteria are met, the iteration is closed and additional ideas go into a backlog for the next iteration plan. This prevents the gradual accretion of "one more thing" that turns a focused iteration into a scope-creep marathon.

### Post-Experiment Phase

**Aggregation script as a standard iteration artifact**. The iteration is not closed until the aggregation script can produce a refreshed comparison matrix from the current results/ contents. The script becomes part of the experiment infrastructure, not optional tooling. New iterations append rows to its output rather than reconstructing the table by hand.

**Effect-size reporting with variance bounds**. Headline metrics are reported as "metric +/- range over N runs" instead of single-point estimates. For deterministic configurations N=1 with a "deterministic" note is fine. For non-deterministic configurations N=3 produces enough signal to distinguish noise from effect.

**Iteration closing checklist**. Before declaring an iteration done: pre-registered predictions are compared to measurements with explicit "predicted X, measured Y" notation; all result artifacts (JSONs, ADR, EVAL_RESULTS update, aggregation output) are committed; the stop rule criteria are satisfied; open questions are deferred to a next-iteration backlog file rather than left as implicit todo.

### Infrastructure

**Git-SHA in every output**. Three lines in the persistence helper that record the current commit short-sha into the JSON top level. Cost: trivial. Value: future reproducibility insurance.

**Pre-registered hypotheses visible in closing documents**. The iteration's closing report in EVAL_RESULTS includes a "predicted vs measured" section that confronts the pre-registration with the data. This is the single strongest defence against storytelling-bias during analysis.

**Negative results are first-class outcomes**. A negative result that contradicts a pre-registered hypothesis is just as informative as a positive one. The standard expectation is that the iteration report documents both, with equal rigor. The v3 ablation handling in Iteration 13 is the template.

---

## Concrete Changes to Apply Starting Iteration 14

The full process changes above are aspirational. The minimum viable subset to introduce in the next iteration is:

1. **A pre-registration file `iteration_14_plan.md`** committed before any code or run. Hypotheses, configurations, stop rule, predictions.

2. **A provider-compatibility smoketest** if any new provider is added (single-doc run against full schema).

3. **A simple aggregation script** that reads results/ and outputs a markdown comparison table. Even 40 lines is enough to start.

4. **Git-SHA persistence** in result JSON output via three lines in the save_results helper.

The remaining items (N=3 runs, ablation matrix as artifact, closing checklist, full effect-size variance reporting) get adopted iteration by iteration based on where they would have most improved the previous one.

---

## Reflection on the Value of This Reflection

Process retrospectives carry their own meta-risk: they can become elaborate post-hoc justifications that retroactively label every decision as deliberate. This document attempts to avoid that by being concrete about specific moments where the work was less rigorous than the published narrative implies. The MODEL_NAME mislabeling, the v3 layer-coupling bug, the EinwendungsTyp surfacing only after a failed Mistral run, the manual table-building: these are recorded as actual gaps, not as features.

The intent for the project is that Iteration 14 starts from this document, not from blank ad-hoc start. The intent for portfolio positioning is that this reflection itself demonstrates engineering maturity: the ability to evaluate one's own process critically and propose concrete improvements is a more durable competence than producing a single clean experiment matrix.