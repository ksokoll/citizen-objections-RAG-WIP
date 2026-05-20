# Walking Skeleton Scope — citizen-objections-RAG

## Principle

The walking skeleton follows Cockburn's definition: real architecture, thin functionality. No simplified data structures. No placeholder schemas that get replaced later. Simplified logic where needed, real connections throughout.

---

## What the Skeleton Delivers

A plain-text Einwendung document goes in. A valid `Abwaegungsstellungnahme` in `DRAFT` status comes out. All four Bounded Contexts are traversed. One smoke test confirms the full path end-to-end.

---

## Scope per Component

### `core/models.py`
Full `Abwaegungsstellungnahme` Pydantic model as defined in ADR-011 and the CLAUDE.md Core Data Model section. All mandatory fields present, including the three reproducibility fields. Hardcoded values are acceptable for the skeleton (`model_version="skeleton-v0.1"`, `prompt_version="abwaegung-stub-v0.1"`, `retrieval_config_hash="skeleton-stub"`), but no fields may be omitted. The state machine (`DRAFT` / `APPROVED`) and `apply_freigabe()` are implemented here from day one. This is non-negotiable: ADR-008's EU AI Act argumentation depends on architecturally enforced human-in-the-loop, and the implementation must match the ADR from the first commit.

### `core/protocols.py`
Three Protocols are defined and used from day one:

- `LLMClientProtocol`: interface for LLM text generation
- `KatalogMatcherProtocol`: interface for catalog matching against the predefined entry set
- `AuditEventPublisherProtocol`: interface for writing audit events

Concrete implementations are minimal in the skeleton. The Protocol separation is not. Adding Protocols later means refactoring every consumer. This is the point at which walking skeletons typically fail.

### `core/events.py`
Typed domain events consumed by AuditLog. Defined in full even if the skeleton only emits one event type.

### `document_ingestion/service.py`
Accepts a plain-text string. Returns it unchanged. No PDF parsing, no PII masking. Those come in `feat/pii-masking`.

### `triage/catalog.py`
Five hardcoded catalog entries with recognizably distinct topic profiles:

1. Environmental protection (Umweltschutz)
2. Heavy traffic and road damage (Schwerlastverkehr)
3. Noise pollution (Lärmschutz)
4. Procedural objections (Verfahrensformalitäten)
5. Species protection (Artenschutz)

Five entries are the minimum to verify that FAISS similarity matching actually discriminates between topics, not just picks the closer of two points.

### `triage/service.py`
FAISS cosine similarity matching against the five catalog entries. No LLM classification fallback in the skeleton. The `KatalogMatcherProtocol` is the interface; `FaissCatalogMatcher` is the concrete implementation. Returns a `CatalogMatch` with a real confidence score and `match_stage="embedding"`.

### `response_drafting/service.py`
Dense retrieval against a small hardcoded norm stub (three to five §-snippets sufficient). LLM call via a stub implementation of `LLMClientProtocol` that returns a fixed string (`"Skeleton-Würdigung: Dies ist ein Platzhalter."`) without making an API call. This keeps the smoke test deterministic and cost-free. The real LLM call is introduced as a distinct step after the skeleton passes. Returns a valid `Abwaegungsstellungnahme` in `DRAFT` status with all fields populated.

### `audit_log/store.py` and `audit_log/service.py`
Writes events to a local JSON file via the `AuditEventPublisherProtocol`. Append-only. No database, no external service.

### `pipeline.py`
Coordinator. Calls each BC in sequence. Performs no business logic itself. Wires all Protocol dependencies. The only file that imports from more than one BC.

### `tests/test_smoke.py`
One end-to-end test. Asserts that a valid `Abwaegungsstellungnahme` is returned with `status=DRAFT` and all mandatory fields present. Does not assert on LLM output content (stub returns a fixed string, so content assertion is not meaningful here).

---

## Explicitly Out of Scope for the Skeleton

| Feature | Introduced in Branch |
|---|---|
| PII masking | `feat/pii-masking` |
| PDF ingestion | `feat/pii-masking` |
| BM25 sparse retrieval | `feat/hybrid-retrieval` |
| Reciprocal Rank Fusion | `feat/hybrid-retrieval` |
| Domain-routed retrieval | `feat/domain-routing` |
| §-reference verification | `feat/verification` |
| Hard Failure routing | `feat/verification` |
| Real LLM call | First step after skeleton smoke test passes |
| FastAPI layer | `feat/fastapi` |
| Observability stack | `feat/observability` |
| NoMatchEvent workflow | `feat/no-match-workflow` |
| LLM classification fallback for catalog matching | `feat/domain-routing` |