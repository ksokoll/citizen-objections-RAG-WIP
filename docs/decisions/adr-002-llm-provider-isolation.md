## ADR-002: Predefined Catalog as Classification Constraint

**Status:** Accepted (revised, supersedes ADR-002b)

**Context:** Einwendung documents routinely contain multiple discrete legal arguments spanning multiple legal domains. Per ADR-013, classification happens per argument.

**Decision:** The catalog is encoded as a Pydantic Enum and used as a constraint in the structured output schema of the LLM extraction step in Triage. Each `ExtrahiertesArgument` is classified by selecting one entry from the enum. Arguments that cannot be mapped return `catalog_id = None`. A `None` value emits an `AuditEvent(type=KEIN_TREFFER)` for that argument but does not block the pipeline. Per-argument retrieval behavior on `None` is defined in ADR-005.

**Rationale:** Catalog-as-enum makes every classification one of a finite, defined set that can be validated and reconstructed months later. Free-form LLM labels are neither auditable nor comparable across documents.

**Rejected Alternatives:** Document-level embedding similarity (ADR-002b, superseded): cannot represent multi-argument documents. Free-form LLM labels: not auditable against a known set.

**Consequences:** The catalog is the single source of truth for the mapping `catalog_id` to `rechtsgebiet` to corpus partition. The Pydantic Enum used in the extraction schema is generated from the catalog at build time to avoid drift. A document where all extracted arguments produce `catalog_id = None` results in `wuerdigungs_status = KEIN_TREFFER` on the `Abwaegungsstellungnahme`.