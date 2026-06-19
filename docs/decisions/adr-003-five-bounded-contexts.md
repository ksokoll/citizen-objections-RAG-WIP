## ADR-003: Hybrid Retrieval with Reciprocal Rank Fusion

**Status:** Accepted (amended — reranking moved to feat/reranking)

**Context:** Legal texts require both exact term matching (paragraph identifiers,
legal terminology) and semantic similarity (paraphrased arguments). Neither
retrieval strategy alone is sufficient.

**Decision:** Hybrid BM25 sparse retrieval plus FAISS dense retrieval, merged via
Reciprocal Rank Fusion (RRF). Cross-encoder reranking over Top-20 candidates in
`feat/reranking`, not deferred to Phase 2.

**Rationale:** Empirically validated in Spikes A–C. Dense-only fails on a 955-chunk
BauGB corpus: zero correct Top-1 results. Hybrid BM25 + RRF places correct answers
in Top-4 for all three test queries. However, RRF scores within the candidate pool
differ by less than 0.001, making Top-1 selection effectively random without a
reranker. Spike C diagnostic confirms this is a ranking problem, not a recall
problem. Cross-encoder reranking is the validated fix: literature confirms +17pp
MRR@3 on legal documents. "Marginal gain" rationale from prior version is not
supported by the spike evidence.

**Rejected Alternatives:** Dense-only: zero correct results on real corpus,
empirically rejected. BM25-only: fails on paraphrased arguments with no exact
term overlap.

**Consequences:** Retrieval layer: BM25 index, FAISS index, RRF fusion, cross-encoder
reranker over Top-20. RRF k-constant and reranker model are part of
`retrieval_config_hash`. All four components independently testable.
Recommended reranker: `BAAI/bge-reranker-v2-m3`.