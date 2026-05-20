## ADR-009: Audit Trail as First-Class Bounded Context

**Status:** Accepted

**Context:** Public authorities are subject to documentation and retention obligations. The system must be able to reconstruct, months after the fact, which version of the system produced a given draft, which retrieval results were used, what the verification outcome was, and what the Sachbearbeiter did with the output.

**Decision:** AuditLog is a dedicated Bounded Context with an append-only event store. It is not a logging sidecar or an afterthought added to existing contexts. Every system decision that affects the output of an Abwägungsstellungnahme emits a typed event to AuditLog. Every Sachbearbeiter action (review, modification, approval, rejection) emits a corresponding event. No event is ever modified or deleted.

Every `Abwaegungsstellungnahme` carries three reproducibility fields: `model_version` (LLM model identifier and snapshot), `prompt_version` (prompt template version identifier), and `retrieval_config_hash` (hash over embedding model, chunking parameters, Top-K, confidence threshold). These fields are set at generation time and are immutable.

**Rationale:** Treating AuditLog as a separate Bounded Context enforces the invariant that audit events are produced by the domain, not coupled to it. Other contexts emit events; AuditLog owns their storage and query interface. This separation also means that a future requirement to export audit records in a specific format (e.g. for a regulatory inspection) does not require changes to Triage or ResponseDrafting. The reproducibility fields are non-negotiable in a regulated context: without them, a complaint about a specific Abwägungsstellungnahme six months after generation cannot be investigated systematically.

**Rejected Alternatives:** Structured logging via structlog as the audit mechanism. Rejected because log files are mutable, not queryable as records, and do not enforce event completeness. Embedding audit fields directly in existing contexts. Rejected because it couples retention logic to domain logic and makes the audit surface invisible in the architecture.

**Consequences:** AuditLog exposes a write interface (emit event) and a read interface (query by `einwendungs_id`, by time range, by `wuerdigungs_status`). It has no dependency on other Bounded Contexts. All other contexts depend on AuditLog unidirectionally.