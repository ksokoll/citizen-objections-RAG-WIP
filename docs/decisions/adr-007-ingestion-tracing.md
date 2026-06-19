## ADR-007: Federal Law Data Source Strategy

**Status:** Accepted

**Context:** The retrieval backend requires a verified, machine-readable corpus of German federal law. The system's audit requirements demand that every cited legal norm is traceable to an authoritative source.

**Decision:** Federal law is sourced exclusively from rechtsinformationen.bund.de, which publishes official XML datasets of German federal law under Datenlizenz Deutschland 2.0. No web scraping of gesetze-im-internet.de or other sources is used. Einwendungs documents (incoming objections) are synthetic, generated to cover the predefined catalog argument types. Landesrecht is explicitly out of scope for v1.

**Rationale:** rechtsinformationen.bund.de is the authoritative official source, not a secondary aggregator. The XML structure directly supports paragraph-boundary chunking defined in ADR-004 without heuristic parsing. Datenlizenz Deutschland 2.0 permits use in this context without legal ambiguity, which matters for a portfolio project claiming regulatory compliance awareness. Scraping gesetze-im-internet.de introduces robots.txt risk and produces HTML that requires fragile parsing. Synthetic Einwendungen are sufficient for demonstrating retrieval and drafting quality without requiring access to real citizen data, which would introduce DSGVO obligations for the portfolio project itself.

**Rejected Alternatives:** Scraping gesetze-im-internet.de. Rejected due to legal ambiguity and HTML parsing fragility. Using real Einwendungen documents. Rejected because real citizen documents introduce DSGVO obligations that are disproportionate for a portfolio project.

**Consequences:** The ingestion pipeline includes an XML parser targeting the rechtsinformationen.bund.de schema. The corpus is versioned: the download date and source URL are stored as corpus metadata and included in `retrieval_config_hash`. A synthetic data generation script produces Einwendungen documents covering all predefined catalog entries, in both Typ-1 and Typ-2 variants.