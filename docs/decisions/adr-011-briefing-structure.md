## ADR-011: Naming Convention for Mixed-Language Codebase

**Status:** Accepted

**Context:** The project domain is German public administration. The juristic vocabulary of Beteiligungsverfahren (Einwendung, Abwägung, Rechtsgrundlage, Sachbearbeiter, Würdigung) has established legal meaning in German that no English equivalent captures precisely. Translating "Einwendung" to "objection" loses the specific procedural meaning under BauGB §3. At the same time, Python infrastructure, libraries, and the broader software engineering vocabulary are English. Without a stated rule, naming drifts as the codebase grows and ad-hoc translations introduce inconsistency across files.

**Decision:** The codebase follows a split naming convention.

Domain terms remain in German and are transliterated to ASCII: `Abwaegungsstellungnahme`, `Einwendung`, `Rechtsgrundlage`, `KatalogTreffer`, `WuerdigungsAbsatz`, `Sachbearbeiter`, `Beteiligungsverfahren`. Umlauts and ß are replaced (ä becomes ae, ö becomes oe, ü becomes ue, ß becomes ss). No umlauts appear in identifiers.

Infrastructure and technical terms remain in English: `Protocol`, `Repository`, `Coordinator`, `Service`, `Pipeline`, `Adapter`, plus RAG and ML-specific vocabulary (`chunk_id`, `embedding`, `retrieval_metadata`, `model_version`, `prompt_version`).

Docstrings, code comments, and log messages are written in English. User-facing strings (UI labels, error messages shown to Sachbearbeiter, and the generated text content of an Abwägungsstellungnahme) are written in German.

Enum values follow the convention of the domain field they describe. The `WuerdigungsStatus` enum uses `GENERIERT`, `UNTERDRUECKT_UNVERIFIED`, and `OHNE_TREFFER`. The `EinwendungsTyp` enum uses `TYP_1` and `TYP_2`. Test names follow the domain convention they verify: `test_abwaegungsstellungnahme_wird_bei_unverified_rechtsgrundlage_unterdrueckt`.

**Rationale:** This is Ubiquitous Language as defined by Eric Evans. The code uses the language of the domain experts. In a public administration context, the domain experts are juristically trained Sachbearbeiter whose vocabulary is German and not translatable without semantic drift. Translating "Würdigung" to "appraisal" or "assessment" introduces an interpretive gap that produces real bugs when the translation is applied inconsistently across files. Domain terms in their original language are also defensible in code reviews with non-developer domain experts, who can recognize and validate the model.

Transliteration of umlauts is a tooling concession, not a stylistic one. Identifiers with ä, ö, ü produce inconsistent behavior in IDEs, linters, search tools, terminal environments, and code completion across operating systems. Standard practice in established German enterprise codebases (SAP ABAP, banking domain code, insurance domain code) follows the same transliteration rule for the same reasons.

English for infrastructure and tooling vocabulary aligns with Python conventions and the surrounding library ecosystem (Pydantic, FastAPI, pytest). Hybrids like `RepositoryAbwaegung` or `AbhaengigkeitsInjektor` produce friction without value. English docstrings ensure that external reviewers, AI coding assistants, and OSS-style tooling can work with the code without translation overhead.

User-facing German is not negotiable. The system serves German public authority workflows and produces text content that becomes part of a formal administrative record.

**Rejected Alternatives:** Full English with translated domain terms. Rejected because translations such as `ReasonedAssessment` for `Abwaegungsstellungnahme` or `Objection` for `Einwendung` strip established juristic meaning and create silent semantic drift across the codebase.

Full German including infrastructure terms. Rejected because Python and its library ecosystem are English. Names like `BasisVorlage` or `AbhaengigkeitsInjektor` conflict with established Python conventions and impose a translation burden on every contributor and every external tool.

Preserving umlauts in identifiers. Rejected due to recurring tooling inconsistencies across IDEs, linters, terminal environments, and CI systems.

**Consequences:** The README contains a short glossary mapping key German domain terms to one-line English explanations, so that non-German reviewers can follow the code without external lookups.

Existing identifiers are reviewed for consistency. `CatalogMatch` is renamed to `KatalogTreffer`. The `WuerdigungsStatus` enum value `NO_MATCH` is renamed to `OHNE_TREFFER`. The renames are applied in a single dedicated commit to keep the rename diff separate from logic changes.

Future contributors follow the convention. New domain concepts that emerge during catalog extension or feature development are named in German first, with transliteration applied to umlauts. Code review checks the convention at every Bounded Context boundary: DTOs crossing context boundaries follow the same rule as the entities they originate from.