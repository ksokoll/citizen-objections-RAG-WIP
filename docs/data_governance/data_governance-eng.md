# Data Governance: Pseudonymization and LLM Processing

Status: Draft
Date: 2026-06-03
Scope: Processing of citizen objections in the Triage pipeline.

This document records the data-protection basis of the PII processing,
following the structure of a Data Protection Impact Assessment (DPIA,
Art. 35 GDPR). It is a sketch for the demo project, not a full DPIA. In a
real public-administration deployment it would be replaced by the DPIA of
the authority's data protection officer. The technical implementation of the
masking is in ADR-025, which refers to this document for the legal rationale.

A German-language version of this document is kept alongside it
(DATA_GOVERNANCE.de.md); the German version is authoritative for the German
legal terminology.

## 1. Description of the processing

During public consultation, the authority processes incoming citizen
objections (mass objections) to development plans and comparable projects.
The pipeline extracts the legal arguments via an LLM (Triage), assigns norms,
and produces a deterministic briefing for the case worker. The purpose is the
efficient, traceable preparation of the objections, not the assessment of or
decision about individual citizens.

The intended production model is Mistral under maximum security requirements
(European provider).

## 2. Legal basis

Processing takes place within the authority's statutory administrative task
(Art. 6(1) GDPR, processing to comply with a legal obligation or to perform a
task in the public interest). The objection itself is the citizen-initiated
procedural step; handling it is the statutory task.

## 3. Pseudonymization, not anonymization

The pipeline works exclusively with masked text. The unmasked original is held
in the access-controlled raw store and is recoverable via the document_id
(ADR-010).

Legally this is pseudonymization, not anonymization: re-identification remains
possible with additional information (the raw store). Consequence: the masked
data remain personal data and stay within the scope of the GDPR. This is
deliberate. The goal of masking is risk minimization and data minimization,
not leaving the GDPR regime. The recoverability is intended, for archival and
case-worker audit.

## 4. Data minimization: scope of masking

The governing principle is Art. 5 GDPR (data minimization): the identifying
attributes that are not necessary for the processing purpose (legal argument
extraction) are removed.

Masked are the identifying core attributes:
- Names (PERSON)
- Phone numbers
- Email addresses
- IBAN

Not masked are location references (LOCATION). Rationale:
- A bare place name does not identify a person in a regional mass objection;
  the authority expects most objections to come from the region anyway.
- Place names, water bodies, protected areas, and plan areas are regularly
  the substantive subject of the objection (for example the habitat
  compatibility of a named area, water extraction from a named river).
  Masking them would destroy the argumentative core and thereby violate the
  data-quality requirement that the LLM input needs for correct extraction.
- Empirically confirmed: blanket location masking produced 26 masked spans in
  one real test document, of which only a small part were genuine address
  components and the rest were substantive proper nouns.

The identifying content of an address lies in the combination of name plus
street plus house number. Since the name is already masked, the
identification value of any remaining location data drops significantly.

## 5. Risks and measures

Residual risk: the NER-based name detection is not perfect (German model
around 0.84 F1); individual names may pass through unmasked. The masking is
therefore one line of defense, not the only safeguard.

Measures:
- European model (Mistral) under maximum security requirements.
- Access-controlled raw store; the unmasked original does not leave the
  pipeline.
- Human in the loop: the briefing goes to the case worker; the system makes
  no final decision about the citizen.
- Masking quality is measurable as a Fitness Function (recall-first, F2),
  with a data-driven threshold (ADR-025).

## 6. EU AI Act

Classifying the Triage under the EU AI Act risk classes is a case-by-case
assessment. Administrative decision support in a citizen-rights-relevant
administrative procedure points toward a high-risk classification (Annex III).
The AI Act does not regulate the masking depth; for data protection it refers
back to the GDPR (DPIA under Art. 35 GDPR) and additionally requires
traceability, documentation, human oversight, and cybersecurity. These
requirements are addressed by the audit trail, the observability, and the
case worker in the loop.

Labeling obligations for AI-generated citizen-facing text barely apply: the
briefing goes to the case worker, not to the citizen, and the final stage is
deterministic without LLM generation.

## 7. Disclaimer

This document is a technical sketch, not legal advice. The binding
data-protection assessment and the EU AI Act classification are the
responsibility of the authority's data protection officer or of a legal
review in the concrete deployment.