## ADR-016: Catalog Refactor — From 7 Thematic Clusters to 9 Gesetz-Based Entries

**Status:** Accepted

**Context:** The Triage milestone left three non-aligned notions of "catalog" in the codebase. First, `app.triage.catalog` defined seven thematic clusters (C-001 to C-007) with a `corpus_partition` field. Of those, only six partition values were distinct (C-001 Bauplanungsrecht and C-007 Verfahrensrecht both routed to the `baugb` partition), and one value (`bimschg_ta_laerm`) referred to a partition that does not exist as an XML in the corpus. Second, `HybridRetrievalService._load_corpora` discovers XMLs by filename and produces one partition per file. With nine XMLs in the corpus, this implicitly defines nine partitions keyed `baugb`, `baunvo`, `bimschg`, `bnatschg`, `enwg`, `vwgo`, `wastrg`, `whg`, `wpg`. Third, the ground-truth files in `experiments/extraction_evaluation/ground_truth/retrieval_gt/*.json` already use these nine law abbreviations in `expected_catalog_ids`.

The Retrieval block (ADR-015 Open Question) had left three options open for bridging `catalog_id` to `partition`: carry partition data on the argument via Triage, inject a `partition_for_catalog` callable from the Coordinator, or move `KATALOG` to `core/`. All three preserved the cluster-to-partition indirection without resolving the underlying drift. The first time `ResponseDraftingService._retrieve` is wired to the real retriever, the aspirational `bimschg_ta_laerm` value would surface as an invalid partition key at runtime — a hidden bomb.

**Decision:** One Katalog = one Gesetz = one Vektorstore. `CatalogId` has nine values, one per law in the corpus (`BAUGB`, `BAUNVO`, `BIMSCHG`, `BNATSCHG`, `ENWG`, `VWGO`, `WASTRG`, `WHG`, `WPG`, with lowercase values). `KatalogEintrag` loses the `corpus_partition` field; `catalog_id` IS the retriever partition key. `ResponseDraftingService._retrieve` passes `argument.catalog_id` directly as `partition` to the retriever, with a defensive guard against `None`.

**Rationale:** (a) The retriever is already gesetz-based via filename discovery in `HybridRetrievalService._load_corpora`. The refactor brings the upstream pipeline and the downstream retriever into agreement instead of papering over the gap with an indirection layer. (b) It eliminates the `catalog_id`-to-partition mapping that the ADR-015 Open Question had three options for. All three are replaced by direct identity, which is the simplest possible mapping and the one that requires no new module or DI wiring. (c) The ground-truth files for retrieval evaluation already use the nine law abbreviations. The refactor aligns the production catalog with the evaluation convention rather than forcing a translation layer at the evaluation boundary.

**Rejected Alternatives:**

Keep the 7-cluster model and introduce a `partition_for_catalog` DI callable from the Coordinator. Would have worked but adds indirection with no semantic gain. The aspirational `bimschg_ta_laerm` naming would still need to be reconciled or removed. The cluster-to-partition mapping would remain 1:1 with partition overload (C-001 and C-007 both pointing to `baugb`), which weakens the granularity of the routing decision: two distinct cluster labels can no longer be distinguished by the retriever, only by post-retrieval logic that does not currently exist.

Cluster-to-partition 1:N mapping (e.g. a Verfahrensrecht cluster routing to both `baugb` and `vwgo`). Would have worked but makes the LLM classification task harder: it becomes multi-label, increasing prompt complexity and reducing model accuracy. The effect on the pipeline is approximately the same as gesetz-based routing but with extra cluster indirection sitting between argument and retrieval.

**Consequences:**

Cluster naming (Bauplanungsrecht, Verfahrensrecht, etc.) disappears from `catalog_id`. The thematic distinction between, e.g., Bauplanungsrecht and Verfahrensrecht now lives in `zitierte_normen` and `argument_text` rather than in the catalog identifier. This is acceptable for the current pipeline because `ResponseDraftingService` uses `catalog_id` only as a filter and as the partition key, not for template selection. If a future feature requires per-Sachgebiet template routing, it can read `zitierte_normen` or be added as an explicit field on `ExtrahiertesArgument`.

`ResponseDraftingService._retrieve` becomes trivial: `partition=argument.catalog_id`. The defensive `RetrievalError` guard for `catalog_id=None` ensures that the upstream filter in `draft()` is enforced even if `_retrieve` is ever called from somewhere else.

The evaluation block (`extraction_evaluation.py`, `norm_extraction_evaluation.py`, `catalog_definition.json`, all result files) must be updated in a separate round. The distractor concept (C-008 / C-009 / C-010 as deliberately unmappable clusters used to measure discrimination) does not translate one-to-one to the gesetz-based model and must be either re-thought or dropped. Those files are deliberately left in an inconsistent state on this branch; the refactor scope ends at the production code path.

The LLM prompt becomes shorter and more concrete: gesetz descriptions replace cluster descriptions that previously listed multiple laws per cluster. This should reduce LLM ambiguity for arguments that cite a single law.
