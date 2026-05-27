# ADR-019: Retain Custom Norm Extractor over OpenLegalData Library

## Status

Accepted. Empirically validated via head-to-head benchmark on the Phase A evaluation corpus.

## Context

The project maintains a custom `norm_extractor` (jura_regex-based, 9-Gesetz whitelist, i.V.m. chain handling, canonical-form rendering). The OpenLegalData ecosystem provides `legal-reference-extraction` (PyPI; imports as `refex`), used in production at de.openlegaldata.io and actively maintained.

The maintenance cost of the custom extractor is real: 49 unit tests, pattern updates whenever the regex needs extending, and ownership of edge cases. Adopting an established library would shift that maintenance to the community.

A direct benchmark was needed to decide whether to retain the custom extractor or migrate to the external library.

## Decision

Retain the custom `norm_extractor` as the production extraction component. Document `refex` as a known alternative but do not adopt it.

## Rationale

Head-to-head benchmark on 10 documents (7 TYP_2, 3 Mixed, plus 10 TYP_1 for false-positive resilience), comparing both extractors against the Phase A ground truth:

| Metric | Custom | OLD-Regex (refex 0.5.2) |
|---|---|---|
| Phase A Recall | 100.0% | 95.8% |
| Phase A Precision | 90.8% | 90.8% |
| Phase A F1 | 94.6% | 92.1% |
| TYP_1 False Positives | 0 / 10 | 0 / 10 |
| Mean extraction time | 0.2 ms | 40.2 ms |
| Median extraction time | 0.2 ms | 0.9 ms |

Three concrete advantages:

1. **i.V.m. chain handling**. On einspruch_12_mixed, only the Custom extractor recovers `§ 9 Abs. 1 Nr. 1 WHG` from `§ 9 Abs. 1 Nr. 1 i.V.m. § 8 WHG`. `refex` logs `Marker could not be assign to book` and drops the inner citation. This is exactly the Iteration 9 improvement, empirically validated.

2. **Performance**. Roughly 200x faster on the mean. The gap grows on complex documents: einspruch_14 took refex 260 ms versus 0.8 ms for Custom. For batch processing in production this is a measurable cost difference.

3. **Identical precision and FP resilience**. No quality trade-off in the other direction. Custom does not over-extract relative to the external library.

The OpenLegalData broader-domain coverage (BGB, StGB, EU law, case law) is real but irrelevant to this project's 9-Gesetz scope. The project context constrains to BauGB, BauNVO, BImSchG, BNatSchG, EnWG, VwGO, WaStrG, WHG, WPG; whatever lies outside is not in the corpus and not in the ground truth.

## Consequences

Positive:

- Recall ceiling stays at 100% on Phase A documents, supporting the Hybrid Pattern (ADR-018) which depends on reliable upstream extraction.
- Production batch throughput is not constrained by extractor latency.
- The i.V.m. handling investment from Iteration 9 is justified by empirical advantage.

Negative:

- Maintenance burden remains in-house. Future Gesetz additions, pattern edge cases, and regression handling are project responsibility.
- The custom code path needs to be re-validated against `refex` periodically (annually at minimum). If `refex` adds i.V.m. handling and the performance gap narrows, this decision is reconsidered.
- The benchmark is small-N (10 documents). The conclusion is robust on observed data but not statistically saturated. Validation on real authority documents in Phase 2 is still required.

## Alternatives Considered

A. Adopt OpenLegalData Regex (`refex` 0.5.2). Rejected because of the i.V.m. recall gap, the 200x performance gap, and the lack of a meaningful coverage advantage within the project scope.

B. Adopt OpenLegalData Transformer (EuroBERT-210m fine-tune at `openlegaldata/legal-reference-extraction-base-de`). Deferred. The model is a gated HuggingFace repository; access approval is pending. Once obtained, a follow-up benchmark will measure whether the Transformer brings meaningful improvement on the documented extractor_limitation cases (FFH-Richtlinie, TA Lärm, DIN standards, Landesverordnungen). The CC BY-NC 4.0 license restricts production deployment in a commercial Behörden context regardless of measured performance, so the Transformer is more relevant as a research data point than as a production candidate.

C. Hybrid: use Custom as primary, fall back to OLD-Regex when Custom finds nothing. Rejected because Custom dominates on every measured axis; a fallback would only run when Custom already failed, and OLD-Regex shares the same blind spots (no coverage of administrative rules, EU law, or Landesverordnungen).

## References

- ADR-006: Argument verification (Layer 1)
- ADR-017: Option Y position-based assignment, model-dependent loss
- ADR-018: Hybrid norm assignment pattern (depends on reliable upstream extraction)
- `experiments/extraction_evaluation/script/norm_extractor_benchmark.py` for the experimental implementation
- `refex` on PyPI: https://pypi.org/project/legal-reference-extraction/ (package name) imported as `refex.orchestrator`
- Repository: https://github.com/openlegaldata/legal-reference-extraction