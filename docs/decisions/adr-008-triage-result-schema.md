## ADR-008: EU AI Act Risk Classification

**Status:** Accepted

**Context:** The EU AI Act (Regulation 2024/1689) establishes risk categories for AI systems. Annex III lists high-risk use cases including AI systems used by public authorities in administrative decisions affecting individuals. The system assists Sachbearbeiter in drafting responses to citizen objections in a formal regulatory procedure, which warrants explicit classification.

**Decision:** The system is classified as limited risk under the EU AI Act, not high risk. Full high-risk compliance obligations (conformity assessment, technical documentation per Article 11, registration in the EU database) are not implemented in v1. A documented rationale for this classification is maintained in this ADR.

**Rationale for limited risk classification:** The system does not make or automate administrative decisions. Every Abwägungsstellungnahme is a draft that requires explicit Sachbearbeiter review and approval before any official use. The Sachbearbeiter is the legally responsible decision-making instance throughout. The system performs no profiling of individual citizens. The system does not assess eligibility, entitlements, or rights of individuals. The output is a drafting aid for an internal workflow step, not a decision communicated to citizens. The human-in-the-loop design is not a disclaimer but an architectural constraint enforced at the data model level: `sachbearbeiter_freigabe` is a required step before an Abwägungsstellungnahme can be marked as complete.

**Consequences:** The architectural human-in-the-loop constraint must be maintained in all future versions. Any change that enables the system to communicate directly with citizens or to automatically finalize Abwägungsstellungnahmen without human review would require re-classification and a full high-risk compliance assessment. This boundary is documented here as a design invariant, not a feature preference.