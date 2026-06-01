# Iteration 14 Plan: NormResolution Bounded Context with Hybrid Retrieval

Status: Pre-registered. Written before implementation per the process changes in LESSONS_LEARNED_EXPERIMENTS.md. Predictions in this document are committed before any measurement.

Date: 2026-05-27

---

## Motivation

The Triage pipeline produces ExtrahiertesArgument entities, each carrying zitierte_normen as canonical citation strings (for example "§ 9 Abs. 1 Nr. 1 WHG"). These citations are identifiers, not content. ResponseDrafting needs the actual Gesetzestext behind each citation to ground its drafted responses. This iteration introduces a NormResolution bounded context that resolves each canonical citation to its source legal text.

## Hypothesis

A hybrid retrieval strategy (exact-match lookup with vector-similarity fallback) resolves extracted norms to the correct Gesetzestext passage with overall recall of at least 95% across the nine-Gesetz whitelist, where "correct" means the retrieved passage contains the cited paragraph's text.

Sub-hypotheses:
- H-A: Exact-match alone resolves 80 to 90% of canonical citations directly, because the norm_extractor produces clean canonical forms that key directly into the paragraph index.
- H-B: Vector fallback recovers the remaining cases, specifically the granularity-drift cases (i.V.m.-synthesised inner citations, citations more specific than the index granularity), lifting overall recall to at least 95%.

## Architecture

New bounded context `src/app/norm_resolution/`, separate from Triage. Triage remains responsible for "what does the citizen argue and which norms are cited". NormResolution is responsible for "what does the cited law actually say".

Layering within the context:

- Domain: `NormWithSource` entity (canonical citation plus resolved source text plus resolution metadata: method used, confidence). `NormResolver` Protocol defining the resolution interface.
- Application: `NormResolutionService` orchestrating exact-match then vector fallback.
- Infrastructure: `GesetzXMLLoader` (parses the nine local XML files into paragraph chunks), `E5Embedder` (wraps multilingual-e5-large with the required query/passage prefixes), `FaissNormIndex` (faiss-cpu vector store over the paragraph chunks).

Resolution flow per norm:

```
canonical citation ("§ 9 Abs. 1 Nr. 1 WHG")
    -> normalise to paragraph key ("§ 9 WHG")
    -> exact-match dict lookup
        hit  -> NormWithSource(method="exact")
        miss -> embed "query: § 9 Abs. 1 Nr. 1 WHG"
             -> faiss top-k over passage embeddings
             -> filter top-k by Gesetz suffix match
             -> NormWithSource(method="vector", confidence=score)
```

The Gesetz-suffix filter on vector results prevents cross-law false matches (a § 9 from BauGB matching a § 9 query for WHG).

## Configurations

- Data source: nine local gesetze-im-internet.de XML files (current Behörde state). No external fetch; the files are the authoritative snapshot.
- Chunk granularity: per paragraph (§). Each § becomes one chunk holding its full text including all Absätze, Sätze, Nummern.
- Embedding model: intfloat/multilingual-e5-large, run locally via sentence-transformers. Asymmetric prefixes applied: "passage: " for indexed chunks, "query: " for citation lookups.
- Vector store: faiss-cpu, IndexFlatIP (inner product on L2-normalised embeddings, equivalent to cosine similarity).
- Retrieval: hybrid. Exact-match first, vector top-3 fallback with Gesetz-suffix filtering.

## Predicted Outcomes

- Exact-match direct-resolution rate: 80 to 90% of canonical norms in the test corpus.
- Vector-fallback recall at k=3: at least 95% on the granularity-drift subset (i.V.m. inner citations, sub-paragraph citations).
- Overall resolution recall: at least 95%.
- Per-norm resolution latency: under 200ms (exact-match sub-millisecond, vector path dominated by embedding compute).
- Index build time: under 30 seconds for the nine Gesetze at paragraph granularity (one-time at startup).

## Smoketest (before full eval)

Per the provider-compatibility lesson: before the full retrieval eval, a single smoketest verifies the e5 model loads, the prefixes are applied, the faiss index builds, and one known citation ("§ 9 WHG") resolves via exact-match and one drift case ("§ 9 Abs. 1 Nr. 1 i.V.m. § 8 WHG" inner) resolves via vector fallback. Sixty seconds, catches setup errors before a full run.

## Measurement

Ground truth for retrieval requires a mapping from each canonical norm in the test corpus to the expected paragraph identifier. This GT is constructed once from the XML files (every § that appears in the must_retrieve sets). Retrieval recall is then measured as: fraction of norms where the resolved passage's paragraph identifier matches the expected one.

An aggregation step produces a per-Gesetz and overall recall table, plus a breakdown of exact-match versus vector-fallback resolution counts.

## Stop Rule

The iteration is complete when:
1. The nine XML files are parsed and indexed at paragraph granularity.
2. The hybrid resolver passes the smoketest.
3. Retrieval recall is measured against the constructed GT with the per-Gesetz and exact-versus-vector breakdown.
4. ADR-020 documents the NormResolution bounded context and the hybrid retrieval decision.

Additional ideas (reranking, finer granularity, cross-encoder rescoring, multi-paragraph context windows) are deferred to a next-iteration backlog and do not extend this iteration.

## Open Risks

- XML structure assumption: the gesetze-im-internet.de format uses norm elements with enbez (Einzelbezeichnung) and textdaten. The parser assumes this structure. If the local files deviate, the loader needs adjustment. To be verified against one actual file before building the full loader.
- e5 prefix correctness: multilingual-e5 requires the query/passage prefixes for asymmetric retrieval. Omitting them degrades retrieval quality silently. The smoketest must confirm prefixes are applied.
- Granularity mismatch direction: exact-match normalises citations down to § level, so a citation more specific than § (Abs/Nr) still resolves to the full § text. This is acceptable for the Behörde use case (the Sachbearbeiter sees the full provision) but means "recall" is measured at § granularity, not sub-paragraph.
- GT construction effort: building the norm-to-paragraph GT is manual-ish work derived from the existing must_retrieve sets and the XML files. Scope it before starting.

## Git-SHA Persistence

Result files for this iteration include the current git commit short-sha in their JSON top level, per the infrastructure lesson. The save helper records it at write time.