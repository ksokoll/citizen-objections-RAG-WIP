# ADR-020: Retrieval as a Separate Bounded Context

## Status

Accepted.

## Context

The Triage bounded context extracts arguments from citizen objections and assigns canonical norm citations to each argument via the deterministic norm_extractor and the Option Y positional assignment (ADR-013, ADR-017). Each ExtrahiertesArgument carries zitierte_normen as canonical citation strings such as "§ 9 Abs. 1 Nr. 1 WHG".

These citations are identifiers, not content. The downstream consuming context (then called ResponseDrafting, later renamed Briefing per ADR-022) needs the actual Gesetzestext behind each citation. At the time of this decision that context drafted responses grounded against the source law; under ADR-022 it instead assembles a deterministic briefing that pairs each argument with its resolved norm text.

The original design sketch placed this norm-to-text resolution inside the Triage context, on the reasoning that fetching the law text is a natural continuation of norm extraction. Before implementing the resolution step, this assumption was revisited.

## Decision

Norm resolution is implemented as a separate bounded context, `retrieval`, not as an extension of Triage.

The pipeline order becomes: DocumentIngestion produces cleaned text, Triage produces arguments with assigned canonical citations, Retrieval resolves each citation to its source Gesetzestext, and the consuming context (Briefing, then ResponseDrafting) consumes the fully-contextualised arguments.

Retrieval follows the standard layering: a domain layer with the GesetzParagraph entity and the Retriever Protocol, an application layer with the resolution service, and an infrastructure layer with the XML loader, the embedder, and the vector index. Cross-context communication uses DTOs passed through the application-layer orchestrator; no direct imports between Triage and Retrieval.

## Rationale

Separation of concerns. Triage answers "what does the citizen argue and which norms are cited". Retrieval answers "what does the cited law actually say". These are distinct responsibilities with distinct reasons to change: Triage changes when argument-extraction or classification logic changes, Retrieval changes when the statute corpus, the retrieval strategy, or the embedding model changes. Binding them into one context would couple two independent change axes.

Testability. With resolution in a separate context, Triage unit tests mock the Retriever Protocol and need no vector index, no embedding model, and no statute XML. Conversely, Retrieval tests exercise the retrieval logic without running the LLM-backed Triage pipeline. Keeping the two apart means each context's tests stay fast and hermetic, satisfying the "unit tests run with zero network access" rule.

Replaceability. The retrieval strategy can be swapped, and any future resolution component can be replaced, all without touching Triage. The Protocol boundary makes Retrieval a replaceable component. At the time of this ADR the strategy was a hybrid exact-match plus vector fallback; ADR-021 later reduced production resolution to exact-match only and retained the vector code as experimental reference.

These three align with the architecture decision hierarchy: the separation reduces coupling (risk), is cheaply reversible (the contexts can be merged later if the boundary proves wrong), keeps each context simpler, and improves both replaceability and testability.

## Consequences

Positive:

- Triage stays focused on extraction and classification. Its test suite does not acquire a dependency on the retrieval infrastructure.
- The retrieval strategy is isolated behind a Protocol and can evolve independently, including the future Variant-B style hardening or a switch of embedding model.
- The statute corpus (nine local XML files representing the current Behörde state) is owned by a single context, with one loader and one index.

Negative:

- An additional bounded-context boundary exists, with the attendant DTO mapping and orchestration wiring. The application-layer orchestrator must now coordinate four contexts rather than three.
- Cross-context communication adds a small amount of ceremony compared to an in-context method call. This is the deliberate cost of the decoupling.

## Alternatives Considered

A. Resolution inside Triage. Rejected. It couples the retrieval infrastructure into Triage's test surface and binds two independent change axes (argument extraction versus statute retrieval) into one context. The apparent cohesion ("norm extraction and norm resolution are related") is weaker than the separation of responsibilities.

B. Resolution inside the consuming context (then ResponseDrafting, now Briefing). Rejected. That context would then carry two responsibilities: fetching the legal source and producing its output artifact. The fetched Gesetzestext is also potentially useful to other contexts (for example an audit or review view), so binding it to the consuming step would limit reuse.

## References

- iteration_14_plan.md: the pre-registered plan for this iteration, with the hybrid-retrieval hypothesis and measurement design
- ADR-013: per-argument processing
- ADR-017: Option Y positional norm assignment
- ADR-018: Hybrid Pattern (Triage-level norm-assignment improvement, distinct from this retrieval step)
- architecture-foundations: Bounded Context isolation, dependency rule, Protocol boundaries