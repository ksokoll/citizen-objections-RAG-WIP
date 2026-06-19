## ADR-004: Paragraph-Boundary Chunking

**Status:** Accepted

**Context:** Legal source texts from rechtsinformationen.bund.de are delivered as structured XML with explicit paragraph boundaries. The chunking strategy directly determines citation granularity and therefore the reliability of the post-hoc verification step.

**Decision:** The system chunks legal source texts at paragraph boundaries (§-level), not at fixed token windows. One chunk corresponds to one paragraph or subsection. Chunk metadata carries the canonical paragraph identifier (e.g. `baugb_§3_abs1`) as a structured field.

**Rationale:** Token-window chunking splits paragraphs arbitrarily, producing chunks that contain partial citations and making it impossible to verify whether a generated citation corresponds to a complete, coherent legal provision. Paragraph-boundary chunking ensures that a retrieved chunk is always a self-contained legal unit. This is the prerequisite for the verification logic in ADR-006: a citation can only be verified against a chunk if the chunk represents exactly the legal unit being cited. The XML structure from rechtsinformationen.bund.de makes paragraph-boundary chunking straightforward to implement without heuristics.

**Rejected Alternatives:** Fixed token-window chunking (e.g. 512 tokens with overlap). Rejected because it produces chunks that cross paragraph boundaries, undermining citation integrity. Sentence-level chunking. Rejected because individual sentences from legal texts are rarely self-contained legal provisions.

**Consequences:** The ingestion pipeline requires an XML parser that extracts paragraph structure, not a generic text splitter. Each chunk carries a `paragraph_id` field in canonical form. The canonical form must normalize across citation variants before storage (see ADR-006).