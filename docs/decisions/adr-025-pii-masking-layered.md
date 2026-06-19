# ADR-025: PII Masking via Layered Detection (Presidio plus Anchor Extraction)

Status: Accepted. Reduced to decision length in the one-time consolidation
(ADR-037). The data-protection rationale and residual-risk analysis live in
DATA_GOVERNANCE_STATEMENT.md, the fitness-function methodology in
experiments/pii_evaluation, and the detailed masker mechanics in the
DocumentIngestion code.
Date: 2026-06-03
Deciders: Kevin Sokoll

## Context

ADR-010 established that DocumentIngestion masks PII as the last step before
handing text downstream, that masking (reversible via an access-controlled raw
store) is chosen over irreversible anonymization, and that masking accuracy is a
Fitness Function. The step was a pass-through until this work; the raw Einwendung
flowed through the pipeline unmasked. The task: detect and mask PII in free-text
German Einwendungen deterministically, without an LLM in the masking step itself.

Threat-model note (it relaxes the requirements): the production deployment assumes
the Triage LLM runs encapsulated, no outbound network and no prompt retention
(ADR-036). Under that assumption masking is internal data minimization and least
privilege (limit PII in logs and to case workers, reduce blast radius if the
boundary breaks), not a wall against a third-party processor; a missed entity
stays within the trust boundary. The data-protection reasoning, the
de-identification-not-anonymization framing, and the residual quasi-identifier and
LLM-inference risk are in DATA_GOVERNANCE_STATEMENT.md, not solved by more masking.

## Decision

Layered detection behind a PiiMasker Protocol, with the masker as the only module
importing Presidio.

Masked scope (identifying core attributes only): PERSON, PHONE_NUMBER,
EMAIL_ADDRESS, IBAN_CODE. Locations, postal codes, and case numbers are
deliberately not masked (see Rationale).

Two detection layers, merged additively:

1. Anchor-based zone extraction (pure regex, no spaCy): names in the structurally
   fixed zones (the submitter line, the "vertreten durch" clause) are extracted
   deterministically, titles and functions stripped, the organisation before
   "vertreten durch" skipped and the natural person after it taken. Extracted
   names are masked only within two zones, the anchor (header) zone and the
   signature zone (located by a Grußformel or "gez." marker, else the trailing
   short-block run), not document-wide; running-text occurrences are left to NER.
   Confining anchor masking to these zones closes an analysis-integrity vector
   (below).
2. Presidio analyze over the full text: spaCy German NER (de_core_news_md) for
   PERSON, a controlled German phone-number regex, and the built-in email and IBAN
   recognizers. The built-in PhoneRecognizer is removed because it matches German
   dates as phone numbers.

A single Anonymizer pass replaces detected spans with speaking placeholders
([NAME], [TELEFON], [EMAIL], [IBAN]), not empty strings, so sentence structure
survives for the Triage LLM and the masked document stays auditable.

Count contract (owned by our code, not Presidio). masked_entity_counts is
DSGVO-relevant, so it must not rest on a third-party heuristic a Presidio version
bump could shift silently. The masker merges the anchor and analyzer spans into
regions (joining overlapping and whitespace-adjacent same-type spans), counts
regions per type, and hands that same merged set to the anonymizer, so the count
is deterministic and not an artefact of model behaviour. The same name in the
header and the signature is two regions, hence two NAME counts; a medium-scale
test pins this against the real masker.

One-way masking: no token-to-original mapping is kept. The original is available
only from the access-controlled raw store via the document_id (a random uuid4
token), for archival and Sachbearbeiter audit, never re-injected.

Structure: the PiiMasker Protocol and the MaskingResult DTO live in the
DocumentIngestion context (consumed only by DocumentIngestionService, implemented
by PresidioMasker and FakePiiMasker); only the cross-context IngestionResult stays
in core. Dependencies are pinned exactly (the de_core_news_md wheel as a PEP 508
direct reference, presidio-analyzer and presidio-anonymizer ==), because the count
contract depends on their behaviour and pinning is what makes the golden
evaluation stable.

## Rationale

Why layered, not flat NER. Flat NER reaches only about 75% name recall on the
evaluation corpus (md and lg both, so model size is not the lever); the misses
concentrate in the fixed submitter and representative zones, where a name after a
title in a header line is out-of-distribution for a model trained on running text.
Anchor extraction makes those zones near-deterministic and lets NER handle only
the running text where it is in its element. The layered masker reaches 90.8% name
recall at 99.0% precision (Consequences).

Why the reduced scope. LOCATION masking was empirically catastrophic for utility:
against a real document, blanket location masking produced 26 masked spans,
destroying argument-bearing geography (river names, protected areas, plan areas)
that Triage needs, while a place name barely identifies in a regional mass
objection. Postal codes alone do not identify once the name is masked. Case
numbers are rare in citizen free text, are process identifiers, and carry the
highest false-positive risk against norm citations. All three: delete, not refine.

Speaking placeholders over empty strings: an empty string gives the Triage LLM
malformed input; a placeholder preserves syntax and is auditable. One-way masking:
ADR-010 keeps the original in the raw store, so the pipeline text never needs
de-masking and the masker keeps no sensitive mapping.

## Alternatives Considered

1. Layered detection (chosen).
2. Flat NER, no anchors. About 75% recall, misses the fixed-zone names. Rejected
   as primary.
3. Larger spaCy model (lg). No meaningful gain over md, 567 MB for nothing.
   Rejected.
4. Transformer NER. Higher running-text recall at higher cost and size. The
   production upgrade path, not built for the demo.
5. LLM-based masking. Highest recall, but violates the no-LLM constraint, sends
   PII to a model, adds cost and variance. Rejected.
6. No Protocol, call Presidio directly. Rejected: violates the External I/O
   Boundary rule (unit tests could not run without spaCy).

On 6, the conflict with the Simplify Loop (an abstraction serving one
implementation) is resolved in favor of the External I/O rule, because the
Protocol has two real users from day one: PresidioMasker in production and
FakePiiMasker in the unit tests.

## Analysis-integrity hardening (zone restriction)

A security review of the masking path raised five findings; under the
encapsulated-LLM model none hard-fails on a leak (a slipped name stays inside the
trust boundary), so the path records and logs rather than blocking. The one
decision worth recording here beyond robustness fixes is zone-restricted anchor
masking, which is analysis integrity, not privacy. Anchor names were previously
masked at every occurrence document-wide, so a crafted submitter line ("Einreicher:
Lärmschutz Bebauungsplan") could redact substantive words throughout the legal
reasoning and suppress a real argument into KEIN_TREFFER. This is input-controlled
poisoning of the analysis, not a data leak, and the encapsulated LLM does not
protect against it. Anchor masking is now confined to the anchor and signature
zones; NER covers running-text names. Re-measured after the change: 90.8% recall
(59/65), 99.0% precision (101/102), against the pre-change 92.3% / 98.0%. The
one-token recall dip is "Sommer", a submitter surname whose season homonym now
correctly survives in running text while the surname is masked in the header and
signature; the crude metric counts the surviving season word as a leak, so the dip
is an integrity win (precision rose), not a regression.

The other hardening and the deliberate non-fixes are in the DocumentIngestion code
and DATA_GOVERNANCE_STATEMENT.md: an input-size bound at the ingestion boundary as
the one hard error; a coverage self-check that proves the deterministic anchor
layer cleared its zones and records the NAME count as positive evidence, logging
an anomaly but never blocking; raw-store owner-only permissions, best-effort on
Windows; and, as deliberate non-fixes, no hard-fail in the masking path itself and
no NER-leak detection for running-text names spaCy misses (that probabilistic
residual stays documented, not claimed as caught).

## Consequences

Positive: the pipeline is PII-free downstream of ingestion for the masked scope;
the service is unit-testable with FakePiiMasker, no spaCy in fast tests; measured
masking quality is 90.8% name recall at 99.0% precision on a 20-document synthetic
corpus, deterministic (pinned model), so a single run per configuration is valid.

Negative: a residual recall gap (about 9%) of names in unanchored zones (a long
signature list, a "gez." lawyer signature) that NER misses, acceptable under the
least-privilege framing; token-level anchor masking can over-mask an organisation
name sharing a token with a person name, now confined to the header and signature
zones so running-text collateral is gone; the pinned model and spaCy version must
be kept in sync on upgrade.

Neutral: the Fitness Function reports recall and precision separately, not a single
averaged score, because they capture opposite failure modes (recall the safety
axis, a leaked name; precision the utility axis, destroyed context); the corpus is
synthetic, safe to commit and annotate.

Deferred (decisions, not omissions): no PersonSpanDetector strategy protocol (one
backend, and the swap seam already exists at the PiiMasker Protocol; introduce a
second strategy when a second detector is built); no externalised
recognizer-vocabulary config (one corpus; externalise when a second data source
with different zone conventions arrives).