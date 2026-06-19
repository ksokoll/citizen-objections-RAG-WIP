# Data Governance: Pseudonymization and LLM Processing

Status: Draft
Date: 2026-06-03 (section 4 and the related section 6 risk corrected to the
implemented four-category masking scope; the DE_STRASSE street recognizer named in
an earlier draft was never built, see ADR-025).
Scope: Processing of citizen objections in the Triage pipeline.

This document records the data-protection basis of the PII processing, following
the structure of a Data Protection Impact Assessment (DPIA, Art. 35 GDPR). It is
a sketch for the demo project, not a full DPIA. In a real public-administration
deployment it would be replaced by the DPIA of the authority's data protection
officer. The technical implementation of the masking is in ADR-025, which refers
to this document for the legal rationale.

## 1. Description of the processing

During public consultation, the authority processes incoming citizen objections
(mass objections) to development plans and comparable projects. The pipeline
extracts the legal arguments via an LLM (Triage), assigns norms, and produces a
deterministic briefing for the case worker. The purpose is the efficient,
traceable preparation of the objections, not the assessment of or decision about
individual citizens.

The demo runs exclusively on synthetic and self-authored test documents; no real
citizen data is processed. The analysis below describes the conditions a real
deployment would have to meet. The production model is Mistral (European
provider); in the demo it is called via the hosted API, and the encapsulated
deployment posture this would require in production is described in section 8.

## 2. Legal basis

A public authority processing personal data to perform its statutory tasks relies
on Art. 6(1)(e) GDPR (performance of a task carried out in the public interest or
in the exercise of official authority), read together with the sector-specific
procedural law that governs the consultation (the Anhörungsverfahren under the
VwVfG and the applicable sectoral planning or approval statute) and the
data-protection law of the competent Land (Landesdatenschutzgesetz) or the BDSG.
The precise combination is to be specified for the concrete procedure.

Legitimate interest (Art. 6(1)(f)) is not available here: the final subparagraph
of Art. 6(1) excludes it for processing by public authorities in the performance
of their tasks. Any legitimate-interest reasoning, including the legitimate-
interest part of EDPB Opinion 28/2024, therefore does not apply to this case
worker scenario.

The objection itself is the citizen-initiated procedural step; handling it is the
statutory task.

## 3. Pseudonymization, not anonymization

The pipeline works exclusively with masked text. The unmasked original is held in
the access-controlled raw store and is recoverable via the document_id, which is a
random uuid4 token (not derived from the personal data) kept separately from the
masked data (ADR-010). This mirrors the GDPR definition of pseudonymization and
the re-identification code that HIPAA permits.

Legally this is pseudonymization, not anonymization: re-identification remains
possible with additional information (the raw store), so the masked data remain
personal data within the scope of the GDPR. This is deliberate; the recoverability
is intended for archival and case-worker audit. The goal of masking is risk and
data minimization, not leaving the GDPR regime.

This aligns with the scientific state of the art. Named-entity masking is
de-identification, not anonymization (Pilán and Lison, Text Anonymization
Benchmark, 2022): it removes predefined identifier categories but cannot remove
the unbounded set of quasi-identifiers (profession, local references, the specific
substance of an objection) that may still enable re-identification. Under EDPB
Opinion 28/2024 a claim that a model or its data is anonymous must show that the
likelihood of identification, by all means reasonably likely to be used, is
insignificant. This system does not meet that bar and does not claim to; it
operates as a de-identification and risk-reduction measure within the GDPR regime.

## 4. Data minimization: scope of masking

The governing principle is Art. 5(1)(c) GDPR (data minimization): identifying
attributes not necessary for the processing purpose (legal argument extraction)
are removed; substantively relevant context is retained.

Masked (the four identifying core attributes, ADR-025):
- Names (PERSON)
- Phone numbers (PHONE_NUMBER)
- Email addresses (EMAIL_ADDRESS)
- IBAN (IBAN_CODE)

Not masked:
- Street and house number. The implementation masks only the four core
  identifiers above; there is no street recognizer. Street and house number
  therefore remain in the processed text. Unlike the place names below, this is
  not argument-bearing context but a precise residential locator, so it is the
  strongest retained-data residual and is recorded as a known residual and a
  candidate scope extension in section 6, not justified away here.
- Place names and the broader geographic context (LOCATION). A bare place name
  does not identify a person in a regional mass objection, and place names, water
  bodies, protected areas, and plan areas are regularly the substantive subject of
  the objection (for example the habitat compatibility of a named area, water
  extraction from a named river). Masking them would destroy the argumentative
  core and degrade the data quality the extraction needs. Blanket location masking
  was empirically over-broad: in one test document it produced 26 masked spans,
  most of which were substantive proper nouns rather than address components
  (ADR-025, Rationale).
- Postal code (PLZ). A five-digit postal code identifies a postal area of
  thousands of residents, not an individual, and its identification value is
  further reduced once the name is masked. Keeping it also avoids false-positive
  masking of unrelated five-digit numbers (amounts, quantities).
- Case reference numbers (Aktenzeichen). The recognizer was removed because its
  pattern collides most strongly with legal norm citations, and masking a citation
  would break the downstream norm extraction; a case reference is also weakly
  identifying once the name is masked.

The identifying content of an address lies in the combination of name, street, and
house number. The implementation breaks this combination at one point only, the
name. Street and house number are retained in the processed text, which is a
precise residential locator rather than coarse geography (the residual in section
6). The additionally retained coarse geography (place name, postal code) is itself
weakly identifying once the name is removed and is kept for substantive reasons.

## 5. Administrative-law context (VwVfG, Art. 22, file integrity)

Beyond data protection, the public-law frame applies.

Automated individual decision-making (Art. 22 GDPR): the pipeline prepares
decisions, it does not make them. A human case worker remains in the loop and the
final stage is a deterministic briefing without LLM generation. This supports the
position that the processing stays outside Art. 22 and that the substantive
decision remains attributable to the responsible official (Amtswalter). A real
deployment should state this explicitly and ensure no automated decision with
legal effect on the citizen is produced.

File integrity (Aktenwahrheit, Aktenvollständigkeit): administrative law requires
a complete and truthful file. Because the case worker's working artifacts are
derived from masked text, the complete unmasked original must remain the
authoritative record. The raw store is the file (Akte); the masked briefing is a
working aid only; the substantive decision must rest on the complete original, not
on the masked version. The pseudonym-in-raw-store architecture is built for this,
and the deployment process must enforce it.

## 6. Risks and measures

Residual risks:
- Street and house number are retained. Because the masking scope is the four
  core identifiers and no street recognizer is implemented (ADR-025), a full
  street address present in a citizen objection remains in the processed text and
  in the masked artifacts derived from it. This is a precise residential locator
  and therefore a stronger residual than the coarse geography retained for
  substantive reasons. It is recorded here as a known residual, not a closed gap.
  A candidate scope extension is a dedicated street recognizer narrow enough not
  to collide with the argument-bearing geography that section 4 deliberately
  keeps; the production DPIA decides whether the residential-locator risk warrants
  it. Until then the primary control against this residual is the same closed,
  encapsulated processing setting that controls the inference risk below, not
  deeper masking.
- NER name detection is imperfect (German model around 0.84 F1 as a flat-NER
  baseline; the layered masker improves substantially on this, see ADR-025);
  individual names may pass unmasked. Masking is one line of defense, not the only
  safeguard.
- De-identification does not remove quasi-identifiers. An inference-capable model
  can re-identify from residual context such as dialect, local references, or the
  phrasing of a location-specific objection. Staab et al. (2023) show that LLMs
  infer attributes such as location and age even from text processed by
  state-of-the-art anonymizers. EDPB Opinion 28/2024 treats this as an expected
  check: an anonymity or risk assessment is expected to test resistance to
  attribute and membership inference, exfiltration, regurgitation, model inversion,
  and reconstruction. These tests belong in the production DPIA.

Measures:
- Access-controlled raw store; the unmasked original does not leave the pipeline.
- Human in the loop: the briefing goes to the case worker; the system makes no
  final decision about the citizen.
- Masking quality is measurable as a Fitness Function (recall-first, F2) with a
  data-driven threshold (ADR-025).
- Closed processing setting. EDPB Opinion 28/2024 ties the required degree of
  attack resistance to the release setting (open versus closed) and recognizes
  context controls that reduce attack likelihood without transforming the data. A
  closed, encapsulated deployment (section 8) therefore lowers the required
  transformation and is the primary control against the inference risk and against
  the retained-locator residual above, more than deeper masking.

## 7. EU AI Act

Classifying the Triage under the EU AI Act is a case-by-case assessment.
Administrative decision support in a citizen-rights-relevant procedure points
toward a high-risk classification (Annex III, administration and public services).
The high-risk obligations apply from 2 August 2026; a political agreement of
7 May 2026 provides for a postponement of roughly sixteen months for new or
substantially modified Annex III systems, subject to formal adoption.

Role: an authority that runs an unmodified third-party (open-weights) model for
its own use acts as a deployer, not a provider. Provider obligations arise mainly
from substantial modification or from placing the system on the market under one's
own name. The relevant obligations here are therefore deployer obligations (human
oversight, use according to instructions, logging, cooperation with the DPIA).

The AI Act does not regulate masking depth; for data protection it refers back to
the GDPR (DPIA under Art. 35). It additionally requires traceability,
documentation, human oversight, and cybersecurity, which are addressed by the
audit trail, the observability layer, and the case worker in the loop. Labeling
obligations for AI-generated citizen-facing text barely apply: the briefing goes
to the case worker, not the citizen, and the final stage is deterministic.

## 8. Deployment posture and limitations

This demo calls Mistral via the hosted API and runs only on synthetic data. The
honest limitation is the deployment posture of the LLM, not the design: data
governance is a first-class design concern here (masking, the pseudonym-in-raw-
store separation, this document), but the production-grade deployment controls are
out of scope for the demo.

For a real deployment with real citizen data:
- The legal basis must be the public-task basis (Art. 6(1)(e)) together with the
  sector-specific Fachrecht and the LDSG, clarified by the authority (section 2).
- The hosted API would be a transfer to a processor and would require a
  data-processing agreement under Art. 28 GDPR. This is a precondition for lawful
  processing of real data, independent of the encapsulation goal; without it the
  hosted API is not permissible.
- The documented production target is an encapsulated deployment: no outbound
  network, no prompt retention, an access-controlled endpoint and access-controlled
  outputs. "Encapsulated" is orthogonal to who operates the infrastructure, and the
  Art. 28 picture differs along a spectrum: on-premise (own hardware, no external
  processor); a VPC in a sovereign German cloud (still an Art. 28 processor with a
  data-processing agreement, but no third-country issue); and a VPC at a global
  hyperscaler (additionally raising third-country and CLOUD-Act considerations).
  This is why public administration tends toward on-premise or sovereign clouds.
- The LLMClientProtocol makes the model provider a deployment choice rather than a
  code change, so this swap does not affect the pipeline architecture.

The current SRB case law (CJEU, EDPS v SRB, C-413/23 P, 2025) is relevant to the
transfer question: pseudonymized data may not be personal data for a recipient who
cannot reasonably re-identify, but it remains personal data for the controller
(the authority, which holds the raw store). This does not remove the controller's
obligations and is not relied on operationally.

## 9. Disclaimer

This document is a technical sketch, not legal advice. The binding data-protection
assessment and the EU AI Act classification are the responsibility of the
authority's data protection officer or of a legal review in the concrete
deployment.