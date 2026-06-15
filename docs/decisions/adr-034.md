# ADR-034: Cross-Cutting Layers and an Import-Linter Fitness Function

## Status

Accepted.

## Context

The architecture is five sequential bounded contexts (DocumentIngestion, Triage, Retrieval, Briefing, AuditLog) with the rules: no context imports another, and `core/` holds only cross-context contracts. Two whole-system architecture reviews found the rules held in practice (no cycle, no layer violation, no leaky boundary) but raised two gaps.

First, the documented model was incomplete. CLAUDE.md said "no context imports another; only pipeline.py imports multiple contexts; core only for contracts." The real graph has more structure than that sentence admits: every context depends on `observability/` for tracing, logging, and metrics, and the CLI depends on `services/` for the LLM client. These are not bounded contexts and not violations; they are cross-cutting layers. But because they were never declared as such, a reader comparing the sentence to the graph sees a contradiction, and a future author has no stated rule for which way a dependency between a context and observability may point.

Second, the one structural guarantee the system most wants, that the cross-cutting layer never reaches back into a context (which would turn a middleware layer into a hub coupled to every context's internals), rested on discipline alone. A review noted the absence of a fitness function: nothing failed the build if observability imported a context; a reviewer had to catch it by eye. The same is true of the no-context-imports-another rule and the core-holds-no-context rule.

## Decision

Declare `observability/` and `services/` as cross-cutting layers, not bounded contexts, with a fixed one-way dependency direction: a bounded context may depend on a cross-cutting layer as middleware, and a cross-cutting layer imports no bounded context. The vocabulary union in `observability_registry.py` is a composition root (like pipeline.py and __main__.py), not part of the observability layer, and may import each context's declared events to assemble the root union.

Enforce the structural rules with import-linter contracts declared in `pyproject.toml` under `[tool.importlinter]`, pinned exact, run in the test gate:

1. Observability imports no bounded context (forbidden contract).
2. The five bounded contexts are mutually independent (independence contract).
3. Core imports no bounded context (forbidden contract).

The contracts run inside `pytest -q` via `tests/small_scale/test_import_contracts.py`, so a structural violation fails the same gate that proves behavior. A second test runs a deliberately-broken contract (the Coordinator forbidden from importing Triage, which it does) and asserts it is reported broken, so the gate cannot pass vacuously through a mistyped module name.

## Rationale

This is "enforce, don't document" applied to a structural invariant. A rule a reviewer must catch by eye is one merge away from erosion; a rule the build checks is mechanism. The cross-cutting-layer concept is the honest name for what observability and services already are, and naming it gives the otherwise-unstated dependency direction a place to live and a check to hold it.

Running the contracts in the test gate rather than only in pre-commit ties them to the proof the rest of this round rests on (the unchanged green suite) and keeps them cross-platform robust, with no dependency on a hook environment. The negative-control test addresses the failure mode the reviews implicitly warned about: a contract whose source or forbidden modules are mistyped matches nothing and passes silently, which reads as "covered" while guarding nothing.

The direction is the same single-ownership principle that governs the rest of the architecture: knowledge and dependencies flow one way. A middleware layer that imported a context would become a hub on which every context's change ripples, the god-module a review indicts.

## Consequences

Positive:

- The documented architecture matches the real graph: cross-cutting layers are named, and the dependency direction is explicit.
- The three structural rules are mechanical. Observability reaching into a context, one context importing another, or a bounded-context type leaking into core, each fails `pytest -q` with a located report.
- The check is fast (static graph analysis, no model load) and runs in the existing gate.

Negative:

- One more dev dependency (import-linter, pinned exact) and one more config block in pyproject.
- The contracts encode module names; renaming a context means updating the contracts. This is the intended coupling: the rule should track the structure it guards.

## Alternatives Considered

A. Document the layers in CLAUDE.md and leave enforcement to review. Rejected: this is exactly the discipline-not-mechanism state the reviews flagged. Prose drifts from code; that drift is what this round closes elsewhere.

B. Enforce via a pre-commit hook only. Rejected as the sole mechanism: a hook runs in its own environment and can be skipped, and it would not be part of the green-suite proof this round uses. The test gate is the more robust home; a hook could be added later as an additional fast-feedback layer without moving the source of truth.

C. A golden test pinning the dependency graph. Rejected: it treats the symptom (graph changed) not the cause (a rule was broken), and it is noisy, every legitimate new import edits the golden file. A contract states the rule directly.

## References

- ADR-020: Retrieval as a separate bounded context (the bounded-context model these contracts guard).
- ADR-026: observability and the structured-log/event vocabulary (the cross-cutting layer this declares).
- Round 17.1 and Round 20 (this round): single-context types moved out of core, so the "core imports no bounded context" contract holds.
- architecture-foundations: bounded-context isolation, the central-registry coupling-hub rule, the dependency direction.
- `pyproject.toml` [tool.importlinter] and `tests/small_scale/test_import_contracts.py`: the contracts and their gate.
