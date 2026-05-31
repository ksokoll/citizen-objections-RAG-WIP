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
└──┬───────────┬───────────┬───────────┬────────┘
   │           │           │           │
   ▼           ▼           ▼           ▼
┌────────┐ ┌────────┐ ┌──────────┐ ┌──────────┐
│Document│ │ Triage │ │Retrieval │ │ Response │
│Ingest. │ │        │ │          │ │ Drafting │
└────────┘ └────────┘ └──────────┘ └──────────┘
                                         │
                         ┌───────────────┘
                         ▼
                   ┌──────────┐
                   │ AuditLog │
                   └──────────┘
```

**Dependency rule:** Coordinator imports from all five BCs. No BC imports from another BC. AuditLog has no imports from any other BC. All external I/O (LLM, FAISS, embedding model, file system) sits behind Protocols defined in `core/protocols.py`.

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
    zitierte_normen: list[str]  # canonical norm citations for Retrieval
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
**feat/triage:** `openai` or `mistralai` (real LLM call with structured output schema).

### 8. Test Scenarios

| Scenario | Input | Expected Output |
|---|---|---|
| TYP_2 document with three arguments | Legal text citing BauGB and BauNVO | Three `ExtrahiertesArgument` entries with correct `catalog_id` |
| TYP_1 document | Informal complaint, no legal basis | `extracted_arguments=[]`, `einwendungs_typ=TYP_1` |
| Argument with unverifiable zitat | Stub returns quote not in source text | Argument marked `ARGUMENT_UNVERIFIED` |
| LLM call failure | Stubbed to raise exception | `TriageError` raised |
| Mixed match / no-match | Two arguments: one with catalog_id, one without | One argument proceeds, one triggers `NoMatchEvent` |

---

## Context 3: Retrieval

### 1. Responsibility
For each canonical norm citation produced by Triage, resolve the citation to its source Gesetzestext passage. Hybrid resolution: exact-match lookup on the paragraph-level key first, vector-similarity fallback over the statute corpus for granularity-drift cases.

### 2. Why It's Separate
Resolving a citation to its source law has a distinct change axis from both argument extraction and generation. The statute corpus, the retrieval strategy, and the embedding model change at a different rate from classification logic (Triage) or generation logic (ResponseDrafting). A failed resolution is a `RetrievalError`, distinct from `TriageError` and `GenerationError`. Keeping retrieval separate lets Triage and ResponseDrafting mock the `Retriever` Protocol in their unit tests, so neither acquires a dependency on the vector index, the embedding model, or the statute XML. The retrieval strategy can be swapped without touching the contexts on either side. See ADR-020.

### 3. Interface Contract

```python
def resolve(citations: list[str]) -> list[NormWithSource]:
    """Resolve canonical norm citations to their source Gesetzestext.

    For each citation, attempt exact-match lookup on the paragraph-level
    key, then fall back to vector similarity with Gesetz-suffix filtering.

    Assumes:
        The statute index is loaded and non-empty.
        Citations are canonical strings from Triage
        (e.g. "§ 9 Abs. 1 Nr. 1 WHG").

    Does NOT check:
        Whether the citation is legally meaningful.
        Whether the cited paragraph is the correct one for the argument.

    Raises:
        RetrievalError: If the index is not loaded or the vector
            query fails.
    """
```

### 4. Input / Output

```python
# Input
citations: list[str]  # canonical norm strings from Triage's zitierte_normen

# Output
list[NormWithSource]

@dataclass(frozen=True)
class NormWithSource:
    canonical_citation: str    # "§ 9 Abs. 1 Nr. 1 WHG" (as cited)
    paragraph_key: str         # "§ 9 WHG" (resolved paragraph level)
    source_text: str           # full Gesetzestext of the paragraph
    method: str                # "exact" | "vector"
    confidence: float | None   # cosine score for vector, None for exact
    resolved: bool             # False if neither method found a match
```

### 5. Business Rules

1. **Hybrid resolution order is fixed.** Exact-match on the paragraph-level key is attempted first. Vector-similarity fallback runs only on an exact-match miss. Exact wins because it is unambiguous and sub-millisecond.
2. **Exact-match normalises the citation to paragraph level.** "§ 9 Abs. 1 Nr. 1 WHG" is reduced to the key "§ 9 WHG" for lookup. A citation more specific than a paragraph still resolves to the full paragraph text. Resolution granularity is the paragraph, not the Absatz or Nummer.
3. **Vector fallback filters by Gesetz suffix.** The top-k candidates are restricted to the cited statute, preventing a § 9 from one law matching a § 9 query for another.
4. **The corpus is the nine local XML files.** They represent the current Behörde state. One loader, one index, single source of truth for legal text. No external fetch at resolution time.
5. **An unresolved citation is a valid outcome.** A citation that matches neither exactly nor by vector returns `NormWithSource(resolved=False)`. This is not a `RetrievalError`. Downstream decides how to handle an unresolved norm.
6. **Resolution is deterministic.** Exact-match is dictionary lookup. Vector embedding runs at temperature-free inference. The same citation against the same corpus resolves identically across runs.

*Note: In the skeleton, the index can be built from a small fixture corpus. The full nine-statute index builds at startup from the XML directory.*

### 6. Error Strategy

- Index not loaded or empty at resolve time: raise `RetrievalError`.
- Embedding model load failure (startup): raise `RetrievalError`.
- Vector query failure: raise `RetrievalError`.
- Unresolved citation: return `NormWithSource(resolved=False)`. Valid outcome, not an error.
- Empty citation list: return `[]`.

### 7. Dependencies

**This iteration:** `faiss-cpu`, `numpy`, `sentence-transformers` (multilingual-e5-large), stdlib (`xml.etree`, `pathlib`, `re`).

### 8. Test Scenarios

| Scenario | Input | Expected Output |
|---|---|---|
| Exact-match hit | `["§ 9 WHG"]` | `NormWithSource(method="exact", resolved=True)` |
| Sub-paragraph citation | `["§ 9 Abs. 1 Nr. 1 WHG"]` | Resolves to `§ 9 WHG` paragraph text, `method="exact"` |
| Vector fallback (i.V.m. inner) | citation not keyed in index | `method="vector"`, `confidence` set, `resolved=True` |
| Gesetz-suffix isolation | `["§ 9 WHG"]` with a § 9 BauGB in corpus | Resolves to WHG, never the BauGB § 9 |
| Unresolved citation | citation for a paragraph absent from corpus | `NormWithSource(resolved=False)` |
| Empty input | `[]` | `[]` |
| Index not loaded | resolve called before build | `RetrievalError` raised |

---

## Context 4: ResponseDrafting

### 1. Responsibility
For each verified extracted argument, generate a Würdigung grounded in the resolved Gesetzestext supplied by Retrieval, verify all §-references, and aggregate into a single Abwägungsstellungnahme draft. Optionally retrieve additional semantic context chunks for generation, distinct from the deterministic citation resolution performed by the Retrieval context.

### 2. Why It's Separate
ResponseDrafting owns generation and §-reference verification. Its failure modes (generation failure, verification failure) are independent of citation resolution (Retrieval) and argument extraction (Triage). The generation logic and prompt strategy change at a different rate from the retrieval strategy. This is the primary generation context for the portfolio.

The deterministic resolution of cited norms to their source text was extracted into the Retrieval context (ADR-020). ResponseDrafting consumes the resolved norms rather than performing that resolution itself. Any semantic context retrieval that ResponseDrafting still performs is generation-support retrieval (finding related passages to ground the LLM), conceptually distinct from the citation-to-text resolution that Retrieval owns.

### 3. Interface Contract

```python
def draft(
    triage_result: TriageResult,
    resolved_norms: list[NormWithSource],
    clean_text: str,
    document_id: str,
) -> Abwaegungsstellungnahme:
    """Generate per argument grounded in resolved norms, aggregate into one draft.

    For each ExtrahiertesArgument with a valid catalog_id:
    - Take the resolved Gesetzestext for the argument's cited norms
      from resolved_norms (produced by the Retrieval context)
    - Generate a Würdigung grounded in that source text
    - Verify all §-references against the resolved norms

    Assumes:
        triage_result.extracted_arguments is non-empty.
        resolved_norms covers the citations of the processed arguments.

    Does NOT check:
        Whether arguments were correctly extracted.
        Whether citations were correctly resolved.
        Whether the Sachbearbeiter will approve the draft.

    Raises:
        GenerationError: If the LLM call fails after retries.
    """
```

### 4. Input / Output

```python
# Input
triage_result: TriageResult
resolved_norms: list[NormWithSource]  # from the Retrieval context
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
2. **Processing is per argument.** Each `ExtrahiertesArgument` with a valid `catalog_id` is generated using the resolved Gesetzestext for its cited norms.
3. **Arguments with `catalog_id=None` are skipped.** They were already handled by `NoMatchEvent` in Triage.
4. **§-reference verification per ADR-006.** Any unverified reference sets `wuerdigungs_status=UNTERDRUECKT_UNVERIFIED` for the entire draft. Verification is against the resolved norms from Retrieval.
5. **Reproducibility fields are mandatory.** `model_version`, `prompt_version`, `retrieval_config_hash` must be set on every returned object.
6. **Generation strategy differs by `EinwendungsTyp`.** TYP_1: concise plain-language Würdigung. TYP_2: precise legal Würdigung with explicit norm references. Same source depth, different prompt.
7. **An unresolved norm degrades its argument.** If an argument's cited norm came back with `resolved=False` from Retrieval, the argument cannot be grounded and its §-reference verification fails, setting the draft status accordingly.

*Note: In the skeleton, LLM is stubbed and returns a fixed string. Verification always passes.*

### 6. Error Strategy

- LLM call failure after retries: raise `GenerationError`.
- Unverified §-reference: set `wuerdigungs_status=UNTERDRUECKT_UNVERIFIED`, return object. Valid outcome.
- Unresolved norm for an argument: the argument's verification fails, draft status reflects it. Not a `GenerationError`.
- All arguments skipped (all `catalog_id=None`): return draft with `wuerdigungs_status=KEIN_TREFFER`.

### 7. Dependencies

**Skeleton:** `anthropic` (stubbed).
**feat/generation:** `openai` or `mistralai` (real LLM call), `re` (stdlib, §-reference verification).

### 8. Test Scenarios

| Scenario | Input | Expected Output |
|---|---|---|
| Happy path (skeleton stub) | Valid `TriageResult` + resolved norms for one argument | `Abwaegungsstellungnahme` with `status=DRAFT`, all reproducibility fields set |
| Two arguments, different norms | BauGB + BauNVO arguments with resolved norms | Two generations, results aggregated |
| TYP_1 input | `einwendungs_typ=TYP_1` | Plain-language tone in generated Würdigung |
| TYP_2 input | `einwendungs_typ=TYP_2` | Legal precision tone, explicit norm references |
| Unverified §-reference | LLM output with hallucinated paragraph | `wuerdigungs_status=UNTERDRUECKT_UNVERIFIED` |
| Unresolved norm input | argument whose norm has `resolved=False` | argument's verification fails, draft status reflects it |
| All arguments catalog_id=None | Triage returned only unmatched arguments | `wuerdigungs_status=KEIN_TREFFER` |

---

## Context 5: AuditLog

### 1. Responsibility
Record an append-only trace of the pipeline. After each BC step the Coordinator emits an `AuditEvent`; AuditLog persists it. The log must remain writable even when other contexts fail, so that failures are themselves recorded.

### 2. Why It's Separate
AuditLog has a different failure mode from the domain contexts: a write failure must not suppress the domain result. It has a different persistence contract (append-only, no updates or deletes) and a different lifecycle (it must be writable precisely when other BCs fail). These three independent Disintegrators justify the boundary rather than folding audit into the Coordinator.

### 3. Interface Contract

```python
def append(event: AuditEvent) -> None:
    """Append a single audit event to the store.

    Append-only: an event_id may be written at most once.

    Assumes:
        The audit store is writable.

    Raises:
        AuditLogError: If the store write fails or the event_id
            already exists.
    """

def query(
    einwendungs_id: str | None = None,
    wuerdigungs_status: str | None = None,
) -> list[AuditEvent]:
    """Return events matching the given filters, in write order."""
```

### 4. Input / Output

```python
# Input (append)
event: AuditEvent

# Output (query)
list[AuditEvent]  # in write order
```

### 5. Business Rules

1. **Append-only.** Events are never updated or deleted. An `event_id` is unique; a second write of the same ID is an error.
2. **Write failure does not suppress domain results.** An `AuditLogError` is logged at ERROR level; the pipeline result is returned to the caller regardless.
3. **Writable on failure.** AuditLog must accept events that record the failure of other contexts. It cannot depend on any domain context succeeding.

### 6. Error Strategy

- Store write failure: raise `AuditLogError`. The Coordinator logs it at ERROR and returns the pipeline result regardless.
- Duplicate `event_id`: raise `AuditLogError` on the second write.

### 7. Dependencies

**Skeleton:** stdlib (`json`, `pathlib`). No external services.

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
pipeline.py (Coordinator)  →  Retrieval           ✅
pipeline.py (Coordinator)  →  ResponseDrafting    ✅
pipeline.py (Coordinator)  →  AuditLog            ✅
DocumentIngestion          →  Triage              ❌
Triage                     →  Retrieval           ❌
Retrieval                  →  ResponseDrafting    ❌
Any BC                     →  pipeline.py         ❌
```

### Data Flow

BCs communicate only through input arguments and return values passed through the Coordinator. No shared state. No global singletons. The Coordinator owns the flow; the BCs own the logic. The Coordinator calls Triage, collects the canonical citations from the extracted arguments, passes them to Retrieval, then passes the resolved norms to ResponseDrafting.

### Error Propagation

```
IngestionError    →  Pipeline aborted, AuditEvent(INGESTION_FAILED) emitted
TriageError       →  Pipeline aborted, AuditEvent(TRIAGE_FAILED) emitted
NoMatchEvent      →  Pipeline terminated (valid), AuditEvent(NO_MATCH) emitted
RetrievalError    →  Pipeline aborted, AuditEvent(RETRIEVAL_FAILED) emitted
GenerationError   →  Pipeline aborted, AuditEvent(GENERATION_FAILED) emitted
AuditLogError     →  Logged at ERROR, pipeline result returned regardless
```

Note: `RetrievalError` originates from the Retrieval context (citation resolution). An unresolved citation is not a `RetrievalError`; it is a valid `NormWithSource(resolved=False)` that degrades the downstream argument in ResponseDrafting.

### Testing Strategy

```
Unit tests:         Each BC in isolation. All Protocols replaced with Fakes.
                    No network, no file system (use tmp_path fixture for AuditLog).
Integration tests:  Coordinator with real BC implementations and Fake Protocols.
E2E smoke test:     Full pipeline with LLM stub. One test. Asserts DRAFT returned.
```

---

## Design Decisions

**Why is AuditLog a separate BC and not a Coordinator service?**
AuditLog could be a service layer within the Coordinator rather than a separate BC. Separation was chosen because AuditLog has a different failure mode (write failure must not suppress domain results), a different persistence contract (append-only), and a different lifecycle (it must be writable when other BCs fail). These are three independent Disintegrators that justify the boundary.

**Why is Retrieval separate from ResponseDrafting?**
Citation resolution (norm to source text) and generation-context retrieval are two different steps with two different change axes. Resolution is deterministic (exact-match plus vector fallback over a fixed statute corpus). Generation is stochastic (LLM call with a prompt strategy). Binding them would couple the statute corpus and embedding model to the generation prompt logic. Separating them lets ResponseDrafting mock the resolved norms in its tests and lets the retrieval strategy evolve independently. See ADR-020.

**Why is the Coordinator synchronous?**
The Sachbearbeiter does not wait on a queue: they submit a document and expect a draft. The end-to-end pipeline is fast enough for synchronous execution (LLM call dominates latency). Async would add complexity without reducing wait time for the user. ADR candidate if throughput requirements change.

**Why does ResponseDrafting receive `TriageResult` rather than just `CatalogMatch`?**
`ResponseDrafting` needs `einwendungs_typ` to select the generation strategy (TYP_1 vs. TYP_2 prompt). Passing the full `TriageResult` avoids a second parameter and keeps the contract stable if Triage later adds fields that ResponseDrafting needs. The alternative (passing only `CatalogMatch` and `EinwendungsTyp` separately) creates two parameters that always travel together, which is a sign they belong in one object.