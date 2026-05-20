## ADR-001: Domain Terminology and Scope Boundary

**Status:** Accepted

**Context:** The system processes mass citizen submissions during formal public participation procedures for construction approval projects. The German term "Bürgerbegehren" (citizen initiative) is a legally defined instrument under municipal law (HGO §8b) requiring signature quorums and a defined petition subject. It does not describe this use case.

**Decision:** The project exclusively uses the following terms: Einwendungen im Beteiligungsverfahren (objections in participation procedures, BauGB §3/§4, VwVfG §73) for incoming documents. Abwägungsstellungnahme (reasoned assessment) for the generated output. Beteiligungsverfahren (participation procedure) for the overarching process context.

**Rationale:** Correct terminology is not a style preference in a public sector context. Mislabeling objections as "Bürgerbegehren" would disqualify the system immediately for any reviewer with public administration background, as the legal obligations and procedural requirements differ fundamentally. The Abwägungsstellungnahme follows a defined juristic text structure (Sachverhalt, Vorgebrachte Einwendung, Rechtliche Würdigung, Abwägungsergebnis) that directly determines the output schema of the system.

**Rejected Alternatives:** Using "Bürgerbegehren" as a simplified label for external communication. Rejected because the incorrectness is not a simplification but a category error with legal implications.

**Consequences:** All domain models, ADRs, docstrings, and README content use the terminology defined above. The Abwägungsstellungnahme structure is the canonical output format and is defined before Bounded Context cuts are finalized.