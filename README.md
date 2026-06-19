# citizen-objections-rag: Auditable Processing of Mass Citizen Objections

A retrieval-augmented system that turns raw citizen objections (*Masseneinwendungen*)
from a data-center permitting procedure (*Genehmigungsverfahren*) into a structured
assessment briefing (*Würdigungs-Briefing*) for the case officer (*Sachbearbeiter*).
The focus of this project is not the RAG mechanics. It is the data governance and
provability that processing personal data in a regulated public-sector context
demands: deterministic PII masking, a content-free tamper-evident custody chain,
fail-closed audit semantics, and a strict separation of raw, processed, and audit
data.

The system is deliberately conservative where a public procedure requires it.
Citation resolution is deterministic, not LLM-generated. The language model has a
single, bounded role (classification in one context), and every load-bearing rule
is enforced by a mechanism that breaks, not by a prompt that asks.

---

## Motivation

My background is process transformation and RPA automation in a regulated industry,
which is where I learned that the hard part of automating real work is rarely the
happy path. It is the edge cases, the auditability, and the accountability when a
system touches data or decisions that matter. This project applies that lesson to an
ML/AI context: a domain where a wrong or unprovable output has real consequences for
the people whose data is processed and for the authority that must defend its
procedure.

I chose mass citizen objections in data-center permitting because it concentrates
the problems I find most interesting in regulated ML engineering: a high volume of
unstructured, personal-data-bearing text, a legally meaningful output, and a hard
requirement that the processing be transparent, minimized, and provable after the
fact.

---

## Business Context

When a data center is planned, the permitting procedure includes a public
participation phase in which citizens can file objections. In a large project these
arrive in the thousands, many repeating the same concerns in different words. For
each objection a case officer must produce a *Würdigung*, a formal assessment that
addresses the substance of the objection against the relevant legal norms.

This system takes a single raw objection and produces a *Würdigungs-Briefing*: a
draft assessment that identifies the objection type, extracts the cited legal norms,
verifies the citations against the corpus, and assembles the material the case
officer needs. The briefing is a draft. It supports the human assessment, it does
not replace it.

The objections contain personal data: names, addresses, contact details. Everything
in this project follows from that fact.

### At a glance

The numbers below are structural and quality facts about the system. The PII
masking metric is discussed separately, in context, because a single recall figure
is easy to misread.

| Property | Value |
|---|---|
| Bounded contexts | 5 (ingestion, triage, retrieval, briefing, audit) |
| Custody event types in the chain | 6 |
| Default-deny enforcement points | 4 (log keys, event vocabulary, span attributes, audit payload schema) |
| PII categories masked | 4 (person, phone, email, IBAN) |
| Content written into the immutable chain | 0 by construction (per-event payload schema) |
| LLM surface | 1 context (triage classification); retrieval and norm extraction are deterministic |
| Static typing | mypy strict across all modules |
| Architecture enforcement | import-linter contract (layer direction) on every check |
| Tests | <verify against repo> passing |
| Architecture Decision Records | <verify against repo> |

---

## Architecture

Five bounded contexts run as a sequence. Each context owns its logic and its tests.
No context imports another. Only the Coordinator (`pipeline.py`) crosses context
boundaries, and `core/` holds only the contracts that genuinely cross those
boundaries. External I/O sits behind Protocols, with Fakes in the tests.

```
raw objection
  │
  ▼
pipeline.py (Coordinator, composition root)
  ├── document_ingestion/   deterministic PII masking
  ├── triage/               classification (LLM), deterministic norm extraction
  ├── retrieval/            exact norm resolution against the corpus
  ├── briefing/             assessment briefing assembly, status derivation
  └── audit_log/            content-free tamper-evident custody chain
        │
        ▼
  Würdigungs-Briefing  +  audit trail
```

```
src/app/
  core/                 Cross-context contracts only (results, statuses, failures)
  document_ingestion/   PII masking (Presidio NER + anchor extraction)
  triage/               LLM classification, norm extraction, substance + contradiction checks
  retrieval/            Exact dictionary lookup, norm resolution
  briefing/             Briefing assembly, status derivation
  audit_log/            Custody chain: serialization, verify, payload schema
  observability/        Governed logging sink, tracing, metrics (cross-cutting)
  services/             LLM client (cross-cutting)
  pipeline.py           Coordinator orchestration
  __main__.py           CLI entry point
experiments/            Evaluated-and-rejected alternatives (e.g. vector retrieval)
docs/decisions/         Architecture Decision Records
```

The following are the design decisions that define the project. Each is recorded in
an ADR with its reasoning and its trade-offs.

### Deterministic PII masking, not an LLM pass

Masking runs in the ingestion context before anything else sees the text. It is
deterministic: a layered anchor extraction combined with Presidio NER, scoped to
person, phone, email, and IBAN. An LLM masking pass was considered and rejected. A
probabilistic model masking personal data introduces a second, opaque failure mode
on top of the one masking already has, and it cannot be reasoned about or tested the
way a deterministic pipeline can. Masking is the one probabilistic-adjacent surface
in the data path, and keeping it deterministic and measurable was a deliberate
choice. Its measured quality and its residual risk are discussed in Data Governance
below.

### Deterministic norm extraction, not an LLM relay

The legal norms an objection cites are extracted and resolved deterministically and
checked against the corpus by exact match. The language model does not invent,
summarize, or relay citations. A claimed citation that does not verify against the
corpus is marked unverified and that status outranks every other signal in the
briefing. This closes the failure mode where a model produces a confident, fabricated
legal reference. The verification treats the model's output as a hypothesis and the
corpus as the truth.

### Exact lookup, not vector search

Production retrieval is exact resolution, not semantic vector search. Vector search
was built and evaluated, then rejected for this use case and moved to `experiments/`
with the evaluation preserved. For citation resolution, an approximate match that
returns a plausible-but-wrong norm is worse than no match, and the deterministic
lookup is both correct and explainable. This is recorded as a decision, not hidden:
the rejected approach is visible as a documented alternative.

### Content-free tamper-evident custody chain

Every step that handles an objection writes a custody event into an append-only
chain. Each event is serialized canonically (the same logical content always
produces the same bytes, which is what makes the hash meaningful) and hashed
together with its predecessor, so any modification of a past event breaks the chain
and is detected by `verify_chain`. The chain is **tamper-evident, not
tamper-proof**: it is keyless, so it detects modification rather than cryptographically
preventing it. Production would add an HMAC or external timestamping; that is on the
deferral list.

The chain is **content-free by construction**. A per-event payload schema, enforced
at write entry, allows only declared keys with declared types (pseudonyms, hashes,
counts). Free text cannot enter the chain. This is the precondition for coexisting
with the right to erasure: the chain is immutable, the right to erasure applies to
the raw store, and that only holds because the immutable chain carries nothing that
erasure would need to reach.

### Fail-closed custody

A custody-write failure aborts the run. The system does not return a briefing whose
processing it could not record. This is the late, deliberate inversion of an earlier
fail-open interim: visibility was built first (a logged error and a failure metric),
and only once the chain was robust enough that an abort signals a real problem was
the abort armed. Auditability is symmetric: the system records not only what it
wrote but the one critical read of raw personal data.

### Default-deny, applied four times

The same principle recurs across the system: allow only what is declared, reject
everything else. It governs the logging key allowlist, the registered event
vocabulary, the tracing span attributes, and the audit payload schema. In each case
the mechanism rejects the undeclared case loudly rather than letting it pass
silently.

### Scope discipline: two deliberate rollbacks

Two clusters were built and then deliberately rolled back as out of scope for an
ML/AI portfolio. The chain's infrastructure hardening (fsync durability, a
single-writer lock, quarantine-based recovery) was removed: the chain still
demonstrates the concept of manipulation-evident logs, fail-closed survives because
the append still raises on I/O error, and the verify path and the content-free gate
never depended on the removed parts. The logging layer's operational hardening
(log rotation and retention, file-permission hardening, a size metric, a heavy
bootstrap guard) was removed for the same reason. Both are recorded as superseded
decisions, not deleted, and both appear on the deferral list as "built, then rolled
back; production would restore them." Removing working code because it does not serve
the project's purpose is itself a design decision.

---

## Data Governance and Compliance

This project treats data governance as the core, not an afterthought, so it gets its
own section.

**Three-store separation.** Raw objection text, processed (masked) data, and the
audit chain are separate stores with different lifecycles. The right to erasure
(Art. 17 GDPR) applies to the raw store; the audit chain is immutable and, by the
content-free design above, carries nothing that erasure would need to reach.

**PII masking quality, in context.** The deterministic masking achieves approximately
**90.8% recall and 99.0% precision** for personal-name detection on the project's
masking evaluation set. The high precision means it rarely over-masks and corrupts
legitimate text. The recall figure is the honest and important one: it means roughly
one name in eleven is not caught by masking alone. This is why masking is presented
as one layer in a defense-in-depth approach, not as a standalone guarantee. The
residual is a documented risk, and the other layers (the content-free chain so
residual PII never reaches the immutable store, the three-store separation, the
narrow LLM surface) exist precisely because masking is not, and cannot be, perfect.
Overstating masking as "PII removal" would be exactly the kind of claim this project
avoids.

**Regulatory framing (forthcoming).** Two documents are in preparation and will be
linked here:

- A data protection impact assessment (*Datenschutz-Folgenabschätzung*, Art. 35
  GDPR), mapping the processing, its risks, and the implemented mitigations.
- An EU AI Act classification, assessing whether the system is high-risk given that
  it produces a draft under human assessment, and which obligations follow.

A deferral list documents what was deliberately not built or rolled back (keyed
integrity such as HMAC, external timestamping, WORM storage, a SIEM integration, the
rolled-back durability and operational hardening, access control), each with the
condition under which production would add it. The honest accounting of what the
system does not do is part of what it does well.

> This project demonstrates an auditable ML-system and data-governance architecture.
> It is not a certified, revision-proof authority system. The chain is
> tamper-evident, not tamper-proof; access control, cryptographic hardening, and the
> organizational and legal frame are the layers a production deployment would add.

---

## Key Decisions

The Architecture Decision Records in `docs/decisions/` document the major choices and
their reasoning. Representative examples (verify the exact numbers against the repo):

- **Deterministic PII masking** over an LLM masking pass, for a single,
  reason-about-able failure mode.
- **Vector search evaluated and rejected** in favor of exact citation resolution,
  with the evaluation preserved under `experiments/`.
- **Canonical serialization** as the foundation of the hash chain: the same logical
  event must always produce the same bytes, or verification computes a different hash
  than writing did and the proof is silently broken.
- **Content-free payload schema** enforced at write entry, the precondition for
  erasure coexistence.
- **Fail-closed custody**, armed only after the chain was robust enough that an abort
  signals a real problem.
- **Declared cross-cutting layers** with an import-linter contract, so the
  architecture is enforced by a mechanism rather than by discipline.
- **Two scope rollbacks** of infrastructure and operational hardening, recorded as
  superseded rather than deleted to preserve the decision history.

---

## Quickstart

```bash
# Install
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Add your MISTRAL_API_KEY

# Process an objection
python -m app <args>          # see --help for the current interface

# Verify the audit chain
python -m app verify-audit
```

---

## Development

```bash
make test         # unit tests, no API calls (Fakes for external I/O)
make lint         # ruff + mypy strict
make architecture # import-linter contract (layer direction)
```

### Test Strategy

Unit tests cover per-context logic with Fakes for external I/O and no API calls. The
import-linter contract enforces the layer direction (the observability and services
layers depend on no bounded context) on every run, so structural drift is caught
mechanically. An evaluation harness measures the probabilistic component (masking)
against a labeled set. mypy runs in strict mode across all modules.

Tests are named after the behavior they protect, not the method they call. Test
doubles are Fakes, not mocks, and the read path of the audit store is validated for
tolerance (a content rule has no business failing an open) while the write path is
strict.

---

## Status

A portfolio project. It runs on synthetic example objections, not on live authority
data, and against a fixed, checked-in legal corpus.

The implementation was developed in close collaboration with Claude Code. Code
generation and routine refactoring were AI-assisted; the architecture, the ADRs, the
review-driven refactor strategy, and the scope decisions (including the two
deliberate rollbacks) are mine. Each build round was followed by independent review
passes (architecture, security, reliability), and the finished system was put through
two whole-system reviews; the single real correctness finding from those reviews (an
empty-quote verification bypass) was fixed with a regression test. A final ownership
review was the validation step.

This working method is itself part of the project: directing an AI implementation
agent while holding the architecture, the reviews, and the scope under explicit
control.

Not actively accepting contributions.

## License

MIT. See `LICENSE` for details.