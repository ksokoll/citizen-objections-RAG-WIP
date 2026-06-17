# Bounded Contexts: citizen-objections-RAG

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
│  - AuditEvent emission (fail-closed, ADR-033)│
│  - Failure routing and status mapping        │
└──┬───────────┬───────────┬───────────┬────────┘
   │           │           │           │
   ▼           ▼           ▼           ▼
┌────────┐ ┌────────┐ ┌──────────┐ ┌──────────┐
│Document│ │ Triage │ │Retrieval │ │ Briefing │
│Ingest. │ │        │ │          │ │          │
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
Argument extraction requires its own LLM call with a domain-specific schema and catalog constraint. The extraction logic, catalog definition, and failure modes are distinct from retrieval and generation. A failed extraction is a `TriageError`, distinct from the failure modes of the downstream contexts. Catalog updates happen here without touching Briefing.

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
For each canonical norm citation produced by Triage, resolve the citation to its source Gesetzestext passage. Resolution is exact-match only (ADR-021): a dictionary lookup on the paragraph-level key over the statute corpus. The paragraph-level normalisation absorbs sub-paragraph granularity drift, so no vector fallback is needed in production.

### 2. Why It's Separate
Resolving a citation to its source law has a distinct change axis from both argument extraction and briefing assembly. The statute corpus and the resolution strategy change at a different rate from classification logic (Triage) or the deterministic assembly logic (Briefing). A failed resolution is a `RetrievalError`, distinct from `TriageError`. Keeping retrieval separate lets the contexts on either side mock the `Retriever` Protocol in their unit tests, so neither acquires a dependency on the statute XML or the experimental embedding code. The resolution strategy can be swapped without touching the contexts on either side. See ADR-020 and ADR-021.

### 3. Interface Contract

```python
def resolve(citations: list[str]) -> list[NormWithSource]:
    """Resolve canonical norm citations to their source Gesetzestext.

    For each citation, perform an exact-match lookup on the
    paragraph-level key over the loaded statute corpus.

    Assumes:
        The statute corpus is loaded and non-empty.
        Citations are canonical strings from Triage
        (e.g. "§ 9 Abs. 1 Nr. 1 WHG").

    Does NOT check:
        Whether the citation is legally meaningful.
        Whether the cited paragraph is the correct one for the argument.

    Raises:
        RetrievalError: If the corpus is not loaded.
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
    method: str                # "exact" | "none"
    confidence: float | None   # None for exact-match resolution
    resolved: bool             # False if the key was not in the corpus
```

### 5. Business Rules

1. **Resolution is exact-match only (ADR-021).** The paragraph-level canonical key is looked up in the corpus dictionary. There is no vector fallback in the production path: the measured exact-match recall was 25/25 on the Phase A ground truth, and the vector fallback both resolved zero real citations and produced a confident-wrong match on an out-of-corpus probe.
2. **Exact-match normalises the citation to paragraph level.** "§ 9 Abs. 1 Nr. 1 WHG" is reduced to the key "§ 9 WHG" for lookup. A citation more specific than a paragraph still resolves to the full paragraph text. Resolution granularity is the paragraph, not the Absatz or Nummer. This normalisation absorbs the granularity drift a vector fallback was designed to catch.
3. **The key includes the Gesetz suffix.** "§ 9 WHG" and "§ 9 BauGB" are distinct keys, so a section number in one statute never matches a query for another.
4. **The corpus is the nine local XML files.** They represent the current Behörde state. One loader, one exact-match dictionary, single source of truth for legal text. No external fetch at resolution time.
5. **An unresolved citation is a valid outcome.** A citation whose paragraph-level key is absent from the corpus returns `NormWithSource(resolved=False)` with `method="none"`. This is not a `RetrievalError`. Downstream decides how to handle an unresolved norm.
6. **Resolution is deterministic.** Exact-match is a dictionary lookup. The same citation against the same corpus resolves identically across runs.

*Note: In the skeleton, the dictionary can be built from a small fixture corpus. The full nine-statute corpus builds at startup from the XML directory. The E5Embedder and FaissNormIndex are retained under `experiments/` as reversible experimental reference only; they are not loaded by the production service.*

### 6. Error Strategy

- Corpus not loaded or empty at resolve time: raise `RetrievalError`.
- Unresolved citation: return `NormWithSource(resolved=False)`. Valid outcome, not an error.
- Empty citation list: return `[]`.

### 7. Dependencies

**Production:** stdlib only (`xml.etree`, `pathlib`, `re`). Exact-match resolution needs no embedding model or vector index.
**Experimental reference (not in the production path):** `faiss-cpu`, `numpy`, `sentence-transformers` (multilingual-e5-large), retained under `experiments/` per ADR-021.

### 8. Test Scenarios

| Scenario | Input | Expected Output |
|---|---|---|
| Exact-match hit | `["§ 9 WHG"]` | `NormWithSource(method="exact", resolved=True)` |
| Sub-paragraph citation | `["§ 9 Abs. 1 Nr. 1 WHG"]` | Resolves to `§ 9 WHG` paragraph text, `method="exact"` |
| Gesetz-suffix isolation | `["§ 9 WHG"]` with a § 9 BauGB in corpus | Resolves to WHG, never the BauGB § 9 |
| Unresolved citation | citation for a paragraph absent from corpus | `NormWithSource(resolved=False, method="none")` |
| Empty input | `[]` | `[]` |
| Corpus not loaded | resolve called before build | `RetrievalError` raised |

---

## Context 4: Briefing

### 1. Responsibility
Assemble a per-argument briefing for the Sachbearbeiter. For each extracted argument, pair the argument with the source Gesetzestext of its resolved norms and derive a status, then aggregate the per-argument entries into a single `WuerdigungsBriefing`. This is a deterministic assembly: no LLM call, no generation, no §-reference verification gate. The briefing supports the human assessment; it does not perform it. The Briefing context delivers the structured `WuerdigungsBriefing` as the system's contract; it does not render it. Presentation happens in a frontend beyond the system boundary (ADR-028).

### 2. Why It's Separate
Briefing owns the deterministic assembly of arguments and their resolved norm text into a Sachbearbeiter-facing artifact. Its concern (presentation and status derivation) has a distinct change axis from citation resolution (Retrieval) and argument extraction (Triage). It can evolve the briefing shape and the status rules without touching either neighbour.

The resolution of cited norms to their source text is owned by the Retrieval context (ADR-020). Briefing consumes the resolved norms supplied by the Coordinator rather than performing that resolution itself. Per ADR-022, the final stage produces a deterministic briefing rather than an LLM-generated Würdigung, because the case facts (the Akte) are outside this system's boundary.

### 3. Interface Contract

```python
def assemble(
    document_id: str,
    einwendungs_typ: str,
    arguments: list[dict],
    norms_by_argument: dict[str, list[ResolvedNormEntry]],
    corpus_id: str,
    created_at: datetime,
) -> WuerdigungsBriefing:
    """Assemble a per-argument briefing from arguments and resolved norms.

    For each argument:
    - Pair it with the resolved norm entries for its argument_id
      from norms_by_argument (mapped by the Coordinator from the
      Retrieval context's NormWithSource into ResolvedNormEntry)
    - Derive a BriefingStatus from the catalog match and the
      resolution state of the cited norms
    - Set requires_case_context for entries the Sachbearbeiter must
      still weigh against the Akte

    Assumes:
        arguments is a list of plain dicts with keys argument_id,
        argument_text, original_zitat, einwendungs_typ, catalog_id.
        norms_by_argument maps each argument_id to its resolved norms.
        corpus_id and created_at are supplied by the Coordinator
        (provenance, ADR-028); the context only places them.

    Does NOT check:
        Whether arguments were correctly extracted.
        Whether citations were correctly resolved.
        Whether the cited norm is the legally correct one.

    Returns:
        A WuerdigungsBriefing. Assembly does not fail with a
        generation error; an unresolved norm is a valid status,
        not an exception.
    """
```

### 4. Input / Output

```python
# Input
document_id: str
einwendungs_typ: str                                  # document-level classification
arguments: list[dict]                                 # plain dicts: argument_id,
                                                      # argument_text, original_zitat,
                                                      # einwendungs_typ, catalog_id
norms_by_argument: dict[str, list[ResolvedNormEntry]]  # resolved norms per argument,
                                                      # supplied by the Coordinator
                                                      # (mapped from Retrieval's
                                                      # NormWithSource)

# Output
@dataclass
class WuerdigungsBriefing:
    document_id: str
    einwendungs_typ: str
    corpus_id: str                  # statute-corpus content hash (ADR-028)
    created_at: datetime            # tz-aware UTC creation time (ADR-028)
    entries: list[BriefingEntry]
    limitation_note: str            # states the Akte is outside the boundary

@dataclass
class BriefingEntry:
    argument_id: str
    argument_text: str
    original_zitat: str
    einwendungs_typ: str
    catalog_id: str | None
    norms: list[ResolvedNormEntry]
    status: BriefingStatus
    requires_case_context: bool     # True for every BRIEFING_READY entry

# BriefingStatus values:
#   BRIEFING_READY   catalog match and all cited norms resolved
#   NORM_UNRESOLVED  catalog match but one or more cited norms unresolved
#   KEIN_TREFFER     no catalog match
```

### 5. Business Rules

1. **Assembly is deterministic.** Given the same arguments and resolved norms, Briefing produces the same `WuerdigungsBriefing` every time. There is no LLM call and no stochastic step.
2. **Status is derived, not generated.** For each argument: `KEIN_TREFFER` if there is no catalog match (`catalog_id` is None), else `NORM_UNRESOLVED` if any cited norm came back unresolved from Retrieval, else `BRIEFING_READY`.
3. **`requires_case_context` is always True for a `BRIEFING_READY` entry.** A ready entry pairs the argument with its source law, but the binding assessment still requires the Sachbearbeiter to weigh it against the Akte. The flag signals that the Abwägung is not performed by this system.
4. **No §-reference verification gate.** Briefing does not verify or reject references. It surfaces the resolved norm text alongside the argument and lets the status reflect whether resolution succeeded.
5. **No binding Würdigung is produced (ADR-022).** The case facts (Planunterlagen, Gutachten, Festsetzungen) are outside the system boundary, so the system assembles a briefing rather than a binding Abwägung. The `limitation_note` records this scope decision.
6. **`einwendungs_typ` is document-level context.** It is carried onto the briefing for classification, not used to select a generation strategy.

### 6. Error Strategy

- Briefing assembly is pure: it does not raise a generation error.
- Unresolved norm for an argument: the entry takes `status=NORM_UNRESOLVED`. Valid outcome, not an error.
- Argument with no catalog match (`catalog_id=None`): the entry takes `status=KEIN_TREFFER`. Valid outcome.
- Empty argument list: the Coordinator does not call Briefing for a document with no extracted arguments; it terminates with `KEIN_TREFFER` at the pipeline level.

### 7. Dependencies

**All branches:** stdlib only. No LLM, no embedding model, no vector index. The context defines its own `ResolvedNormEntry` in `app/briefing/entities.py` and imports nothing from Triage or Retrieval.

### 8. Test Scenarios

| Scenario | Input | Expected Output |
|---|---|---|
| Happy path | One argument with catalog_id and all norms resolved | One `BriefingEntry`, `status=BRIEFING_READY`, `requires_case_context=True` |
| Two arguments, different norms | BauGB + BauNVO arguments, all norms resolved | Two entries, both `BRIEFING_READY` |
| Unresolved norm | argument with catalog_id but a norm `resolved=False` | entry `status=NORM_UNRESOLVED` |
| No catalog match | argument with `catalog_id=None` | entry `status=KEIN_TREFFER` |
| Limitation note present | any non-empty briefing | `limitation_note` set, stating the Akte is outside the boundary |
| Determinism | same inputs assembled twice | identical `WuerdigungsBriefing` both times |

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
2. **Custody-event write failure is fail-closed.** A failed custody-event write is logged at ERROR and counted (`audit_write_failures_total`), then re-raised so the run aborts: no result is returned that would imply a complete trail it lacks (ADR-027, armed in ADR-033). A telemetry write failure, outside the custody set, does not abort a run.
3. **Writable on failure.** AuditLog must accept events that record the failure of other contexts. It cannot depend on any domain context succeeding.

### 6. Error Strategy

- Custody-event store write failure: raise `AuditLogError`. The Coordinator logs it at ERROR, increments `audit_write_failures_total`, and re-raises it, so the run aborts fail-closed (ADR-027, armed in ADR-033).
- Duplicate `event_id`: not detected. The store keys the chain off an in-memory head and does not scan per append (ADR-030); the pipeline mints a fresh id per event, so this is a deliberate trade, not a guarantee.

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
pipeline.py (Coordinator)  →  Briefing            ✅
pipeline.py (Coordinator)  →  AuditLog            ✅
DocumentIngestion          →  Triage              ❌
Triage                     →  Retrieval           ❌
Retrieval                  →  Briefing            ❌
Any BC                     →  pipeline.py         ❌
```

### Data Flow

BCs communicate only through input arguments and return values passed through the Coordinator. No shared state. No global singletons. The Coordinator owns the flow; the BCs own the logic. The Coordinator calls Triage, collects the canonical citations from the extracted arguments, passes them to Retrieval, then maps Retrieval's `NormWithSource` into the Briefing context's `ResolvedNormEntry` and passes the per-argument arguments and resolved norms to Briefing.

### Error Propagation

```
IngestionError    →  Pipeline aborted before a document_id exists; no AuditEvent
                     can be keyed, only the terminal metric is recorded (ADR-027)
TriageError       →  Pipeline aborted, AuditEvent(PIPELINE_FEHLER) emitted
NoMatchEvent      →  Pipeline terminated (valid), AuditEvent(KEIN_TREFFER) emitted
RetrievalError    →  Pipeline aborted, AuditEvent(PIPELINE_FEHLER) emitted
AuditLogError     →  Custody-event write failure: logged at ERROR, counted in
                     audit_write_failures_total, then re-raised so the run aborts
                     fail-closed (ADR-027, armed in ADR-033). One type carries it:
                     duplicate detection was removed (ADR-030)
```

Note: `RetrievalError` originates from the Retrieval context (citation resolution). An unresolved citation is not a `RetrievalError`; it is a valid `NormWithSource(resolved=False)` that produces a `NORM_UNRESOLVED` entry in Briefing. Briefing has no generation error: assembly is deterministic and cannot fail with a generation failure.

Note: the read path is also under custody. show-document records a
`ROHDOKUMENT_ZUGRIFF` event in the chain before printing a raw document, and a
failed write aborts the read fail-closed (ADR-033), the read-side analogue of
the completion-before-return ordering.

### Testing Strategy

```
Unit tests:         Each BC in isolation. All Protocols replaced with Fakes.
                    No network, no file system (use tmp_path fixture for AuditLog).
Integration tests:  Coordinator with real BC implementations and Fake Protocols.
E2E smoke test:     Full pipeline with LLM stub. One test. Asserts a
                    WuerdigungsBriefing is returned, with one entry in
                    BRIEFING_READY for the default single-argument case.
```

---

## Design Decisions

**Why is AuditLog a separate BC and not a Coordinator service?**
AuditLog could be a service layer within the Coordinator rather than a separate BC. Separation was chosen because AuditLog has a different failure mode (a custody-event write failure aborts the run fail-closed, while a telemetry write failure does not), a different persistence contract (append-only), and a different lifecycle (it must be writable when other BCs fail). These are three independent Disintegrators that justify the boundary. The write-failure propagation policy for custody events is fail-closed; see ADR-027 (armed in ADR-033).

**Why is Retrieval separate from Briefing?**
Citation resolution (norm to source text) and briefing assembly are two different steps with two different change axes. Resolution is exact-match lookup over a fixed statute corpus (ADR-021). Briefing is deterministic presentation and status derivation. Binding them would couple the statute corpus to the briefing shape and status rules. Separating them lets Briefing consume already-resolved norms in its tests and lets the resolution strategy evolve independently. See ADR-020.

**Why is the Coordinator synchronous?**
The Sachbearbeiter does not wait on a queue: they submit a document and expect a briefing. The end-to-end pipeline is fast enough for synchronous execution. Async would add complexity without reducing wait time for the user. ADR candidate if throughput requirements change.

**Why does Briefing receive the arguments and resolved norms from the Coordinator?**
Briefing assembles a per-argument artifact, so it needs the extracted arguments (as plain dicts) and the resolved norms mapped by the Coordinator into `ResolvedNormEntry`. It also receives `einwendungs_typ` to carry the document-level classification onto the briefing, not to select an LLM prompt strategy (there is no LLM in this context). The Coordinator owns the cross-context mapping so that Briefing imports nothing from Triage or Retrieval.