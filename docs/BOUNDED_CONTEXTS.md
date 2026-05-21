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
Extract all discrete legal arguments from the clean text, classify each against the predefined catalog, and return a structured argument list for per-argument retrieval and generation.

### 2. Why It's Separate
Argument extraction requires its own LLM call with a domain-specific schema and catalog constraint. The extraction logic, catalog definition, and failure modes are distinct from retrieval and generation. A failed extraction is a `TriageError`; a failed generation is a `GenerationError`. Catalog updates happen here without touching ResponseDrafting.

### 3. Interface Contract

```python
def triage(clean_text: str) -> TriageResult:
    """Extract legal arguments and classify each against the catalog.

    Single structured LLM call returns list[ExtrahiertesArgument].
    Each argument contains a normalized search text, a verbatim
    quote for verification, and a catalog_id from the predefined enum.

    Assumes:
        clean_text has been PII-masked by DocumentIngestion.

    Does NOT check:
        Whether extracted arguments are legally correct.
        Whether a response can be generated for each argument.

    Raises:
        TriageError: If the LLM call fails.
    """
```

### 4. Input / Output

```python
# Input
clean_text: str

# Output
@dataclass
class TriageResult:
    einwendungs_typ: EinwendungsTyp
    extracted_arguments: list[ExtrahiertesArgument]
    # Empty list is valid: TYP_1 document with no legal arguments.
    # Pipeline sets wuerdigungs_status=KEIN_TREFFER.

@dataclass
class ExtrahiertesArgument:
    argument_id: str
    argument_text: str       # normalized for vector search
    original_zitat: str      # verbatim quote for ADR-006 verification
    catalog_id: str | None   # from predefined enum; None triggers NoMatchEvent
```

### 5. Business Rules

1. **Single LLM call for extraction and classification.** Argument extraction, text normalization, and domain classification happen in one structured output call. No second preprocessing roundtrip.
2. **Catalog is a constraint enum, not a matching target.** The LLM chooses `catalog_id` from the predefined set. It cannot invent new domain labels.
3. **`original_zitat` is mandatory per argument.** Every extracted argument must contain a verbatim quote from the source document. Substring check against `clean_text` validates presence. Failed check marks argument as `ARGUMENT_UNVERIFIED`.
4. **Empty extraction list is a valid terminal state.** TYP_1 documents with no identifiable legal argument return `extracted_arguments=[]`. Not a `TriageError`.
5. **Per-argument `NoMatchEvent` does not abort the pipeline.** Arguments with `catalog_id=None` are skipped; remaining arguments are still processed.

*Note: In the skeleton, the LLM call is stubbed. Triage returns a hardcoded `list[ExtrahiertesArgument]` covering the five catalog entries.*

### 6. Error Strategy

- LLM call failure: raise `TriageError` with the upstream exception.
- Empty extraction list: return `TriageResult(extracted_arguments=[], ...)`. Not an error.
- Argument verification failure (`original_zitat` not found): mark argument `ARGUMENT_UNVERIFIED`, continue with remaining arguments.

### 7. Dependencies

**Skeleton:** `anthropic` (stubbed), stdlib.
**feat/triage:** `anthropic` (real LLM call with structured output schema).

### 8. Test Scenarios

| Scenario | Input | Expected Output |
|---|---|---|
| TYP_2 document with three arguments | Legal text citing BauGB and BauNVO | Three `ExtrahiertesArgument` entries with correct `catalog_id` |
| TYP_1 document | Informal complaint, no legal basis | `extracted_arguments=[]`, `einwendungs_typ=TYP_1` |
| Argument with unverifiable zitat | Stub returns quote not in source text | Argument marked `ARGUMENT_UNVERIFIED` |
| LLM call failure | Stubbed to raise exception | `TriageError` raised |
| Mixed match / no-match | Two arguments: one with catalog_id, one without | One argument proceeds, one triggers `NoMatchEvent` |

---

## Context 3: ResponseDrafting

### 1. Responsibility
For each verified extracted argument, retrieve relevant legal norms from the domain-specific corpus, generate a Würdigung, verify all §-references, and aggregate into a single Abwägungsstellungnahme draft.

### 2. Why It's Separate
ResponseDrafting owns the RAG pipeline: per-argument retrieval, LLM generation, and §-reference verification. Its failure modes (retrieval failure, generation failure, verification failure) are independent of argument extraction. The retrieval logic changes at a different rate from classification logic. This is the primary learning context for the portfolio.

### 3. Interface Contract

```python
def draft(
    triage_result: TriageResult,
    clean_text: str,
    document_id: str,
) -> Abwaegungsstellungnahme:
    """Retrieve and generate per argument, aggregate into one draft.

    For each ExtrahiertesArgument with a valid catalog_id:
    - Retrieve top-k chunks from the domain-specific corpus
    - Generate a Würdigung grounded in retrieved context
    - Verify all §-references against retrieved chunk_ids

    Assumes:
        triage_result.extracted_arguments is non-empty.
        The norm index is loaded and non-empty.

    Does NOT check:
        Whether arguments were correctly extracted.
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
Abwaegungsstellungnahme  # Always DRAFT. Never APPROVED.
                         # wuerdigungs_status reflects worst-case
                         # across all arguments: if any argument
                         # has an unverified §-reference, the full
                         # Würdigung is UNTERDRUECKT_UNVERIFIED.
```

### 5. Business Rules

1. **ResponseDrafting never produces APPROVED status.**
2. **Processing is per argument.** Each `ExtrahiertesArgument` with a valid `catalog_id` gets its own retrieval call scoped to its domain corpus partition.
3. **Arguments with `catalog_id=None` are skipped.** They were already handled by `NoMatchEvent` in Triage.
4. **§-reference verification per ADR-006.** Any unverified reference sets `wuerdigungs_status=UNTERDRUECKT_UNVERIFIED` for the entire draft.
5. **Reproducibility fields are mandatory.** `model_version`, `prompt_version`, `retrieval_config_hash` must be set on every returned object.
6. **Generation strategy differs by `EinwendungsTyp`.** TYP_1: concise plain-language Würdigung. TYP_2: precise legal Würdigung with explicit norm references. Same retrieval depth, different prompt.

*Note: In the skeleton, LLM is stubbed and returns a fixed string. Verification always passes. Hybrid retrieval (BM25 + RRF) introduced in `feat/hybrid-retrieval`.*

### 6. Error Strategy

- FAISS query failure: raise `RetrievalError`.
- LLM call failure after retries: raise `GenerationError`.
- Unverified §-reference: set `wuerdigungs_status=UNTERDRUECKT_UNVERIFIED`, return object. Valid outcome.
- All arguments skipped (all `catalog_id=None`): return draft with `wuerdigungs_status=KEIN_TREFFER`.

### 7. Dependencies

**Skeleton:** `faiss-cpu`, `numpy`, `openai` (embeddings), `anthropic` (stubbed).
**feat/hybrid-retrieval:** `rank-bm25`.
**feat/verification:** `re` (stdlib).

### 8. Test Scenarios

| Scenario | Input | Expected Output |
|---|---|---|
| Happy path (skeleton stub) | Valid `TriageResult` with one argument | `Abwaegungsstellungnahme` with `status=DRAFT`, all reproducibility fields set |
| Two arguments, different domains | BauGB + BauNVO arguments | Two retrieval calls, results aggregated |
| TYP_1 input | `einwendungs_typ=TYP_1` | Plain-language tone in generated Würdigung |
| TYP_2 input | `einwendungs_typ=TYP_2` | Legal precision tone, explicit norm references |
| Unverified §-reference | LLM output with hallucinated paragraph | `wuerdigungs_status=UNTERDRUECKT_UNVERIFIED` |
| All arguments catalog_id=None | Triage returned only unmatched arguments | `wuerdigungs_status=KEIN_TREFFER` |

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