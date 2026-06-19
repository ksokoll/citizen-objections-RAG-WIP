# PII_EVAL_RESULTS.md

Documentation of the evaluation of the PII masking step (DocumentIngestion Bounded Context). Describes the iterations, methodological turning points, and final state. Serves as a development history and lessons-learned reference. Companion to docs/decisions/adr-025-pii-masking-layered.md (the decision record) and DATA_GOVERNANCE_STATEMENT.md (the data-protection reasoning).

This document is written as an honest process record, not a clean narrative. Where the work was ad-hoc (no pre-registered hypothesis, two stale-state incidents, single overwriting result file), it is recorded as a gap, not retrofitted as a plan.

---

## Current State

**Date**: 2026-06-03, Iteration 5 (layered detection validated, phone false-positive fixed)

**Task**: Mask identifying PII in free-text German Einwendungen before the text enters the pipeline (Triage, Retrieval, Briefing, AuditLog), deterministically and without an LLM in the masking step. Masked scope: PERSON (name), PHONE_NUMBER, EMAIL_ADDRESS, IBAN_CODE. Locations, postal codes, and case numbers are deliberately not masked (rationale in ADR-025 and the governance statement).

**Architecture**: Layered detection behind the PiiMasker protocol. Anchor-based zone extraction (zone_extractor, pure regex) for the structurally fixed submitter and representative zones, plus Presidio (spaCy German NER de_core_news_md for PERSON, a controlled German phone regex, built-in email and IBAN recognizers) over the full text. The built-in Presidio PhoneRecognizer is removed because it matches German date formats. Anchor and analyzer spans are merged additively into a single anonymizer pass.

**Final Metrics (20-document synthetic corpus)**:

| Stage | Name Recall | Precision |
|-------|-------------|-----------|
| Flat NER (de_core_news_md) | ~75% | 98% |
| Flat NER (de_core_news_lg) | 75.4% | 97.1% |
| Layered (anchors + md NER) | 92.3% | 98.0% |

Recall and precision are reported separately, not averaged, because they capture opposite failure modes: recall is the safety axis (a leaked name), precision the utility axis (a substantive term destroyed). The measurement is deterministic (pinned spaCy model), so a single run per configuration is valid; no variance bound is needed.

**Status**: Masker built and measured. Not yet wired into DocumentIngestionService (the pass-through clean_text = raw_text is still in place; wiring is the next feature step). Residual issues known and documented (see Open Issues).

---

## Test Corpus

The same 20 synthetic objection documents used by the extraction evaluation, reused rather than duplicated (the run script reads from experiments/extraction_evaluation/data via the document list in the ground truth).

- einspruch_01 to einspruch_10: TYP_1 (informal citizen letters)
- einspruch_14 to einspruch_20: TYP_2 (formal legal submissions)
- einspruch_11 to einspruch_13: mixed variants

All documents are synthetic (no real citizen PII), so the ground truth with full names is safe to annotate and commit. The corpus contains no phone numbers, emails, or IBANs; this evaluation therefore exercises NAME recall and the precision of not masking substantive terms. The fixed phone regex is not covered by this ground truth.

---

## Evaluation Method (Weg A)

Count-level ground truth was insufficient: when the masker over- and under-masks in the same document (a real name missed, a street name falsely masked), a single count of 14-vs-12 mixes two opposite errors into one uninterpretable number. The method was extended (Iteration 3) to two explicit lists per document:

- **names_must_mask**: name tokens (given and family names) that must be absent from the masked text. Checked with word boundaries (so "Stein" is not counted as present inside "Steinbruch"). A token still present as a word is a recall miss.
- **must_survive**: substantive terms (place names, technical terms, dates, identifiers, organisation names) that must still be present. A plain substring check; spelling is maintained in the ground truth.

Ground truth lives in experiments/pii_evaluation/ground_truth.json, the run script serializes masked text plus entity counts to results.json, and evaluate.py compares the two and reports recall and precision separately, per document and aggregate.

---

## Iterations

### Iteration 1: Flat NER Baseline (de_core_news_md)

**Approach**: Presidio over the full text, spaCy de_core_news_md for PERSON, regex for phone/email/IBAN. No anchors. Measured against the count-plus-lists ground truth.

**Results**: 73.5% name recall on the first run, with two ground-truth artifacts inflating the miss count (the Groß and Franken/Stein token conflicts had not been removed from names_must_mask; the GT correction had not been saved to disk). After the GT correction the baseline was clean. Precision ~98%.

**Finding**: The misses are not random. They concentrate in the structurally fixed submitter and representative header zones (einspruch_14 Andreas Wengler, 15 Stefan Kramer, 16 Hans-Dieter Volz, 17 Karl-Heinz Pontzen all at 0%). A name right after a title in a header line ("vertreten durch Geschäftsführer Dr. ...") is out-of-distribution for a model trained on running text.

**Methodological gap recorded**: No pre-registered hypothesis. The "misses sit in the fixed zones" insight came from reading the per-document output, not from a prediction stated before measurement. This is HARKing; the honest framing is "measured flat, analysed the error structure, derived the hypothesis, then confirmed it", not "predicted that anchors would help".

---

### Iteration 2: Flat NER with Larger Model (de_core_news_lg)

**Hypothesis** (this one was stated before the run): a larger model lifts recall.

**Approach**: Swap de_core_news_md for de_core_news_lg (567 MB), nothing else changed. Re-run.

**Results**: 75.4% recall, 97.1% precision. Essentially identical to md. The aggregate hid the real effect: lg fixed 17 and 19 (which md missed) but newly missed 14, 15, 16 (which md caught). Same ~75%, different leaks.

**Finding (negative result, first-class)**: Model size is not the lever. Both models reach about three quarters, but different three quarters; the misses are quasi-random at the model-class boundary, and a different model just rolls the dice differently. The structural problem (NER on fixed zones) is unchanged by model size. lg was not adopted: 567 MB for no benefit. This negative result is the empirical justification for the layered architecture, not the flat-model path.

**Decision**: Stay on md. Pursue layered detection rather than a stronger flat model.

---

### Iteration 3: Evaluation Method Correction

**Motivation**: Two weaknesses in the evaluation itself, independent of the masker, surfaced during Iterations 1 and 2.

**Changes**:
- Recall check moved from a plain substring test to a word-boundary test. A name token counts as leaked only if present as a standalone word, so a name appearing as a substring of an unrelated word (the "Stein" in "Steinbruch" case) is no longer a false leak, and a name is not falsely counted as masked because a longer word containing it disappeared.
- expected_name_count removed from the evaluation logic. It was a dead indicator (documented but never asserted) and ambiguous after the span-versus-person discussion; names_must_mask is the better recall indicator.
- The two ground-truth token conflicts (Familie Groß / Weingut Groß; lawyer surnames / Kanzlei Franken & Stein) resolved by dropping the colliding surnames from names_must_mask, since the same string cannot be both a mask obligation and a survival obligation under a flat string check.

**Finding**: This is the reactive-diagnostic-extension pattern: when the metric looked uninterpretable (counts mixing two error directions), the measurement was extended rather than the model tuned. The word-boundary logic was self-tested on six cases including Stein/Steinbruch and hyphenated names before use.

---

### Iteration 4: Layered Detection (Anchor Extraction plus NER)

**Hypothesis** (derived from Iteration 1, then tested here): names in the fixed zones can be extracted deterministically by anchors, closing the recall gap that flat NER leaves there. NER is reduced to the running text, where it is in its element. This is the "anchor and propagate" pattern applied to person zones.

**Approach**: New zone_extractor module (pure regex, no spaCy, separately testable). Two anchors:
- Direct submitter: text after "Einreicher:/Von:/Einreichende Person:" up to the first comma, unless an organisation marker or a "vertreten durch" clause is present (then the name comes from the representative clause).
- Representative: text after "vertreten durch", with a shared prefix set stripping titles and functions (Dr., Dipl.-Ing., Geschäftsführer, den Vorsitzenden, ...).

Extracted names are split into tokens (stopwords und, c/o, Familie, ... dropped) and masked at every word-boundary occurrence, which also covers the signature without a separate anchor. Anchor spans are merged additively with the Presidio analyzer spans into one anonymizer pass (Variant 1: global NER plus anchor spans, avoiding offset recomputation).

**Prefix-stripping bugs found and fixed during extractor development**: "RA" stripped the start of "Ralf" (prefix matched as substring), and "Sprecher" consumed the start of "Sprecherin". Both fixed by requiring prefixes to be whole tokens (followed by space, period, or end), verified against all 20 header zones (19 exact, 1 the intended Familie Groß case).

**Results**: 92.3% name recall, 98.0% precision. The four flat-NER total failures (14, 15, 16, 17) are all at 100%. The couple constructions (02, 08, 10) fully masked. einspruch_13 from 80% to 100%.

**Finding**: The deterministic anchors close the fixed-zone gap, and as a side effect improved address precision (the header addresses are no longer handed to NER, so street names are no longer falsely masked there). Schicht 2 (a separate address-block detector, originally planned) turned out not to be needed after this, a data-driven decision not to build it.

---

### Iteration 5: Phone False-Positive Fix (Built-in Recognizer)

**Symptom**: einspruch_08 showed "08.11.2024" (a date) masked as [TELEFON]. The controlled German phone regex did not match it (verified by direct test against the real file), yet the masked output and the counts contained a TELEFON.

**Two false leads before the cause was found**: First suspected the phone regex itself (it was the wrong mechanism). Then suspected a stale bytecode cache (cleared it, no change). The module path and the loaded regex were both verified correct.

**Cause**: Presidio's AnalyzerEngine loads a default recognizer set, including a built-in PhoneRecognizer (backed by the phonenumbers library) that runs in parallel to the custom regex and matches German date formats as phone numbers. The two PHONE_NUMBER recognizers both fired; the built-in one matched the date.

**Fix**: Remove the built-in PhoneRecognizer from the registry so only the controlled regex decides what is a phone number. Documented in ADR-025, with the note that an explicit allow-list registry (only the wanted recognizers) would be the more thorough production approach.

**Result**: 08 clean (date survives), final state 92.3% recall, 98.0% precision.

**Methodological gap recorded**: Two stale-state incidents cost time in this evaluation. In Iteration 1, the GT correction was not saved before re-running, so evaluate.py reported phantom leaks (Groß, Franken, Stein). In Iteration 5, results.json was from an older masker state, so a fixed bug appeared unfixed. Both are the same class as the MODEL_NAME mislabeling in the extraction evaluation: a code-or-artifact state mismatch caught only by output comparison. The recurring lesson: the measured state and the code state must be verifiably the same.

---

## Open Issues

### einspruch_06: 80% Recall (12-Signature List)

The 12-person numbered signature list ("1. Hildegard Mayer, Moselstraße 3") has no submitter anchor, so it is handled by flat NER, which catches 16 of 20 name tokens (Petra, Schreiber, Fuchs, Renate missed). A dedicated list anchor (numbered lines "N. Vorname Nachname, Straße") would close it, but it was deliberately scoped out as a special case.

**Priority**: low. Under the encapsulated-LLM least-privilege framing a missed name stays within the trust boundary.

---

### einspruch_20: One Signature Name Missed (Matthias)

The lawyers Matthias Franken and Lena Stein appear only in a "gez." signature, not in a "vertreten durch" clause. "Lena" is caught by flat NER, "Matthias" is not. A "gez." anchor was not built (the signature is covered for the common case via the same-name-repetition logic, which does not apply when the signature introduces names not in the header).

**Priority**: low. Same trust-boundary reasoning.

---

### Org-Name Over-Masking (Weingut Groß, Weingut Kessler)

Token-level masking of extracted names over-masks organisation names sharing a token with a person name. "Weingut Groß" and "Weingut Kessler" lose the shared surname token. Accepted as harmless over-masking (an org-name token masked, no leak, no argument damage). The whole-string alternative would avoid it but miss scattered single-token occurrences in the signature.

**Priority**: low. Documented in ADR-025 Consequences.

---

### NER False Positive on Place Names (Moseltal as PERSON)

Flat NER occasionally classifies a place name as PERSON in the running text (observed earlier as "im Umfeld des [NAME]" where a place name stood). This is a NER misclassification, not a regex issue, and not addressed by anchors. Under the least-privilege framing it is a tolerable context loss, monitored rather than fixed.

**Priority**: low.

---

## Lessons Learned

### NER is the Wrong Primary Tool for Structured Zones

The single most important finding. NER is built for running text, where names appear at unpredictable positions and a statistical model is needed. In this corpus about 90% of identifying names sit in structurally fixed zones (submitter line, representative clause, signature). Throwing a probabilistic tool at a largely deterministic problem produced 75% recall; deterministic anchors plus NER on the remainder produced 92%. The lever was the architecture, not the model.

Pattern: before reaching for a bigger model, check whether the task is actually model-shaped. If the target sits in a fixed structure, an anchor is a 1.0-recall operation where NER guesses.

---

### Model Size is Not a Substitute for the Right Architecture

de_core_news_lg over md gained nothing (75.4% vs ~75%), only shifted which names leaked. A stronger flat model would not have solved a structural mismatch. The negative lg result was the evidence that justified the layered approach.

Pattern: a model swap that shifts errors without reducing them is a signal that the problem is structural, not capacity-bound.

---

### Separate the Two Axes, Never Average Them

Recall (leaked names) and precision (destroyed substantive terms) are opposite failure modes with opposite costs. A single averaged score would have hidden that flat NER was simultaneously leaking real names and masking street names. Reporting them separately made each failure visible and made the data-driven decision to skip Schicht 2 possible.

---

### Pinned Model Makes N=1 Valid

The Weg-B dependency pinning (the spaCy model as a versioned wheel) makes the NER output deterministic. Unlike the extraction evaluation, where temperature-1.0 models needed repeat runs for variance bounds, the masker is reproducible, so a single run per configuration is a valid measurement. The pinning decision paid off twice: reproducibility and stable golden evaluation.

---

### State Mismatches Are the Recurring Process Failure

Two incidents (unsaved GT, stale results.json) produced misleading evaluation output that nearly led to wrong conclusions (a fixed bug appearing unfixed, phantom leaks appearing real). Both were caught only by direct comparison against the real file. This is the same class as the extraction evaluation's MODEL_NAME mislabeling.

The fix going forward is cheap and is the recommended next infrastructure step: persist the git short-sha into results.json, and encode the model identifier in the result filename so md and lg runs coexist instead of overwriting. Neither was in place during this evaluation; both would have caught the incidents in seconds.

---

## Methodological Limitations

### No Pre-Registration

No iteration plan, hypothesis, or stop criterion was committed before measurement. The anchor hypothesis was derived from the flat-NER error analysis (HARKing). For the demo the story is honest (the error structure was analysed before the architecture was built), but the process was ad-hoc, not pre-registered.

### Single Overwriting Result File

results.json is overwritten on each run. The md baseline was overwritten by the lg run; the two could not be compared side by side after the fact without re-running. No git-sha or model identifier was persisted. Reproducibility insurance is missing for past runs and is the first infrastructure fix to add.

### Synthetic Corpus, NAME-Only Coverage

All 20 documents are synthetic. Real authority documents will differ (OCR noise, inconsistent headers, free-form emails without the "Einreicher:" anchor the layered approach relies on). The corpus contains no phone, email, or IBAN instances, so those recognizers (including the carefully fixed phone regex) are not exercised by this ground truth. To test them, documents with those PII types would need to be added.

### De-Identification, Not Anonymisation

NER-based masking is de-identification, not anonymisation (cf. Pilán/Lison TAB; Staab et al. 2023 on LLM re-identification from residual context). The residual risk from quasi-identifiers and the encapsulated-LLM threat model are documented in DATA_GOVERNANCE_STATEMENT.md, not solved by more masking. 92% recall is positioned as internal data minimization under an encapsulated deployment, not as a re-identification guarantee against a third party.

---

## Artifact Directory

**Ground truth and scripts**:
- experiments/pii_evaluation/ground_truth.json: per-document names_must_mask and must_survive (Weg A)
- experiments/pii_evaluation/run_masker.py: runs the masker over the corpus, serializes results.json
- experiments/pii_evaluation/evaluate.py: compares results against GT, reports recall and precision separately
- experiments/pii_evaluation/results.json: generated output (current state: 92.3% recall, 98.0% precision)

**Production code**:
- src/app/core/results.py: MaskingResult DTO
- src/app/core/protocols.py: PiiMasker protocol
- src/app/document_ingestion/zone_extractor.py: anchor-based name extraction
- src/app/document_ingestion/presidio_masker.py: layered masker, the only module importing Presidio
- pyproject.toml: pinned presidio-analyzer, presidio-anonymizer, de_core_news_md wheel

**Documentation**:
- docs/decisions/adr-025-pii-masking-layered.md: the decision record
- DATA_GOVERNANCE_STATEMENT.md: the data-protection reasoning

---

## Next Steps

1. Wire the masker into DocumentIngestionService (replace the pass-through; store raw first, then mask; carry entity_counts to the audit).
2. Add the FakePiiMasker to conftest and the service unit tests plus zone_extractor unit tests.
3. Reproducibility fixes: git-sha in results.json, model identifier in the result filename.
4. Reconcile ADR-010 against the implemented masker (the originally planned LLM masking pass was not built).
5. Resolve the ADR numbering collision (the observability document called the audit-write-failure policy "ADR-025 candidate"; ADR-025 is now the PII masking record). Resolved in round 15: the audit-write-failure policy is ADR-027 and the observability logging policy is ADR-026.