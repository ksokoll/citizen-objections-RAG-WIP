# ADR-018: Hybrid Norm Assignment via Deterministic Regex Hint

## Status

Proposed. Empirically validated via encapsulated experiment; production refactor pending.

## Context

ADR-017 documented Option Y (position-based norm assignment) as structurally lossy. Empirical measurement across four LLMs showed assignment loss is heavily model-dependent: +44.9 pp gap (gpt-4o) down to +7.6 pp (gpt-5.5).

Two failure modes drive the loss:

- H2 (narrow zitate): the LLM selects `original_zitat` spans that exclude the relevant §-citation. Affects gpt-4o-mini, gpt-4o, o3-mini.
- H3 (paraphrasing): the LLM rewrites the zitat instead of selecting a verbatim substring, violating ADR-006 Layer 1. Affects gpt-4o specifically.

A model-only fix (use gpt-5.5 in production) closes the gap but creates closed-source vendor lock-in. This conflicts with the Behörden-Sovereignty target of running on-premises open-weights models (LLaMA, Mixtral, LeoLM) under DSGVO and BSI-compliant conditions.

## Decision

Adopt the Hybrid Norm Assignment Pattern:

1. The deterministic `norm_extractor` runs over the cleaned text and produces the set of canonical §-citations.
2. The set is prepended to the LLM prompt as a clearly delimited orientation hint. The hint targets the `original_zitat` field, instructing the LLM to widen its zitat span so that any relevant §-citation falls inside it.
3. The hint is framed as orientation help, not as authoritative truth. The list is described as possibly incomplete so the LLM retains its own judgment.
4. Option Y position-based assignment remains the downstream mechanism that populates `zitierte_normen`. The Hybrid Pattern improves Option Y's inputs; it does not replace Option Y.

The hint payload contains canonical citation strings only. No positions, no context snippets. The volltext follows the hint.

## Consequences

Positive:

- Reduces assignment loss across model tiers. Recall improvements: gpt-4o-mini +13.2 pp, o3-mini +12.2 pp, gpt-5.5 +6.6 pp.
- Decouples production deployment from LLM choice within a reasonable performance band. Smaller and self-hostable models become viable production candidates.
- Compatible with the existing LLM schema (`LLMTriageOutput`). No breaking changes downstream.
- The deterministic component remains auditable. Regex output is reproducible and inspectable.
- Variant A soft-hint design preserves the option to escalate to a hard schema constraint (Variant B) later without architectural rework.

Negative:

- Prompt token cost increases marginally. Typically 5 to 15 canonical citation strings per document.
- Paraphrasing-style failure modes (gpt-4o family) are not addressed by this hint formulation. The Hybrid Pattern raises model floors but does not erase model-specific weaknesses.
- The hint is a soft constraint enforced by prompt discipline. A non-cooperative model would fall back to Option Y baseline behavior.
- Doc-level coverage by `norm_extractor` becomes a load-bearing dependency. Gaps in the regex whitelist (currently nine Gesetze) propagate directly into reduced assignment quality.

## Alternatives Considered

A. Variant B (Hard Constraint via Schema). Extend `LLMArgument` with `zitierte_normen` and validate the LLM output against the deterministic list via Pydantic. Rejected for this iteration because it requires production schema changes and ADR-006 Layer 1 implications need separate analysis. Deferred to a future ADR if Variant A proves insufficient in production.

B. Variant C (Two-Stage LLM Call). Stage 1 extracts arguments unchanged, Stage 2 maps arguments to norms via a second LLM call constrained to the regex list. Rejected because the doubled API cost is unjustified given Variant A's empirical results.

C. Model lock-in on gpt-5.5. Rejected because it conflicts with the Behörden-Sovereignty target and creates closed-source vendor dependency.

## Empirical Evidence

Four-model evaluation on 10 documents (7 TYP_2, 3 Mixed). Baseline uses pure Option Y; Hybrid uses the wrapper described in this ADR.

| Model | Baseline Recall(A) | Hybrid Recall(A) | Delta |
|---|---|---|---|
| gpt-4o-mini | 64.0% | 77.2% | +13.2 pp |
| gpt-4o | 63.8% | 64.6% | +0.8 pp |
| o3-mini | 77.3% | 89.5% | +12.2 pp |
| gpt-5.5 | 92.4% | 99.0% | +6.6 pp |

Doc-level recall remained at 100% across all configurations, confirming `norm_extractor` as a reliable upstream component independent of LLM choice.

## References

- ADR-006: Argument verification (Layer 1)
- ADR-017: Option Y position-based assignment, model-dependent loss
- `experiments/extraction_evaluation/script/norm_coverage_eval_hybrid.py` for the experimental implementation
- Literature: Bachinger et al. (2024) "GerPS-Compare"; LAMUS hybrid approach recommendation; openlegaldata/legal-reference-extraction for the German legal Regex baseline.