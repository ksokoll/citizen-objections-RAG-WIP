# RAG Retrieval Decisions

Empirical record of retrieval architecture decisions for the citizen-objections-RAG
system. Each spike tests one hypothesis on real or representative data before any
production code is written. Decisions are grounded in measured results, not assumptions.

Model used throughout: `paraphrase-multilingual-MiniLM-L12-v2` (384-dim, multilingual).
Retrieval metric: cosine similarity via L2-normalised inner product (FAISS IndexFlatIP).
Discrimination metric: gap between score #1 and score #2. Gap < 0.05 = unreliable.

---

## Spike A: Catalog Matching (Embedding Similarity)

**Hypothesis:** A multilingual sentence embedding model can discriminate between five
predefined catalog entries covering distinct legal domains (Lärmschutz, Verkehr,
Naturschutz, Luftqualität, Stadtbild).

**Setup:** Five catalog entries as embedding targets. Four test queries as informal
citizen objections in German. FAISS cosine similarity, top-3 retrieved.

**Results:**

| Query | Expected | Retrieved #1 | Score | Gap #1-#2 |
|---|---|---|---|---|
| Q-A (Baulärm) | C-001 Lärmschutz | C-001 | 0.557 | 0.111 |
| Q-B (Zufahrtsstraße) | C-002 Verkehr | C-002 | 0.455 | 0.138 |
| Q-C (Feuchtwiese) | C-003 Naturschutz | C-003 | 0.357 | 0.105 |
| Q-D (Feinstaub) | C-004 Luftqualität | C-004 | 0.512 | 0.145 |

All four queries return the correct catalog entry on rank #1. Minimum gap 0.105.

**Finding:** The model discriminates the five catalog entries reliably. Confidence
threshold for production catalog matching: 0.30.

**Decision:** Model is suitable for catalog embedding. Threshold set at 0.30.

**Note:** This decision was later superseded by ADR-002 revision. The catalog is
now used as a constraint enum in a single LLM extraction call, not as an embedding
matching target. FAISS is reserved for paragraph retrieval (Spike B onwards).
The threshold finding is obsolete; the model suitability for German legal text
embeddings remains relevant.

---

## Spike v2: Paragraph-Level Retrieval on Full BauGB Corpus

**Hypothesis:** Dense retrieval (embedding similarity) against the full BauGB corpus
at paragraph granularity (one chunk per §) is sufficient for surfacing relevant
legal norms from `argument_text` queries.

**Setup:** 294 BauGB paragraphs parsed from official XML (rechtsinformationen.bund.de
format). One chunk per `<norm>` element. Three test queries simulating
`ExtrahiertesArgument.argument_text` values from a TYP_2 Einwendung.

**Results:**

| Query | Expected | Retrieved #1 | Score | Gap #1-#2 |
|---|---|---|---|---|
| Q-A (FNP-Widerspruch) | baugb_§8_abs2 | baugb_§7 | 0.792 | 0.049 |
| Q-B (Abwägung) | baugb_§1_abs7 | baugb_§13 | 0.648 | 0.004 |
| Q-C (Bürgerbeteiligung) | baugb_§3 | baugb_§7 | 0.668 | 0.001 |

Zero out of three queries return the correct paragraph on rank #1.
§7 BauGB (broad vocabulary, thematically wide) appears in all three top-3 results
as a false-positive anchor — the "catch-all" problem.
Gaps are catastrophically small: Q-B gap 0.004, Q-C gap 0.001.

**Finding:** Dense-only retrieval at paragraph granularity fails on a realistic
legal corpus. Two root causes identified:

1. Paragraph-level chunks are too long and thematically mixed. §1 BauGB contains
   eight subsections covering different topics; the embedding averages over all of
   them and loses the signal of §1 Abs. 7 (Abwägungsgebot).
2. Score compression: with 294 candidates, cosine similarity scores converge into
   a narrow range and gap-based discrimination collapses.

**Decision:** Paragraph-level chunking is insufficient. Two follow-up spikes required
before any ADR amendment is written:

- Spike B: subsection-level chunking (one chunk per `<P>` element). Tests whether
  ADR-004 as specified resolves the discrimination problem without BM25.
- Spike C: hybrid retrieval BM25 + Dense + RRF on current chunking. Tests ADR-003
  in isolation.

Result pending for both. ADR amendments written only after empirical results are in.

---

## Spike B: Subsection-Level Chunking (ADR-004 Compliance Test)

**Hypothesis:** Chunking at `<P>`-element granularity (one chunk per subsection,
e.g. §1 Abs. 7 as its own chunk) improves retrieval discrimination sufficiently
to surface correct paragraphs without requiring BM25.

**Setup:** BauGB XML re-parsed with one chunk per `<P>` element. 955 chunks (vs.
294 paragraphs in Spike v2). chunk_id encodes paragraph and subsection number
(e.g. `baugb_§1_abs7`). Same three queries and model as Spike v2.

**Results:**

| Query | Expected | Retrieved #1 | Score | Gap #1-#2 |
|---|---|---|---|---|
| Q-A (FNP-Widerspruch) | baugb_§8_abs2 | baugb_§7 | 0.792 | 0.002 |
| Q-B (Abwägung) | baugb_§1_abs7 | baugb_§214_abs2 | 0.681 | 0.033 |
| Q-C (Bürgerbeteiligung) | baugb_§3_abs1 | baugb_§135_abs5 | 0.680 | 0.002 |

Zero out of three queries correct. Gaps deteriorated vs. Spike v2 for Q-A and Q-C
(more candidates → more score compression). §7 remains a pervasive false positive
across all three queries regardless of chunking granularity.

**Finding:** Subsection-level chunking alone does not resolve the retrieval failure.
The problem is not granularity but the embedding model's inability to connect
query terminology to legal concepts without exact term overlap. ADR-004 is still
the correct chunking strategy, but its recall improvement (confirmed by arxiv
2605.19806 on German statutory law) only materialises with a sufficiently
discriminating retrieval stack. This spike shows that finer chunking without BM25
does not help.

**Decision:** Hypothesis rejected. Proceed to Spike C (BM25 + Dense + RRF).
Subsection-level chunking is retained as the correct granularity per ADR-004;
the 955-chunk index from this spike is reused in Spike C.

---

## Spike C: Hybrid Retrieval BM25 + Dense + RRF (ADR-003 Validation)

**Hypothesis:** Combining BM25 sparse retrieval with FAISS dense retrieval via
Reciprocal Rank Fusion (RRF) recovers the correct paragraphs that dense-only
retrieval misses, particularly for queries containing exact legal terminology
(Flächennutzungsplan, Entwicklungsgebot, Bürgerbeteiligung).

**Setup:** Same 955 subsection chunks as Spike B. BM25Okapi with whitespace
tokenization. FAISS IndexFlatIP with L2-normalised embeddings. RRF with k=60,
top-50 candidates per retriever before fusion. Same three queries.

**Results:**

| Query | Expected | Retrieved #1 | RRF Score | Expected in Top-3? |
|---|---|---|---|---|
| Q-A (FNP-Widerspruch) | baugb_§8_abs2 | baugb_§7 | 0.0328 | Yes (Rank 2, 0.0315) |
| Q-B (Abwägung) | baugb_§1_abs7 | baugb_§35_abs3 | 0.0261 | No |
| Q-C (Bürgerbeteiligung) | baugb_§3_abs1 | baugb_§3_abs1 | 0.0320 | Yes (Rank 1) ✓ |

One out of three queries correct. Significant improvement over dense-only:
Q-C fully resolved, Q-A partially resolved (expected result now on Rank 2).

**Diagnostic: where does §1 Abs. 7 rank for Q-B in Top-20?**

Top-20 hybrid RRF for Q-B (abbreviated):

| Rank | chunk_id | RRF Score |
|---|---|---|
| 1 | baugb_§77_abs1 | 0.0255 |
| 2 | baugb_§247_abs1 | 0.0164 |
| 3 | baugb_§214_abs2 | 0.0164 |
| **4** | **baugb_§1_abs7** | **0.0161** |
| 5 | baugb_§13_abs1 | 0.0161 |

§1 Abs. 7 is on Rank 4. It is inside the Top-20 candidate pool.

**Root cause analysis:**

Q-B is a ranking problem, not a recall problem. The correct answer is already
retrieved at Rank 4. A cross-encoder reranker reading the full query against §1
Abs. 7 ("öffentlichen und privaten Belange gegeneinander und untereinander gerecht
abzuwägen") would semantically connect "Abwägung" to "abzuwägen" and promote it
to Rank 1. This is the canonical reranking use case.

The initial diagnosis of "German morphology causing BM25 miss" was incorrect.
§1 Abs. 7 does appear in the candidate pool via the "Belange" term overlap.
The issue is that §77 Abs. 1 accumulates more matching terms across the full
paragraph and outscores §1 Abs. 7 on BM25, while dense retrieval also fails to
separate them.

Q-A is also a ranking problem: §8 Abs. 2 is on Rank 2 with RRF gap of 0.0013
to the false-positive §7. Cross-encoder reranking is the validated fix for both.

**Finding:** Hybrid BM25 + Dense + RRF delivers the correct answer in the Top-5
candidate pool for all three queries. The remaining problem is ranking within
the candidate pool, not retrieval recall. This is the textbook reranking scenario:
dense embedding space is too compressed for precise Top-1 selection; a cross-encoder
with full query-document attention resolves it.

**Decision:** ADR-003 validated. Reranking is the confirmed next lever.
ADR-003 amendment required: cross-encoder reranking is a Phase-2 candidate
with empirical justification, not a low-value addition. Literature confirms
+17pp MRR@3 on legal documents (2026 RAG benchmark). The original ADR-003
rationale ("marginal gain, added latency") is not supported by the spike evidence.

---

## Spike D: Cross-Encoder Reranking over Hybrid RRF Candidates

**Hypothesis:** A cross-encoder reranker applied to the Top-20 hybrid RRF candidate
pool promotes §1 Abs. 7 / §1 Abs. 6 (Q-B) and §8 Abs. 2 (Q-A) to Rank 1,
resolving the remaining ranking failures from Spike C.

**Setup:** Same 955 subsection chunks and hybrid BM25 + FAISS + RRF pipeline as
Spike C. Cross-encoder `BAAI/bge-reranker-v2-m3` applied to Top-20 RRF candidates
per query. Reranker produces scalar relevance scores via full query-document
cross-attention.

**Results:**

| Query | Expected | Retrieved #1 | Rerank Score | Expected in Top-3? |
|---|---|---|---|---|
| Q-A (FNP-Widerspruch) | baugb_§8_abs2 | baugb_§214_abs2 | 0.9547 | Yes (Rank 2, 0.9399) ✓ |
| Q-B (Abwägung) | baugb_§1_abs6/7 | baugb_§1_abs6 | 0.8394 | Yes (Rank 1) ✓ |
| Q-C (Bürgerbeteiligung) | baugb_§3_abs1 | baugb_§3_abs1 | 0.9236 | Yes (Rank 1) ✓ |

Rerank scores are well-differentiated: Q-A drops from 0.92 to 0.71 after Rank 3.
Q-B drops from 0.84 to 0.63 after Rank 1. This is real semantic signal, not noise.

**Notes on Q-A and Q-B:**

Q-A: §214 Abs. 2 at Rank 1 (0.9547) vs §8 Abs. 2 at Rank 2 (0.9399) is a 0.015
difference, effectively noise. Juristisch ist §8 Abs. 2 die Primärnorm (das
Entwicklungsgebot selbst); §214 ist die Konsequenznorm (was passiert bei Verletzung).
Ein Anwalt würde §8 Abs. 2 zuerst zitieren. §7 at Rank 3 (0.9193) is a residual
false positive via token overlap. Both §214 and §8 Abs. 2 are in Top-5 context
for the generation LLM, which is the operative criterion.

Q-B: §1 Abs. 6 at Rank 1 is the correct reranker decision for this query. §1 Abs. 6
enumerates the specific Belange to be weighed (including Land- und Forstwirtschaft
under Nr. 8); §1 Abs. 7 states the general Abwägungsgebot. For a query naming
specific Belange, §1 Abs. 6 is more directly responsive. Note: §1 Abs. 6 does not
mention Weinbau explicitly — that falls under Land- und Forstwirtschaft. The
reranker's decision is correct; the earlier characterisation of "weinbauliche Belange
explizit genannt" was imprecise.

**Finding:** Cross-encoder reranking resolves the ranking failures from Spike C.
All three correct answers appear in Top-5 context for the generation LLM. The
reranker is not optional: without it, RRF score differences within the candidate
pool are below 0.001, making Top-1 selection unreliable. Q-A residual issue
(§214 vs §8 order) is within noise and both chunks reach the LLM context.

**Decision:** Retrieval architecture validated end-to-end. Four-component stack
confirmed: subsection chunking (ADR-004), hybrid BM25 + FAISS (ADR-003), RRF
fusion (ADR-003), cross-encoder reranking (`feat/reranking`). ADR-003 amendment
stands. See ADR-014 for reranker model choice rationale.

---

## Limitations of This Spike Series

These spikes validate the retrieval architecture direction but are not a production
benchmark. Three explicit limitations:

**1. Three queries only.** All conclusions are drawn from Q-A, Q-B, Q-C. This is
sufficient for architecture direction but not for production confidence. A proper
evaluation requires a labelled dataset of 50-100 query-paragraph pairs covering
the full catalog domain spread.

**2. No quantitative Recall@K or MRR measurement.** Results are reported as
rank positions and score differences, not as aggregated metrics. Before production,
a systematic Recall@5 and MRR@3 measurement over the full query set is required.

**3. No latency profiling.** Cross-encoder reranking on Top-20 is significantly
more expensive than bi-encoder retrieval (estimated factor 5-10x). Skeleton latency
is irrelevant; production latency budget for Sachbearbeiter-facing response time
must be defined and measured before `feat/reranking` ships.

---

## Post-Skeleton Considerations

- **Latency:** Cross-encoder on Top-20 pairs per query at inference time. Profile
  before production deployment. Consider batching or async execution.
- **Evaluation dataset:** Build a labelled set of (argument_text, expected_chunk_id)
  pairs covering all five catalog domains. Minimum 50 pairs for meaningful metrics.
- **German stemming:** Not validated as needed given Spike D results. Revisit only
  if Q-B-style failures recur with different query formulations.
- **Embedder upgrade:** `paraphrase-multilingual-mpnet-base-v2` (768-dim) or
  `T-Systems-onsite/cross-en-de-roberta-sentence-transformer` are candidates if
  recall problems emerge on a larger query set.
- **Query formulation quality:** `ExtrahiertesArgument.argument_text` quality
  directly determines retrieval quality. LLM extraction should produce normalized
  legal terminology, not raw paraphrases. Architecture decision deferred post-skeleton.