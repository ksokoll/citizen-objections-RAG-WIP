## ADR-015: Bounded-Context Cleanup Before Retrieval Block

**Status:** Accepted

**Context:** During the Triage milestone, an architecture review against `architecture-foundations.md` and `BOUNDED_CONTEXTS.md` identified three deviations from the project's bounded-context discipline. The review was run before starting the Retrieval block because the affected interfaces (`TriageResult`, `RetrieverProtocol`, the location of `HybridRetrievalService`) define the contracts the Retrieval implementation will consume. Fixing them post-hoc would have required re-touching the Retrieval code immediately after writing it.

The three deviations were: (1) ResponseDrafting imported `classify_einwendungs_typ` directly from `app.triage`, violating the bounded-context isolation rule; (2) `HybridRetrievalService` lived in `app.triage.retrieval`, but per `BOUNDED_CONTEXTS.md` the RAG pipeline is owned by ResponseDrafting; (3) `RetrieverProtocol.retrieve()` took `query_embedding: list[float]`, but the concrete `HybridRetrievalService.retrieve()` takes `(query: str, partition: str)`, and embedding generation belongs inside the retriever (BM25 needs the raw text anyway).

**Decision:** Three structural corrections:

1. `TriageResult` carries an explicit `einwendungs_typ: EinwendungsTyp` field, populated by `TriageService.triage()`. ResponseDrafting reads `triage_result.einwendungs_typ` and no longer imports from `app.triage`.

2. `HybridRetrievalService` and `CorpusIndex` move from `src/app/triage/retrieval.py` to `src/app/response_drafting/retrieval.py`. The classification function `classify_einwendungs_typ` stays in `app.triage.classification` as a pure helper, called only within the Triage context.

3. `RetrieverProtocol.retrieve()` signature corrected to `(query: str, partition: str, top_k: int = 5) -> list[RetrievedChunk]`. Embedding generation is an implementation detail of the concrete retriever, not exposed at the protocol boundary.

**Rationale:**

For (1), the data-flow option keeps both bounded contexts honest. The classification logic lives in Triage where it belongs domain-wise. The result of that logic travels with `TriageResult` to ResponseDrafting as a plain field, which is the canonical pattern from `architecture-foundations.md` ("Cross-context communication goes through explicit DTOs passed as function arguments"). It also matches the original `BOUNDED_CONTEXTS.md` spec, which already listed `einwendungs_typ` on `TriageResult`. The implementation simply caught up to the spec.

For (2), `BOUNDED_CONTEXTS.md` explicitly assigns RAG-pipeline ownership to ResponseDrafting. Keeping `HybridRetrievalService` in Triage created a misleading dependency direction: ResponseDrafting would have imported retrieval logic from Triage, which is the opposite of the intended flow. Moving the file aligns the codebase with the documented architecture.

For (3), exposing `query_embedding` at the protocol boundary couples callers to the embedding step. Hybrid retrievers that combine sparse (BM25) and dense (FAISS) ranking inside the implementation need the raw query string in any case. The corrected signature also matches the canonical skeleton pattern from `architecture-foundations.md` (`def search(self, query: str, top_k: int = 5)`), with `partition` added for multi-corpus routing.

**Rejected Alternatives:**

For (1), considered moving `KATALOG` to `core/` so both BCs could read it independently. Rejected because `KATALOG` is semantically a Triage domain artifact (catalog matching is Triage's job), and elevating it to the Shared Kernel would weaken the discipline that `core/` only contains truly cross-cutting types. Also considered injecting a `partition_for_catalog` callable from the Coordinator. Rejected for this round because the cleanup is in scope; the `catalog_id` to `partition` mapping is a separate decision deferred to the Retrieval round (see Open Question below).

For (2), considered promoting Retrieval to its own bounded context (`src/app/retrieval/`). Rejected for this round because it would constitute a new context boundary that has no clear additional consumer yet. If a future Verification BC needs retrieval, this can be re-evaluated. For now, the relocation to ResponseDrafting aligns with the documented ownership.

For (3), considered keeping both signatures behind a Protocol union or adapter pattern. Rejected as over-engineering for the skeleton; one protocol with one signature is simpler and matches the only concrete implementation.

**Consequences:**

`TriageResult` is now the single source of truth for document-level `einwendungs_typ`. ResponseDrafting depends only on `core/` and its own internal modules. The four bounded-context isolation greps (each BC against each other BC) return zero hits, which becomes a verifiable architecture-fitness criterion for CI.

The Retrieval block can now implement against a clean `RetrieverProtocol`. The block's first task is to decide how ResponseDrafting derives the `partition` argument from `ExtrahiertesArgument.catalog_id`. Three options remain open (carry partition data on the argument via Triage, inject a mapping callable from the Coordinator, move `KATALOG` to `core/`). That decision will be documented in its own ADR when the Retrieval block resolves it.

87 tests pass post-cleanup. No production behavior changed; the cleanup is structural only.