# ADR-017: Norm Assignment Loss in Option Y Position-Based Assignment

## Status

Accepted. Documented as a measured architectural cost. The assignment strategy is retained for the current iteration. Reevaluation is deferred until Phase B retrieval recall eval (separate ADR) quantifies the downstream impact.

## Context

ADR-006 established Option Y as the strategy for assigning extracted norms to extracted arguments in `TriageService._build_extrahiertes_argument`. Under Option Y, the deterministic norm_extractor runs once on the full clean_text, producing a list of ExtractedNorm instances each with a position offset. Norms are then assigned to arguments by checking whether their position falls within the `[start, end)` range of the argument's `original_zitat` substring in clean_text.

The LLM is instructed to produce concise `original_zitat` strings (typically one to three sentences) per argument. The assignment is therefore tight: only norms physically located inside the cited substring are attached to that argument.

To measure how well this works against ground truth, the Phase A norm coverage evaluation was added in `experiments/extraction_evaluation/script/norm_coverage_eval.py`. The eval compares two recall metrics per document:

- **recall_assigned**: aggregated `zitierte_normen` across all `ExtrahiertesArgument` instances produced by the production pipeline.
- **recall_doc_level**: set of all canonical norms that `extract_norms` finds when run directly on the same clean_text, without argument-position filtering.

Both metrics are compared against the union of `must_retrieve.citation` entries from the GT files (verbatim_in_text citations only; should_retrieve inferred-applicable entries are excluded since norm_extractor is text-based and cannot find non-present norms).

## Measurement

First evaluation run with gpt-4o-mini at temperature 0, 9 documents (7 TYP_2 plus 2 Mixed; 1 Mixed had no expected norms, excluded from averaging):

| Metric                        | TYP_2  | Mixed | Overall |
|------------------------------|--------|-------|---------|
| Doc-level Recall (extractor) | 100.0% | 83.3% | 95.8%   |
| Assignment-based Recall      | 59.2%  | 70.8% | 62.1%   |
| Assignment Gap               | +40.8% | +12.5%| +33.7%  |

Per-citation diagnostics across the dataset:

- 13 unique citations LOST_BY_ASSIGNMENT (extractor found them in clean_text but they fell outside any original_zitat range)
- 1 unique citation TRULY_MISSING (`§ 9 Abs. 1 Nr. 1 WHG` in einspruch_12_mixed; suspected regex pattern gap in norm_extractor for the `Abs. X Nr. Y` form)

The signal is unambiguous: at the document level, the deterministic extractor captures essentially all expected citations. The Position-based assignment then loses approximately a third of them.

## Decision

Retain Option Y for the current iteration. Document the measured assignment loss (~34% recall gap) as a known architectural cost. Do not change the assignment strategy until Phase B (Retrieval Recall) eval is implemented and run.

The reasoning is that the production-relevant impact of the loss is unknown without measuring whether the retriever still surfaces the correct legal chunks via semantic similarity on `argument_text`. If retrieval is robust without precise norm hints, the loss is mostly an audit-trail concern rather than a functional defect. If retrieval depends on `zitierte_normen` as input or cross-check signal, the cost is direct and high.

## Rationale

The Phase A eval was specifically designed to disambiguate "norm_extractor problem" from "assignment problem". The result is a clean disambiguation: 13 LOST_BY_ASSIGNMENT versus 1 TRULY_MISSING is a 13:1 ratio. There is no meaningful uncertainty about where the cost lies.

Changing the assignment strategy now would be a structural change without empirical justification of the downstream impact. The alternatives (semantic matching, document-level union, longer zitate) each have their own costs and trade-offs. Choosing among them requires more data, which Phase B will provide.

## Rejected Alternatives

**Alternative 1: Immediate refactor to document-level union.** Each ExtrahiertesArgument gets the full document's extracted norms in `zitierte_normen`. Rejected because per-argument retrieval routing in ResponseDrafting depends on per-argument norm granularity; uniformly attaching all norms collapses that distinction and breaks the "Argument is the unit of legal reasoning" principle from ADR-013.

**Alternative 2: Replace position-based assignment with semantic matching.** Use embeddings or an LLM judge to assign each norm to the most semantically related argument. Rejected for this iteration because (a) it introduces a second LLM call into the Triage pipeline, increasing latency and cost; (b) the semantic assignment quality is itself unmeasured; (c) the simpler fix (longer original_zitat) was not yet tried.

**Alternative 3: Increase the LLM prompt's preferred original_zitat length.** Instruct the LLM to produce longer, paragraph-spanning zitate that capture all relevant citations. Rejected for this iteration because (a) it conflicts with the "max two sentences" constraint in v3.0.0 of `ARGUMENT_EXTRACTION_PROMPT`; (b) longer zitate make the substring-match verification (ADR-006 Layer 1) more brittle; (c) the same effect can be achieved more cleanly by changing the assignment algorithm rather than the prompt contract.

**Alternative 4: Carry document_level_norms as a separate field on TriageResult.** Per-argument `zitierte_normen` stays as is, but TriageResult gains an additional `document_level_norms: list[str]` field that contains the full extract_norms output. This preserves both granularities. Deferred rather than rejected: this is the most likely candidate if Phase B shows that audit-trail completeness matters and per-argument hints suffice for retrieval. Would be a separate ADR if adopted.

## Consequences

- `zitierte_normen` on `ExtrahiertesArgument` is a known incomplete view of cited norms in the document. Code reviewers and downstream consumers should understand this is by design, not bug.
- The audit trail at the argument level shows a reduced norm set. For audit purposes that need the full citation list per document, a separate accessor (currently `extract_norms(clean_text)`) is required.
- Layer-2 retrieval impact is unmeasured. Phase B eval will determine whether assigned norms are critical input, optional hint, or unused.
- The `~34% recall gap` is a measured baseline. Any future change to the assignment strategy can be benchmarked against this number to demonstrate concrete improvement.
- One TRULY_MISSING regex case in norm_extractor (`§ 9 Abs. 1 Nr. 1 WHG`) is a separate small fix tracked outside this ADR.

## Related ADRs

- ADR-006: established Option Y as the assignment strategy. This ADR adds empirical context but does not supersede ADR-006.
- ADR-013: established per-argument processing as the pipeline contract. The assignment loss is downstream of this contract.
- Phase B Retrieval Recall ADR (future): will determine whether the assignment loss has functional consequences or is audit-only.