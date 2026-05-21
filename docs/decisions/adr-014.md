## ADR-014: Cross-Encoder Reranker Model Selection

**Status:** Accepted

**Context:** Spike D confirmed that cross-encoder reranking over the Top-20 hybrid
RRF candidate pool is necessary for reliable Top-1 retrieval on German legal texts.
A reranker model must be selected. The choice has implications for retrieval quality,
latency, and multilingual support.

**Decision:** `BAAI/bge-reranker-v2-m3` is the selected reranker for `feat/reranking`.

**Rationale:** BGE-reranker-v2-m3 is a multilingual cross-encoder with strong
performance on German texts, confirmed in MTEB benchmarks and in Spike D results.
It correctly promoted §1 Abs. 6 (Q-B) and §3 Abs. 1 (Q-C) to Rank 1 with
well-differentiated scores (drop from 0.84 to 0.63 and 0.92 to 0.75 respectively).
Rerank scores are interpretable and stable across queries.

**Rejected Alternatives:** `jinaai/jina-reranker-v2-base-multilingual`: smaller and
faster, comparable quality. Deferred as a latency optimisation candidate if BGE
proves too slow in production profiling. `mixedbread-ai/mxbai-rerank-large-v1`:
German-focused, not tested in spikes. Consider in post-skeleton evaluation if
BGE underperforms on domain-specific queries.

**Latency note:** Cross-encoder inference on 20 query-document pairs per retrieval
call is significantly more expensive than bi-encoder retrieval (estimated 5-10x).
This is acceptable for Sachbearbeiter-facing latency in the current scope. Latency
profiling is required before production deployment. Batching and async execution
are mitigation options if needed.

**Consequences:** `feat/reranking` implements `BAAI/bge-reranker-v2-m3` as the
reranker over Top-20 hybrid RRF candidates. The reranker model name is included
in `retrieval_config_hash` for reproducibility. The top_n parameter for the
candidate pool (currently 20) is configurable.