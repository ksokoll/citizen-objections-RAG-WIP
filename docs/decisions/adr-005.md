## ADR-005: Per-Argument Domain-Routed Retrieval

**Status:** Accepted (revised)

**Context:** Per ADR-013, retrieval operates per extracted argument. The legal corpus is partitioned by legal domain (BauGB, BImSchG, etc.). Each `catalog_id` maps to exactly one partition via the catalog (ADR-002).

**Decision:** For each `ExtrahiertesArgument` with non-null `catalog_id`, retrieval is filtered to the corpus partition derived from the catalog. If `catalog_id` is `None`, retrieval falls back to the full corpus for that argument only. Other arguments in the same document are unaffected by either case. The `KEIN_TREFFER` audit event from ADR-002 is independent of this fallback: the event records the classification miss, the fallback ensures the argument is still processed.

**Rationale:** Per-argument routing matches the per-argument processing pattern. Catalog-derived partitioning is deterministic, no classifier confidence threshold is needed. Full-corpus fallback on `None` ensures no argument is silently dropped while keeping the precision benefit for matched arguments.

**Rejected Alternatives:** Document-level routing: cannot handle multi-domain documents. Parallel retrieval across all partitions per argument: redundant when the catalog already determines the partition.

**Consequences:** `RetrievalMetadata` is per argument, not per document. The domain partition and fallback flag are logged per argument in the AuditLog. The confidence threshold and full-corpus fallback condition from the previous ADR-005 version are removed.