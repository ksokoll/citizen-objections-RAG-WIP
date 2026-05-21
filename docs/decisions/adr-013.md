## ADR-013: Per-Argument Extraction as Processing Unit

**Status:** Accepted

**Context:** A single Einwendung document routinely contains multiple discrete legal arguments spanning different legal domains. Document-level processing forces a single domain onto a multi-domain document and loses the argument structure that determines retrieval and response.

**Decision:** Triage extracts all discrete legal arguments from the document in one structured LLM call. The output is `list[ExtrahiertesArgument]`, each entry containing `argument_text` (normalized for retrieval), `original_zitat` (verbatim quote, per ADR-006), `catalog_id` (from the predefined enum, per ADR-002), and `einwendungs_typ` (TYP_1 or TYP_2, classified per argument). All subsequent pipeline steps (retrieval per ADR-005, generation, verification per ADR-006) operate per argument. ResponseDrafting aggregates the per-argument results into the final `Abwaegungsstellungnahme`.

An empty extraction list is a valid terminal state. The document contains no identifiable legal argument: the pipeline sets `wuerdigungs_status = KEIN_TREFFER` and routes to the Sachbearbeiter for manual processing.

**Rationale:** Per-argument processing matches the structure of an Abwägungsstellungnahme, which legally must address each raised argument individually. A single structured LLM call combines extraction, classification, quote-grounding, and type classification without a second preprocessing roundtrip and with minimal hallucination surface.

**Rejected Alternatives:** Document-level embedding plus single catalog match: cannot represent multi-argument multi-domain documents. Separate LLM calls for extraction, classification, and type identification: doubles or triples latency without benefit when structured output handles all three at once.

**Consequences:** `TriageResult` carries `list[ExtrahiertesArgument]` instead of a single `CatalogMatch`. `Rechtsgrundlage`, `RetrievalMetadata`, and `wuerdigungs_status` exist per argument as well as in derived aggregate form on the `Abwaegungsstellungnahme`. ResponseDrafting owns the aggregation logic. `ExtrahiertesArgument` is added to `core/entities.py`.