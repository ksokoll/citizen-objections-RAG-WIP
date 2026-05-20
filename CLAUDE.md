# CLAUDE.md — Einwendungen Processing System

## Purpose of This File

This file is the authoritative context document for the project. Read it fully before writing any code or proposing any architecture change. All ADR decisions documented here are final unless a new ADR explicitly supersedes them. Do not re-decide what is already decided.

---

## Project Overview

This system processes mass citizen objections (Einwendungen) submitted during formal public participation procedures (Beteiligungsverfahren) for construction approval projects such as wind turbines and data centers. Public authorities are overwhelmed by large volumes of objections, including coordinated campaigns with near-identical arguments. The system assists Sachbearbeiter (administrative clerks) by classifying incoming objections, matching them to a predefined catalog of known argument types, and generating a structured draft Abwägungsstellungnahme for human review and approval.

The Sachbearbeiter is the legally responsible decision-making instance at all times. The system is a drafting aid, not a decision-making system. This distinction is architecturally enforced, not just stated in documentation.

---

## Non-Negotiable Terminology

These terms are not interchangeable. Using the wrong term in code, docstrings, or documentation is a domain error.

| Correct Term | Wrong Term | Why |
|---|---|---|
| Einwendung / Einwendungen | Bürgerbegehren | Bürgerbegehren is a legally distinct formal instrument (HGO §8b) with signature quorums. It does not describe this use case. |
| Beteiligungsverfahren | Bürgerbeteiligung | The formal procedure under BauGB §3/§4 and VwVfG §73 |
| Abwägungsstellungnahme | Antwort, Response | A juristic text type with a defined structure. Not a reply to the citizen. |
| Sachbearbeiter | User, Admin, Reviewer | The administrative clerk who reviews and approves drafts |

---

## Architecture

### Four Bounded Contexts

**DocumentIngestion:** Receives raw documents (plain text in skeleton, PDF in later branches). Applies PII masking as the final step before any data passes downstream. Stores the raw document in an access-controlled document store separate from the processing pipeline. Hands off clean, masked text.

**Triage:** Extracts structured arguments from the masked text via LLM. Matches extracted arguments against the predefined catalog using embedding similarity with LLM fallback (see ADR-002b). Classifies the objection as TYP_1 (informal, no legal expertise) or TYP_2 (legally sophisticated, coordinated campaign). Emits a NoMatchEvent when no catalog entry exceeds the confidence threshold.

**ResponseDrafting:** Performs domain-routed retrieval against the Bundesrecht corpus using hybrid retrieval (BM25 + FAISS with RRF). Generates a structured Abwägungsstellungnahme draft. Runs post-hoc verification of all §-references against retrieved chunk IDs. Applies Hard Failure routing if any Rechtsgrundlage is unverified.

**AuditLog:** Append-only event store. Receives typed domain events from all other contexts. Is never modified after write. Supports query by einwendungs_id, time range, and WuerdigungsStatus. Has no dependency on other bounded contexts. All other contexts depend on AuditLog unidirectionally.

### Coordinator Pattern

`pipeline.py` is the single orchestration entry point. It calls each BC in sequence, performs Verification checks in pure Python between steps, and routes to AuditLog after each step. No BC calls another BC directly. This is the same Coordinator pattern used in the Invoice Agent.

### Directory Layout

```
src/
  einwendungen/
    core/
      models.py          # All domain types (see Core Data Model section)
      protocols.py       # LLMClient, Retriever, Embedder as Protocols
      events.py          # Typed domain events for AuditLog
    document_ingestion/
      service.py
    triage/
      catalog.py         # CatalogEntry, CatalogRepository
      service.py
    response_drafting/
      service.py
      prompts/
        abwaegung_v1.py  # Versioned prompt template
    audit_log/
      store.py           # Append-only event store
      service.py
    pipeline.py          # Coordinator
tests/
  test_smoke.py
pyproject.toml
```

---

## Core Data Model

`core/models.py` contains the canonical domain types. This file is written once and treated as the architecture invariant shared by all BCs. Do not place domain types in BC-local files.

### Abwägungsstellungnahme — State Machine Pattern

`sachbearbeiter_freigabe` is NOT an `Optional` field. The ADR-008 argument for EU AI Act limited-risk classification rests on the claim that human-in-the-loop is architecturally enforced. This requires a state machine, not an optional field.

```python
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class WuerdigungsStatus(str, Enum):
    GENERIERT = "generiert"
    UNTERDRUECKT_UNVERIFIED = "unterdrueckt_unverified"
    NO_MATCH = "no_match"


class AbwaegungsStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"


class EinwendungsTyp(str, Enum):
    TYP_1 = "typ_1"
    TYP_2 = "typ_2"


class Rechtsgrundlage(BaseModel):
    paragraph: str          # e.g. "§3 Abs. 1 BauGB"
    gesetz: str
    chunk_id: str           # reference back to source chunk
    verified: bool          # matched against source corpus


class CatalogMatch(BaseModel):
    catalog_id: str
    beschreibung: str
    konfidenz_score: float
    match_stage: Literal["embedding", "llm_fallback"]  # ADR-002b: stage that produced the match


class RetrievalMetadata(BaseModel):
    chunk_ids: list[str]
    scores: list[float]
    domain_route: str
    domain_confidence: float
    fallback_used: bool


class Freigabe(BaseModel):
    sachbearbeiter_id: str
    timestamp: datetime
    kommentar: Optional[str] = None


class Abwaegungsstellungnahme(BaseModel):
    einwendungs_id: str
    cluster_id: str
    einwendungs_typ: EinwendungsTyp
    catalog_match: Optional[CatalogMatch]   # None triggers NoMatchEvent
    sachverhalt: str
    vorgebrachte_einwendung: str
    wuerdigungs_status: WuerdigungsStatus
    rechtliche_wuerdigung: Optional[str]    # None when status is not GENERIERT
    rechtsgrundlagen: list[Rechtsgrundlage]
    abwaegungsergebnis: Optional[str]       # None when wuerdigung is suppressed
    retrieval_metadata: RetrievalMetadata

    # Reproducibility fields — mandatory for audit
    model_version: str
    prompt_version: str
    retrieval_config_hash: str

    # State machine
    status: AbwaegungsStatus = AbwaegungsStatus.DRAFT
    freigabe: Optional[Freigabe] = None     # Set only via apply_freigabe()

    def apply_freigabe(self, freigabe: Freigabe) -> Abwaegungsstellungnahme:
        """Transition from DRAFT to APPROVED. Only valid transition.

        Args:
            freigabe: The Sachbearbeiter approval record.

        Returns:
            New instance with status APPROVED and freigabe set.

        Raises:
            ValueError: If status is not DRAFT.
        """
        if self.status != AbwaegungsStatus.DRAFT:
            raise ValueError(
                f"apply_freigabe requires status DRAFT, got {self.status}"
            )
        return self.model_copy(
            update={"status": AbwaegungsStatus.APPROVED, "freigabe": freigabe}
        )
```

Only `APPROVED` instances are valid final outputs. The pipeline must enforce this before writing to AuditLog as a completed record.

---

## Architecture Decision Records

### ADR-001: Domain Terminology and Scope Boundary

**Status:** Accepted

**Context:** German administrative law defines each procedure type precisely. Using incorrect terminology in system documentation or code creates a mismatch between what the system claims to do and what it legally does. Einwendungen im Beteiligungsverfahren under BauGB §3/§4 and VwVfG §73 are a specific legal instrument with defined rights and obligations for submitters. Calling them by another name implies a different legal instrument with different legal consequences.

**Decision:** The project uses exclusively: Einwendungen im Beteiligungsverfahren (BauGB §3/§4, VwVfG §73) for incoming documents. Abwägungsstellungnahme for generated output. Beteiligungsverfahren for the process context.

**Rationale:** Correct terminology prevents a reviewer, auditor, or court from concluding that the system was designed for a different legal procedure. A system that processes "Bürgerbeschwerden" is processing something different from one that processes "Einwendungen im Beteiligungsverfahren". The distinction is not stylistic.

**Rejected Alternatives:** German-English hybrid naming (German domain types, English docstrings) was considered for developer accessibility. Rejected because it fragments the domain language and makes it ambiguous which legal concept is referenced in any given context. Citizen-facing vocabulary (e.g., "Bürgerbeschwerde", "Vorschlag") was considered to align with how some submitted documents self-describe. Rejected because the system operates in the formal procedure context where the legal term controls, regardless of how the citizen labeled the submission.

**Consequences:** All domain models, ADRs, docstrings, and README content use this terminology. The Abwägungsstellungnahme structure (Sachverhalt, Vorgebrachte Einwendung, Rechtliche Würdigung, Abwägungsergebnis) is the canonical output format. Code review must flag any use of incorrect terms as a domain error, not a naming preference.

---

### ADR-002: Predefined Catalog as Matching Backbone

**Status:** Accepted

**Context:** When receiving a large volume of Einwendungen, two structural approaches exist for routing them toward a response: (1) a predefined catalog of known argument types, or (2) dynamic clustering of the incoming document set. A predefined catalog requires upfront classification work but produces stable, auditable category labels. Dynamic clustering adapts to the input set but produces categories that exist only within a single processing run.

**Decision:** The system uses a predefined, maintainable catalog of known objection patterns. Each entry contains an argument type description, legal domain, and retrieval guidance. Documents that produce no catalog match generate a `NoMatchEvent` rather than a draft.

**Rationale:** Predefined entries produce auditable matching decisions. A catalog match has a human-readable description and a defined legal domain that can be inspected and explained to a non-technical reviewer or court. Emergent clustering cannot produce the same explainability: a dynamically discovered cluster has no inherent label, no associated legal domain, and no predefined response strategy. Additionally, the catalog functions as an explicit knowledge base: it accumulates institutional knowledge about which objection types appear in practice and how they should be addressed.

**Rejected Alternatives:** Dynamic clustering of the incoming document batch: Rejected because clusters have no inherent labels, cannot be associated with legal domains in advance, and do not generalize across procedures. Free-form LLM classification without a catalog: The model could assign arbitrary category names to objections. Rejected because the resulting categories are not stable across runs, cannot be associated with predefined response strategies, and cannot be audited against stable definitions.

**Consequences:** A catalog management workflow is required. `NoMatchEvent` is the primary signal for catalog extension and is the most valuable feedback loop for system improvement. The catalog is the only mechanism for introducing new response patterns; adding a category means adding a catalog entry, not changing code.

---

### ADR-002b: Catalog Matching Mechanism

**Status:** Accepted

**Context:** ADR-002 decides that a predefined catalog is used but does not specify the technical mechanism that produces the matching decision. This is the central technical decision of the Triage context and has direct audit implications: an embedding similarity score is a numeric value that can be inspected and explained; an LLM classification decision is harder to reconstruct after the fact.

**Decision:** The system uses a two-stage matching mechanism. Stage one is embedding similarity: the extracted objection arguments are embedded and matched against pre-computed embeddings of catalog entry descriptions via FAISS cosine similarity. If the top-match cosine similarity exceeds a configurable `catalog_match_threshold`, the match is accepted and the confidence score is stored directly. Stage two is LLM classification fallback: if no entry exceeds the threshold, the system performs a single LLM call with the full catalog as a structured enum and requests a classification with a confidence estimate. If the LLM classification confidence also falls below a second configurable threshold `catalog_llm_threshold`, a `NoMatchEvent` is emitted.

**Rationale:** Embedding similarity is the preferred primary mechanism because the confidence score is numeric, directly interpretable, and reproducible given the same embedding model and catalog. It is consistent with the FAISS infrastructure already in use for retrieval. LLM classification is retained as a fallback because some objections paraphrase legal arguments in ways that do not cluster well in embedding space. The two-stage design keeps the common case (clear match) fully auditable while using the more powerful but less transparent LLM path only for ambiguous cases.

**Rejected Alternatives:** LLM classification as primary mechanism. Rejected because the decision trace is harder to reconstruct and explain to a non-technical reviewer. Keyword-rule-based matching per category. Rejected because it requires manual rule maintenance and fails on paraphrase. Embedding-only with no LLM fallback. Rejected because it produces `NoMatchEvent` for objections that are semantically distant from catalog descriptions but clearly classifiable by a language model.

**Consequences:** Each catalog entry requires a pre-computed embedding stored alongside its description. Both thresholds (`catalog_match_threshold`, `catalog_llm_threshold`) are configurable parameters included in `retrieval_config_hash`. The matching stage and confidence score are stored in `CatalogMatch` via `match_stage: Literal["embedding", "llm_fallback"]` and `konfidenz_score` and logged to AuditLog. The `match_stage` field is part of the canonical `CatalogMatch` model defined in `core/models.py` (see ADR-011).

---

### ADR-003: Hybrid Retrieval with Reciprocal Rank Fusion

**Status:** Accepted

**Context:** Legal texts such as BauGB, VwVfG, and BImSchG use precise, domain-specific identifiers (e.g., "§3 Abs. 1 BauGB") that must be retrievable by exact string matching. At the same time, objection arguments often paraphrase legal concepts in colloquial or semi-formal language that requires semantic matching. No single retrieval modality handles both requirements adequately.

**Decision:** Hybrid retrieval combining BM25 sparse retrieval with FAISS dense retrieval. Result lists merged using Reciprocal Rank Fusion (RRF). No cross-encoder reranker in v1.

**Rationale:** Dense retrieval alone fails on exact legal identifiers: a query mentioning "§3 BauGB" may not embed close to a chunk whose primary content is that paragraph, because the embedding is dominated by semantic context rather than identifier strings. Sparse retrieval alone fails on semantic paraphrase: an objection citing noise concerns without using the BImSchG keyword vocabulary will not keyword-match the relevant norm. RRF operates on rank positions rather than raw scores, which eliminates the incompatible scale problem between BM25 and FAISS cosine similarity — no normalization or weighting of raw scores is required. A cross-encoder reranker was considered as a third layer. Rejected for v1 because it adds per-candidate inference latency, requires a domain-specific training corpus, and the two-modality hybrid already addresses the primary precision gap. The decision is deferred to a future ADR if retrieval precision proves insufficient in testing.

**Rejected Alternatives:** Dense-only retrieval: Rejected because it fails on exact legal identifier queries as described above. Sparse-only retrieval: Rejected because it fails on paraphrase. Score normalization and weighted linear combination of BM25 and FAISS scores: Rejected because BM25 and FAISS cosine similarity are on incompatible scales, making the weighting coefficient brittle and non-interpretable. RRF requires no such coefficient.

**Consequences:** The retrieval layer has two separate retriever components and one fusion step. RRF k-constant is part of `retrieval_config_hash`. Both retrievers must be independently testable.

---

### ADR-004: Paragraph-Boundary Chunking

**Status:** Accepted

**Context:** German federal law is structured into §-units (Paragraphen) with explicit hierarchical subsections (Absatz, Satz). This structure is semantically significant: a single Absatz is typically a self-contained normative provision. The verification logic in ADR-006 requires that each chunk carry a stable paragraph identifier so that a generated citation can be matched back to a specific retrieved chunk. This requirement constrains the chunking strategy.

**Decision:** Legal source texts are chunked at paragraph boundaries (§-level), not at fixed token windows. One chunk corresponds to one paragraph or subsection. Each chunk carries a canonical paragraph identifier as a structured metadata field.

**Rationale:** Token-window chunking splits paragraphs at arbitrary positions, creating chunks that contain partial provisions. A chunk spanning the end of §3 Abs. 1 and the beginning of §3 Abs. 2 cannot be cited as either provision. Paragraph-boundary chunking ensures each retrieved chunk is a self-contained legal unit, which is the prerequisite for the post-hoc verification defined in ADR-006. Without this property, verification would need to infer which chunk corresponds to which citation, which is fragile.

**Rejected Alternatives:** Fixed token-window chunking (512/1024 tokens): Rejected because it splits paragraphs at arbitrary positions and makes citation verification impossible, as described above. Sentence-level chunking: Rejected because German legal sentences are frequently several hundred tokens long, span multiple normative concepts, and do not correspond to the citation granularity used in Abwägungsstellungnahmen. The natural citation unit in German administrative law is the Absatz, not the sentence.

**Consequences:** The ingestion pipeline requires an XML parser targeting the rechtsinformationen.bund.de schema, not a generic text splitter. Each chunk carries a `paragraph_id` field in canonical form.

---

### ADR-005: Domain-Routed Retrieval with Confidence Fallback

**Status:** Accepted

**Context:** The Bundesrecht corpus covers hundreds of laws across multiple legal domains (Baurecht, Immissionsschutz, Naturschutz, Verwaltungsverfahren, etc.). An Einwendung about noise from a wind turbine is governed by BImSchG, not BauGB or NatSchG. Retrieving without domain pre-filtering means the top-k results draw from the entire corpus, diluting the relevant norms with unrelated provisions and degrading citation quality.

**Decision:** An LLM-based classifier identifies the relevant legal domain from extracted objection arguments before retrieval. The classifier output is used as a metadata pre-filter on the retrieval index. If classifier confidence falls below a configurable threshold, the system falls back to full-corpus retrieval without filtering.

**Rationale:** LLM-based classification is used rather than keyword heuristics because objections paraphrase legal domain vocabulary: a noise objection may not mention "Immissionsschutz" but will still be classified correctly by a language model with sufficient context. The confidence threshold and fallback to full-corpus retrieval ensure the system degrades gracefully when the domain cannot be determined with confidence, rather than silently retrieving from the wrong domain. This pattern is domain-routed retrieval with a confidence-gated fallback: the classifier runs once, retrieval runs once; there is no iterative loop. It is not multi-step retrieval, which involves retrieving, analyzing results, and retrieving again. The failure mode here is misclassification, not a poor retrieval iteration — these require different mitigation strategies.

**Rejected Alternatives:** Full-corpus retrieval without domain filtering: Rejected because the corpus size makes top-k results noisy when not domain-scoped, degrading citation precision. Keyword-rule-based domain classification: Rejected because it fails on paraphrase and requires manual rule maintenance for each domain. Hard domain filtering without confidence fallback: Rejected because a misclassified domain would produce zero relevant results with no recovery path. The confidence-gated fallback is the minimum safety mechanism.

**Consequences:** The classifier confidence score and routing decision (domain-filtered or full-corpus fallback) are stored in `RetrievalMetadata.domain_confidence` and `RetrievalMetadata.fallback_used`. The confidence threshold is included in `retrieval_config_hash`.

---

### ADR-006: Hallucination Strategy and Verification Routing

**Status:** Accepted

**Context:** The system generates Abwägungsstellungnahmen citing specific legal paragraphs. A fabricated or incorrect paragraph citation in a public authority document is not a quality degradation but an institutional failure. This is the primary risk of LLM-based generation in this domain.

**Decision:** Two-layer hallucination prevention. Layer one: constrained prompting. The generation prompt explicitly prohibits the model from citing any paragraph not present in the retrieval context. If no relevant norm is found, the model must state the absence explicitly. Layer two: post-hoc regex verification. Every §-reference in the generated text is extracted, normalized to canonical form, and matched against the `paragraph_id` fields of the retrieved chunks. Any reference not found sets `verified: False` on the corresponding `Rechtsgrundlage`.

Canonical normalization covers variant forms before matching. The following all normalize to `baugb_§3_abs1`: `§ 3 Abs. 1 BauGB`, `§3 (1) BauGB`, `§ 3 I BauGB`. Normalization must handle whitespace variants, Roman numeral subsection notation, and parenthetical notation.

Verification scope: §-references only. BVerwG case law citations and administrative regulations (Verwaltungsvorschriften) are explicitly out of scope for v1. Their inclusion requires a separate verified index and is deferred to a future ADR.

If any `Rechtsgrundlage` has `verified: False`, `wuerdigungs_status` is set to `UNTERDRUECKT_UNVERIFIED` and `rechtliche_wuerdigung` and `abwaegungsergebnis` are set to `None`. The Sachbearbeiter receives Sachverhalt, Vorgebrachte Einwendung, Catalog-Match, and the Rechtsgrundlagen list with verified status, plus an explicit note requiring manual drafting.

**Rationale:** Hard Failure on the entire Würdigung is simpler than alternatives and more defensible in a regulated context. The Sachbearbeiter receives all necessary building blocks for manual drafting without the system producing a partially trustworthy output.

**Rejected Alternatives:** Consultable failure with red-marked unverified citations passed to Sachbearbeiter. Rejected because rubber-stamping of flagged content under time pressure is the documented failure mode in human-in-the-loop systems. In a public administration context, a partially shown draft is more dangerous than no draft.

Paragraph-level suppression (suppress only the paragraph containing an unverified citation, pass the remainder). This was explicitly considered as a middle ground between full suppression and sentence-level suppression. Rejected because attributing generated prose to specific citations at paragraph granularity requires structured per-paragraph generation, which significantly increases prompt complexity and output schema complexity without proportionate benefit. The additional engineering cost is not justified by the marginal usability improvement.

Sentence-level suppression. Rejected because it requires attributing individual sentences to specific citations at generation time, which requires per-sentence structured output and makes the generation prompt significantly more complex.

**Consequences:** A normalization utility maps citation variants to canonical `paragraph_id` form. This utility is tested independently with a representative set of variant forms. The verification step runs after generation and before the AuditLog write. `WuerdigungsStatus` enum values: `GENERIERT`, `UNTERDRUECKT_UNVERIFIED`, `NO_MATCH`.

---

### ADR-007: Federal Law Data Source Strategy

**Status:** Accepted

**Context:** The system requires a machine-readable corpus of German federal law with stable, canonical paragraph identifiers. Two publicly accessible sources exist: rechtsinformationen.bund.de provides official XML datasets; gesetze-im-internet.de provides HTML pages. The choice of source determines the parsing strategy and the stability of the paragraph identifiers required by ADR-004 and ADR-006.

**Decision:** Federal law is sourced from rechtsinformationen.bund.de (official XML datasets, Datenlizenz Deutschland 2.0). No scraping of gesetze-im-internet.de. Einwendungen documents are synthetic. Landesrecht is out of scope for v1.

**Rationale:** rechtsinformationen.bund.de provides an officially maintained XML schema with canonical, machine-readable paragraph identifiers. The identifiers are stable across updates and match the canonical form required for verification in ADR-006. The Datenlizenz Deutschland 2.0 permits reuse without restriction for both commercial and non-commercial purposes. gesetze-im-internet.de is designed for human browsing and is HTML: parsing it requires scraping, which is fragile against markup changes and is not the intended use of that resource. HTML parsing cannot produce stable paragraph identifiers reliably. Landesrecht is out of scope for v1 because it varies by Bundesland and would require separate ingestion pipelines for each jurisdiction; this complexity is deferred until the Bundesrecht pipeline is validated.

**Rejected Alternatives:** gesetze-im-internet.de scraping: Rejected because the source is HTML, paragraph identifiers are not machine-readable, and the approach is fragile against markup changes. Commercial legal databases (beck-online, juris): Rejected because they require licensing agreements, do not expose bulk data export APIs, and would make the system's legal corpus non-reproducible without an active commercial subscription.

**Consequences:** The ingestion pipeline includes an XML parser targeting the rechtsinformationen.bund.de schema. Download date and source URL are stored as corpus metadata and included in `retrieval_config_hash`.

---

### ADR-008: EU AI Act Risk Classification

**Status:** Accepted

**Context:** The EU AI Act (Regulation 2024/1689) Annex III Nr. 5(a) explicitly covers AI systems that assist public authorities in assessing entitlements or administrative services where the impact on citizens is significant. An Abwägungsstellungnahme in a Beteiligungsverfahren could fall under this provision under a strict reading, given that the outcome of the procedure affects citizen interests (construction approval).

**Decision:** The system is classified as limited risk under the EU AI Act. Full high-risk compliance obligations are not implemented in v1.

**Rationale for limited risk classification:** The system does not make or automate administrative decisions. Every Abwägungsstellungnahme is a draft requiring explicit Sachbearbeiter approval via `apply_freigabe()` before any official use. The Sachbearbeiter is the legally responsible decision-making instance. The system performs no profiling of individual citizens. It does not assess eligibility, entitlements, or individual rights. The human-in-the-loop design is enforced at the data model level through the DRAFT/APPROVED state machine, not merely stated in documentation.

**Classification Limitations:** This ADR documents the current technical assessment, not a final legal determination. The applicability of Annex III Nr. 5(a) to a drafting-assistance system with mandatory human approval is genuinely ambiguous in the current legal interpretation landscape. In a real deployment, the final risk classification would require consultation with the organization's data protection officer and potentially the relevant supervisory authority. Any future change that enables the system to communicate directly with citizens or to automatically finalize Abwägungsstellungnahmen without human review would require immediate re-classification and a full high-risk compliance assessment. This constraint is a design invariant documented here, not a feature preference.

**Consequences:** The DRAFT/APPROVED state machine is a permanent architectural constraint. `apply_freigabe()` is the only valid transition to APPROVED. This invariant must be maintained in all future branches and must not be bypassed by any pipeline shortcut.

---

### ADR-009: Audit Trail as First-Class Bounded Context

**Status:** Accepted

**Context:** German administrative proceedings under VwVfG require documentation of procedural steps for potential judicial review. Any AI-generated content used in official documents must be traceable to its generation parameters and approval history. A complaint about a specific Abwägungsstellungnahme filed months after generation requires the ability to reconstruct exactly what was generated, with which model version, which retrieval configuration, and who approved it. This traceability requirement cannot be satisfied by application logs, which are ephemeral and unstructured.

**Decision:** AuditLog is a dedicated Bounded Context with an append-only event store. Every system decision affecting the output of an Abwägungsstellungnahme emits a typed event. Every Sachbearbeiter action emits a corresponding event. No event is modified or deleted. Other contexts depend on AuditLog unidirectionally; AuditLog has no dependency on other contexts.

**Rationale:** Append-only storage ensures the audit record cannot be retroactively modified to conceal a generation error, an unverified citation, or a Sachbearbeiter action. Mutability would undermine the integrity guarantee the system provides under ADR-008 (EU AI Act limited-risk classification) and administrative law. A subsequent action on the same Einwendung (e.g., Sachbearbeiter approval after a revision) is represented as a new event, not an update to an existing one: the event log is a factual record of what happened, in order. The unidirectional dependency (all contexts write to AuditLog, AuditLog writes to nobody) prevents AuditLog from becoming a coordination hub that reintroduces coupling between BCs.

**Rejected Alternatives:** Mutable event store with versioning: Rejected because it adds implementation complexity without benefit. The domain events are immutable facts; the history of changes is always represented as a sequence of new events. Database table with soft deletes and updated_at: Rejected because soft deletes are effectively mutation with an extra flag, and the audit record can be "deleted" — which violates the non-modification guarantee.

**Consequences:** AuditLog exposes a write interface (emit event) and a read interface (query by `einwendungs_id`, time range, `wuerdigungs_status`). Reproducibility fields (`model_version`, `prompt_version`, `retrieval_config_hash`) are mandatory on every `Abwaegungsstellungnahme` and are set at generation time.

---

### ADR-010: PII Masking and GDPR-Compliant Processing

**Status:** Accepted

**Context:** Einwendung documents submitted by citizens contain personal data: names, addresses, contact details, and occasionally more specific identifiers. Processing this data in an LLM-based pipeline without masking would mean transmitting it to an external LLM API in unredacted form. This is incompatible with GDPR Art. 5(1)(c) (data minimization) and Art. 25 (data protection by design). The masking boundary is the architectural mechanism for enforcing data minimization: downstream contexts never see PII and therefore cannot leak it.

**Decision:** PII masking is the final step of DocumentIngestion. Raw documents are stored in an access-controlled store separate from the processing pipeline. All downstream contexts operate on masked text only. Masking uses regex for structured PII and an LLM pass for unstructured name detection.

**Rationale:** The dual-approach reflects the structure of PII in these documents. Structured PII (email addresses, phone numbers, postal codes, IBAN numbers) follows predictable patterns that regex handles efficiently, deterministically, and with no inference cost. Unstructured name references embedded in free text (e.g., "mein Nachbar Herr Müller" or "wie die Bürgerinitiative um Frau Schmidt fordert") are not reliably matched by regex without an exhaustive name dictionary. An LLM pass handles these cases. Regex-first also reduces the token count passed to the LLM masking call, lowering cost and latency. Raw documents are stored separately so that the system retains access to the original submission for legal purposes (the Sachbearbeiter may need the original), while ensuring the processing pipeline only ever sees masked text.

**Rejected Alternatives:** LLM-only masking: Rejected because LLM inference is non-deterministic, adds latency and cost for patterns that regex can handle trivially, and introduces a risk that a model version change alters masking behavior. Regex-only masking: Rejected because informal name mentions in free text are not reliably matched without a comprehensive name dictionary, which cannot be exhaustively maintained and would require GDPR-sensitive data (a name list) to operate.

**Consequences:** The raw document store requires access control and a defined retention policy. A test suite verifies no PII passes the ingestion boundary. Masking accuracy is part of the Architecture Fitness Functions.

---

### ADR-011: Core Data Model Design Decisions

**Status:** Accepted

**Context:** The `Abwaegungsstellungnahme` is the central data structure of the system. It determines what ResponseDrafting produces, what AuditLog stores, what the Sachbearbeiter sees, and how verification suppression is implemented. The design decisions embedded in this model require explicit documentation because they encode architectural and regulatory constraints that are not self-evident from the type signatures alone.

**Decision:** The canonical data model is defined in `core/models.py` as documented in the Core Data Model section of this file. The field-level decisions listed in the Rationale below are fixed and may not be reversed without a new ADR.

**Rationale:**

`wuerdigungs_status: WuerdigungsStatus` is an enum rather than a boolean suppression flag because the system has three distinct states (generated, suppressed due to verification failure, no catalog match) that require different Sachbearbeiter workflows. A boolean cannot express the distinction between suppression and no-match; a three-value enum forces callers to handle each state explicitly.

`model_version`, `prompt_version`, `retrieval_config_hash` are mandatory non-optional fields because reproducibility is a non-negotiable audit requirement. A complaint about a specific Abwägungsstellungnahme months after generation must be investigable. Optional reproducibility fields would allow incomplete records to be written without a compile-time error.

`catalog_match: Optional[CatalogMatch]` is Optional because a no-match is a valid and important system state, not an error. A `None` value triggers a `NoMatchEvent` and invites the Sachbearbeiter to propose a catalog extension. Making it required would force a sentinel value that obscures the semantics.

`CatalogMatch` includes `match_stage: Literal["embedding", "llm_fallback"]` in addition to `catalog_id`, `beschreibung`, and `konfidenz_score`. This field records which stage of the two-stage matching process (ADR-002b) produced the match. It is required for audit: a Sachbearbeiter or reviewer must be able to determine whether a match was produced by the primary embedding path or the LLM fallback path, because these have different confidence and explainability characteristics.

`status: AbwaegungsStatus` with a DRAFT/APPROVED state machine replaces `sachbearbeiter_freigabe: Optional[Freigabe]` from earlier iterations. The Optional pattern allowed a DRAFT instance to be treated as a completed output by a caller that neglected to check the field. The state machine makes the constraint explicit and enforceable: `apply_freigabe()` is the only path to APPROVED, and the pipeline can assert `status == APPROVED` before writing a final record.

`rechtliche_wuerdigung: Optional[str]` and `abwaegungsergebnis: Optional[str]` are Optional because they are set to `None` when `wuerdigungs_status != GENERIERT`. This is a direct encoding of the Hard Failure routing defined in ADR-006. Making them required would force sentinel strings that a downstream caller might display as content.

**Consequences:** `core/models.py` is written once at skeleton stage and treated as stable. Changes to this file require a new ADR entry or an explicit amendment to ADR-011. No BC-local model may duplicate or shadow these types.

---

## Walking Skeleton Scope

The skeleton covers the thinnest end-to-end path touching all four BCs. Nothing else.

**In scope for skeleton:**
- `core/models.py` with the full Pydantic model as defined in ADR-011
- `core/protocols.py` with LLMClient, Retriever, Embedder protocols
- `document_ingestion/service.py`: accepts plain text string, returns it unchanged (no PDF, no PII masking)
- `triage/catalog.py`: two hardcoded catalog entries
- `triage/service.py`: FAISS cosine similarity match against two entries, no LLM fallback
- `response_drafting/service.py`: single dense retrieval call, one LLM call, returns valid `Abwaegungsstellungnahme` in DRAFT status
- `audit_log/store.py`: writes events to a local JSON file
- `pipeline.py`: orchestrates all four BCs in sequence
- `tests/test_smoke.py`: one end-to-end test asserting a valid DRAFT `Abwaegungsstellungnahme` is returned

**Explicitly out of scope for skeleton:**
PDF ingestion, PII masking, BM25, RRF, domain routing, verification logic, FastAPI, observability.

---

## Branch Sequence

| Branch | Scope |
|--------|-------|
| `feat/skeleton` | Walking skeleton as defined above |
| `feat/pii-masking` | PII masking in DocumentIngestion, raw document store |
| `feat/hybrid-retrieval` | BM25 + FAISS with RRF score fusion |
| `feat/domain-routing` | LLM-classified metadata pre-filtering with confidence fallback |
| `feat/verification` | Post-hoc §-reference verification, normalization utility, Hard Failure routing |
| `feat/fastapi` | FastAPI layer exposing the pipeline |
| `feat/no-match-workflow` | NoMatchEvent, catalog extension invitation to Sachbearbeiter |
| `feat/observability` | structlog, OpenTelemetry, prometheus_client |

---

## Code Standards

Follow the Google Python Style Guide throughout: module-level imports only, full type annotations, Google-format docstrings (Args/Returns/Raises sections), specific exception handling, readability over cleverness.

Protocol-based dependency injection for LLMClient, Retriever, and Embedder. No concrete implementation is imported by BCs directly. The Coordinator in `pipeline.py` wires dependencies.

Every BC service is independently testable without the full pipeline. The smoke test in `test_smoke.py` is the only test that exercises the full pipeline.

Prompt templates are versioned. The version string in `prompt_version` must match the template filename (e.g. `abwaegung_v1` matches `prompts/abwaegung_v1.py`). Changing a prompt requires incrementing the version.