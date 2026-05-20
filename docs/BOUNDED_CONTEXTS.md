# Bounded Contexts — citizen-objections-RAG

---

## Context Map

```
┌──────────────────────────────────────────────┐
│              FastAPI Entry Point             │
│              (feat/fastapi branch)           │
└─────────────────────┬────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────┐
│              pipeline.py (Coordinator)       │
│  - Sequential BC orchestration               │
│  - Protocol dependency injection             │
│  - AuditEvent emission after each step       │
│  - Failure routing and status mapping        │
└──┬───────────────┬──────────────┬────────────┘
   │               │              │
   ▼               ▼              ▼
┌──────────┐  ┌─────────┐  ┌──────────────┐
│ Document │  │  Triage │  │   Response   │
│Ingestion │  │         │  │   Drafting   │
└──────────┘  └─────────┘  └──────────────┘
                                    │
                    ┌───────────────┘
                    ▼
              ┌──────────┐
              │ AuditLog │
              └──────────┘
```

**Dependency rule:** Coordinator imports from all four BCs. No BC imports from another BC. AuditLog has no imports from any other BC. All external I/O (LLM, FAISS, file system) sits behind Protocols defined in `core/protocols.py`.

---

## Context 1: DocumentIngestion

### 1. Responsibility
Accept a raw text document, mask all personally identifiable information, and return clean text ready for downstream processing.

### 2. Why It's Separate
PII masking has a distinct failure mode from business logic: a masking failure is a compliance failure, not a domain failure. The masking patterns change at a different rate from retrieval or generation logic. The GDPR compliance boundary must be enforced at a single, clearly identifiable layer. Any future extension (PDF parsing, OCR, multimodal input) extends this context without touching the others.

### 3. Interface Contract

```python
def ingest(raw_text: str) -> IngestionResult:
    """Accept raw document text and return masked clean text.

    Applies PII masking as the final step before any data
    passes to downstream contexts. Stores the original raw
    text in the access-controlled document store.

    Assumes:
        raw_text is a non-empty string.
        The raw document store is writable.

    Does NOT check:
        Whether the text is a valid Einwendung.
        Whether the text is in German.
        Whether the text matches any catalog entry.

    Raises:
        IngestionError: If raw_text is empty or the document
            store write fails.
    """
```

### 4. Input / Output

```python
# Input
raw_text: str  # Plain text in skeleton; PDF bytes in feat/pii-masking

# Output
@dataclass
class IngestionResult:
    clean_text: str          # PII-masked text for downstream use
    document_id: str         # UUID assigned at ingestion time
    raw_document_path: str   # Path in access-controlled store
```

### 5. Business Rules

1. **PII masking is mandatory before handoff.** No personally identifiable information (names, addresses, email addresses, phone numbers) may appear in `clean_text`. Violation is a GDPR breach, not a degraded result.
2. **Raw document is retained.** The original unmasked document is stored separately under `document_id`. Retention satisfies public authority archival obligations (VwVfG §71a et seq.).
3. **document_id is immutable.** Once assigned at ingestion, the ID travels with the document through all BCs and into AuditLog.

*Note: PII masking logic (regex pass + LLM pass) is introduced in `feat/pii-masking`. In the skeleton, this context is a pass-through: `clean_text == raw_text`.*

### 6. Error Strategy

- Empty input: raise `IngestionError("raw_text must not be empty")`.
- Document store write failure: raise `IngestionError` with the underlying OS error.
- No expected negative outcomes: every non-empty document can be ingested. Low-quality text is a Triage concern, not an ingestion concern.

### 7. Dependencies

**Skeleton:** none beyond stdlib (`uuid`, `pathlib`).
**feat/pii-masking:** regex (stdlib), `anthropic` (LLM PII pass).

### 8. Test Scenarios

| Scenario | Input | Expected Output |
|---|---|---|
| Happy path | Plain text string | `IngestionResult` with `document_id` set |
| Empty string | `""` | `IngestionError` raised |
| PII present (post-masking branch) | Text with name and address | `clean_text` contains no PII, raw stored separately |

---

## Context 2: Triage

### 1. Responsibility
Extract the core legal arguments from the clean text, match them against the predefined catalog, and classify the objection as TYP_1 or TYP_2.

### 2. Why It's Separate
Catalog matching requires its own embedding index and its own update cycle: new catalog entries can be added without touching retrieval or generation. The Triage domain model (catalog entries, match confidence, objection type) changes at a different rate and requires different domain expertise (administrative law, not NLP) than ResponseDrafting. Failure modes differ: a failed catalog match is a `NoMatchEvent`, not an error; a failed LLM call in generation is a different class of failure.

### 3. Interface Contract

```python
def triage(clean_text: str) -> TriageResult:
    """Extract arguments and match against the predefined catalog.

    Embeds the clean_text, computes cosine similarity against
    all catalog entry embeddings, and returns a match if any
    entry exceeds the confidence threshold.

    Assumes:
        clean_text has been PII-masked by DocumentIngestion.
        The catalog index is loaded and non-empty.

    Does NOT check:
        Whether the text is coherent or well-formed.
        Whether the matched legal domain is correct.
        Whether a response can be generated.

    Raises:
        TriageError: If the embedding model call fails.
    """
```

### 4. Input / Output

```python
# Input
clean_text: str

# Output
@dataclass
class TriageResult:
    catalog_match: CatalogMatch | None  # None triggers NoMatchEvent in pipeline
    einwendungs_typ: EinwendungsTyp
    extracted_arguments: str            # LLM-extracted argument summary
    triage_confidence: float
```

### 5. Business Rules

1. **Catalog is predefined and maintained externally.** The Triage context never creates new catalog entries autonomously. New entries require a deliberate catalog update workflow (out of scope for skeleton).
2. **Match stage must be recorded.** Every `CatalogMatch` carries `match_stage: Literal["embedding", "llm_fallback"]` so downstream analysis can track how often the fallback path is used.
3. **No match is a valid outcome, not an error.** When no catalog entry exceeds `catalog_match_threshold` and the LLM fallback also fails, `TriageResult.catalog_match` is `None`. The Coordinator handles this by emitting a `NoMatchEvent` and terminating the pipeline without generating a draft.
4. **TYP_1 / TYP_2 classification is orthogonal to catalog match.** A single catalog entry (e.g. Umweltschutz) can receive both TYP_1 (informal paraphrase) and TYP_2 (formal legal citation) objections. The classification determines generation strategy, not retrieval domain.
5. **Minimum five catalog entries required.** Two entries cannot verify that matching actually discriminates. The skeleton ships with five entries covering: Umweltschutz, Schwerlastverkehr, Lärmschutz, Verfahrensformalitäten, Artenschutz.

*Note: LLM classification fallback is introduced in `feat/domain-routing`. In the skeleton, only embedding similarity matching is active.*

### 6. Error Strategy

- Embedding model failure: raise `TriageError` with the upstream exception.
- Empty catalog: raise `TriageError("Catalog index is empty")` at init time, not at match time.
- No match found: return `TriageResult(catalog_match=None, ...)`. Not an error.

### 7. Dependencies

**Skeleton:** `faiss-cpu`, `numpy`, `openai` (embeddings), `sentence-transformers` (multilingual model for German text).
**feat/domain-routing:** `anthropic` (LLM classification fallback).

### 8. Test Scenarios

| Scenario | Input | Expected Output |
|---|---|---|
| Clear match | Text about wind turbine noise | `CatalogMatch` for Lärmschutz, `match_stage="embedding"` |
| Ambiguous input | Vague text with no clear legal argument | `catalog_match=None` |
| Each of the five catalog themes | Representative text per theme | Correct catalog entry matched |
| Confidence below threshold | Text unrelated to any catalog entry | `catalog_match=None` |
| TYP_1 classification | Informal, emotional text | `einwendungs_typ=TYP_1` |
| TYP_2 classification | Text citing specific §-references | `einwendungs_typ=TYP_2` |

---

## Context 3: ResponseDrafting

### 1. Responsibility
Retrieve relevant legal norms from the Bundesrecht corpus and generate a structured Abwägungsstellungnahme draft, with post-hoc verification of all §-references against the retrieved sources.

### 2. Why It's Separate
ResponseDrafting has the highest infrastructure complexity in the system: it owns two retrieval indexes (catalog and norms), an LLM generation call, and a verification pass. Its failure modes are distinct from Triage: retrieval failure, generation failure, and verification failure are three independent failure classes. The RAG retrieval logic (chunking strategy, hybrid retrieval, RRF fusion) changes at a different rate from argument extraction. This is also the primary learning context for the portfolio: keeping it separate makes the RAG architecture visible and independently testable.

### 3. Interface Contract

```python
def draft(
    triage_result: TriageResult,
    clean_text: str,
    document_id: str,
) -> Abwaegungsstellungnahme:
    """Retrieve legal norms and generate a draft Abwägungsstellungnahme.

    Performs dense retrieval against the Bundesrecht corpus,
    calls the LLM with retrieved context, verifies all
    §-references in the output, and returns a fully populated
    Abwaegungsstellungnahme in DRAFT status.

    Assumes:
        triage_result.catalog_match is not None (checked by Coordinator).
        The norm index is loaded and non-empty.

    Does NOT check:
        Whether the objection was correctly classified.
        Whether the Sachbearbeiter will approve the draft.

    Raises:
        RetrievalError: If the FAISS index query fails.
        GenerationError: If the LLM call fails after retries.
    """
```

### 4. Input / Output

```python
# Input
triage_result: TriageResult
clean_text: str
document_id: str

# Output
Abwaegungsstellungnahme  # Always in DRAFT status. Never APPROVED.
                         # wuerdigungs_status may be GENERIERT or
                         # UNTERDRUECKT_UNVERIFIED depending on
                         # verification outcome.
```

### 5. Business Rules

1. **ResponseDrafting never produces APPROVED status.** The `apply_freigabe()` transition is the Sachbearbeiter's responsibility, not this context's.
2. **All §-references must be verified against retrieved chunk IDs.** Any reference not found in the retrieved chunks sets `verified=False` on the corresponding `Rechtsgrundlage`.
3. **Hard Failure on any unverified Rechtsgrundlage.** If any `Rechtsgrundlage.verified == False`, `wuerdigungs_status` is set to `UNTERDRUECKT_UNVERIFIED` and `rechtliche_wuerdigung` and `abwaegungsergebnis` are set to `None`. See ADR-006.
4. **Reproducibility fields are mandatory.** `model_version`, `prompt_version`, and `retrieval_config_hash` must be set on every returned `Abwaegungsstellungnahme`. No field may be `None` or an empty string.
5. **The LLM may only cite norms present in the retrieval context.** The generation prompt explicitly prohibits citation of norms not in the provided context. Post-hoc verification catches any violation.
6. **Generation strategy differs by EinwendungsTyp.** TYP_1 receives a concise, plain-language Würdigung. TYP_2 receives a precise legal Würdigung with explicit norm references. Same retrieval depth, different prompt.

*Note: Verification logic and Hard Failure routing are introduced in `feat/verification`. The LLM stub in the skeleton returns a fixed string and produces no §-references, so `wuerdigungs_status` is always `GENERIERT` in skeleton runs. Hybrid retrieval (BM25 + RRF) is introduced in `feat/hybrid-retrieval`.*

### 6. Error Strategy

- FAISS query failure: raise `RetrievalError`.
- LLM call failure after retries: raise `GenerationError`.
- Verification failure (unverified §-reference): not an error. Set `wuerdigungs_status=UNTERDRUECKT_UNVERIFIED`, return the object. This is a valid, expected outcome.
- No norms retrieved (empty result set): return draft with `rechtsgrundlagen=[]` and `wuerdigungs_status=UNTERDRUECKT_UNVERIFIED`.

### 7. Dependencies

**Skeleton:** `faiss-cpu`, `numpy`, `openai` (embeddings), `anthropic` (LLM generation, stubbed in skeleton).
**feat/hybrid-retrieval:** `rank-bm25`.
**feat/verification:** `re` (stdlib, §-reference normalization).

### 8. Test Scenarios

| Scenario | Input | Expected Output |
|---|---|---|
| Happy path (skeleton stub) | Valid `TriageResult`, short clean text | `Abwaegungsstellungnahme` with `status=DRAFT`, all reproducibility fields set |
| TYP_1 input | `einwendungs_typ=TYP_1` | Draft generated with plain-language tone marker |
| TYP_2 input | `einwendungs_typ=TYP_2` | Draft generated with legal precision tone marker |
| Unverified §-reference (post-verification branch) | LLM output containing a hallucinated paragraph | `wuerdigungs_status=UNTERDRUECKT_UNVERIFIED`, `rechtliche_wuerdigung=None` |
| Empty retrieval result | Norm index returns zero chunks | `wuerdigungs_status=UNTERDRUECKT_UNVERIFIED` |
| Reproducibility fields | Any valid input | `model_version`, `prompt_version`, `retrieval_config_hash` all non-empty |

---

## Context 4: AuditLog

### 1. Responsibility
Persist typed domain events from all other BCs in an append-only store, and expose a query interface for audit and investigation.

### 2. Why It's Separate
AuditLog has a fundamentally different persistence contract from all other contexts: writes are immutable by design and must never be rolled back, modified, or deleted. It has no dependency on domain logic and must remain writable even when other BCs fail. Keeping it separate means a ResponseDrafting failure does not prevent the audit event from being recorded. The regulatory retention requirement (Aufbewahrungspflicht) applies to AuditLog independently of whether the pipeline completed successfully.

### 3. Interface Contract

```python
def publish(event: AuditEvent) -> None:
    """Append a domain event to the audit store.

    Writes are append-only. Calling publish() on an already-recorded
    event_id raises AuditLogError rather than silently overwriting.

    Assumes:
        The store is writable (file system or future DB).

    Does NOT check:
        Whether the event content is valid domain data.
        Whether the pipeline completed successfully.

    Raises:
        AuditLogError: If the store write fails or if event_id
            already exists in the store.
    """

def query(
    einwendungs_id: str | None = None,
    wuerdigungs_status: WuerdigungsStatus | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
) -> list[AuditEvent]:
    """Return events matching the given filters.

    Returns an empty list if no events match. Never raises on
    an empty result.

    Raises:
        AuditLogError: If the store read fails.
    """
```

### 4. Input / Output

```python
# Input to publish()
@dataclass
class AuditEvent:
    event_id: str                    # UUID, set by the emitting context
    event_type: AuditEventType       # Enum: INGESTION, TRIAGE, DRAFT, FREIGABE
    einwendungs_id: str
    timestamp: datetime
    payload: dict[str, Any]          # Context-specific detail

# Output of query()
list[AuditEvent]
```

### 5. Business Rules

1. **Append-only.** No `UPDATE` or `DELETE` operations exist. The file is opened in append mode (`"a"`). Any implementation that allows modification violates the audit contract.
2. **event_id is unique.** Duplicate event IDs raise `AuditLogError`. Idempotent replay requires a deduplication check on write.
3. **AuditLog has no dependency on other BCs.** It does not import from `triage`, `document_ingestion`, or `response_drafting`. It consumes typed event payloads but knows nothing about their meaning.
4. **Every pipeline step emits an event.** The Coordinator is responsible for emitting events at each step. AuditLog does not call into the pipeline; the pipeline calls into AuditLog.
5. **Write failure does not suppress the domain result.** If AuditLog fails to write, the Coordinator logs the failure at ERROR level and continues. The audit failure is separately alertable. The domain result is not discarded because of an audit write failure.

*Note: The skeleton implements AuditLog as a JSON Lines file. A database-backed implementation is deferred to a future branch.*

### 6. Error Strategy

- Store write failure: raise `AuditLogError` with the underlying OS error. The Coordinator catches this, logs at ERROR, and does not re-raise (per Business Rule 5).
- Duplicate event_id: raise `AuditLogError("Duplicate event_id: {event_id}")`.
- Empty query result: return `[]`. Not an error.

### 7. Dependencies

**Skeleton:** stdlib only (`json`, `pathlib`, `datetime`, `uuid`).

### 8. Test Scenarios

| Scenario | Input | Expected Output |
|---|---|---|
| Append single event | Valid `AuditEvent` | Event written to store, readable via `query()` |
| Append-only semantics | Two events written sequentially | Both events present, order preserved |
| Duplicate event_id | Same `event_id` written twice | `AuditLogError` on second write |
| Query by `einwendungs_id` | Three events, two matching | Two matching events returned |
| Query by `wuerdigungs_status` | Mixed statuses in store | Only matching events returned |
| Store write failure | Store path unwritable | `AuditLogError` raised |

---

## Interaction Rules

### Dependency Direction

```
pipeline.py (Coordinator)  →  DocumentIngestion   ✅
pipeline.py (Coordinator)  →  Triage              ✅
pipeline.py (Coordinator)  →  ResponseDrafting    ✅
pipeline.py (Coordinator)  →  AuditLog            ✅
DocumentIngestion          →  Triage              ❌
Triage                     →  ResponseDrafting    ❌
Any BC                     →  pipeline.py         ❌
```

### Data Flow

BCs communicate only through input arguments and return values passed through the Coordinator. No shared state. No global singletons. The Coordinator owns the flow; the BCs own the logic.

### Error Propagation

```
IngestionError    →  Pipeline aborted, AuditEvent(INGESTION_FAILED) emitted
TriageError       →  Pipeline aborted, AuditEvent(TRIAGE_FAILED) emitted
NoMatchEvent      →  Pipeline terminated (valid), AuditEvent(NO_MATCH) emitted
RetrievalError    →  Pipeline aborted, AuditEvent(RETRIEVAL_FAILED) emitted
GenerationError   →  Pipeline aborted, AuditEvent(GENERATION_FAILED) emitted
AuditLogError     →  Logged at ERROR, pipeline result returned regardless
```

### Testing Strategy

```
Unit tests:         Each BC in isolation. All Protocols replaced with Fakes.
                    No network, no file system (use tmp_path fixture for AuditLog).
Integration tests:  Coordinator with real BC implementations and Fake Protocols.
E2E smoke test:     Full pipeline with LLM stub. One test. Asserts DRAFT returned.
```

---

## Design Decisions

**Why four BCs and not three?**
AuditLog could be a service layer within the Coordinator rather than a separate BC. Separation was chosen because AuditLog has a different failure mode (write failure must not suppress domain results), a different persistence contract (append-only), and a different lifecycle (it must be writable when other BCs fail). These are three independent Disintegrators that justify the boundary.

**Why is the Coordinator synchronous?**
The Sachbearbeiter does not wait on a queue: they submit a document and expect a draft. The end-to-end pipeline is fast enough for synchronous execution (LLM call dominates latency). Async would add complexity without reducing wait time for the user. ADR candidate if throughput requirements change.

**Why does ResponseDrafting receive `TriageResult` rather than just `CatalogMatch`?**
`ResponseDrafting` needs `einwendungs_typ` to select the generation strategy (TYP_1 vs. TYP_2 prompt). Passing the full `TriageResult` avoids a second parameter and keeps the contract stable if Triage later adds fields that ResponseDrafting needs. The alternative (passing only `CatalogMatch` and `EinwendungsTyp` separately) creates two parameters that always travel together, which is a sign they belong in one object.