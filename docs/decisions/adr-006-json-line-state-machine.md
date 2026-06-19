## ADR-006: Hallucination Strategy and Verification Routing

**Status:** Accepted (revised)

**Context:** Two distinct hallucination modes exist. First: the LLM invents an argument not present in the source document, or maps a real argument to the wrong legal domain during extraction. Second: the LLM generates a paragraph reference in the Würdigung that is not present in the retrieved chunks. Per ADR-013, suppression operates per argument.

**Decision (Layer 1, argument verification):** Each `ExtrahiertesArgument` carries an `original_zitat` field containing a verbatim quote from the masked source text. A substring check against the source validates that the quote exists. Arguments failing this check are marked `argument_verified = False` and skip retrieval and generation. The corresponding audit event is `AuditEvent(type=ARGUMENT_UNVERIFIED)`.

**Decision (Layer 2, paragraph reference verification):** The generation prompt prohibits citing paragraphs outside the retrieved chunks. Post-hoc, every paragraph reference in the generated Würdigung is normalized to canonical form and matched against the retrieved `paragraph_id`s for that argument. Each `Rechtsgrundlage` carries `verified: bool`. If any `Rechtsgrundlage` for argument N is unverified, only argument N is suppressed: its per-argument status becomes `UNTERDRUECKT_UNVERIFIED` and its `rechtliche_wuerdigung` is set to `None`. Other arguments are unaffected. The corresponding audit event is `AuditEvent(type=RECHTSGRUNDLAGE_UNVERIFIED)`.

**Rationale:** Argument-level suppression matches the per-argument architecture from ADR-013. Suppressing the entire Abwägungsstellungnahme on a single bad reference would discard valid work on other arguments. Argument verification via verbatim quote is cheap (one substring check) and closes the most dangerous hallucination mode.

**Rejected Alternatives:** Document-level suppression: discards independent valid arguments. No argument verification: invented arguments produce legally incorrect Abwägungsstellungnahmen with no visible indicator.

**Consequences:** Argument verification runs before retrieval. The aggregate `wuerdigungs_status` on `Abwaegungsstellungnahme` is derived from the per-argument statuses: `GENERIERT` if at least one argument has a generated Würdigung, `UNTERDRUECKT_UNVERIFIED` if all generated Würdigungen are suppressed, `KEIN_TREFFER` if no arguments were extracted at all. `WuerdigungsStatus` retains exactly three values: `GENERIERT`, `UNTERDRUECKT_UNVERIFIED`, `KEIN_TREFFER`.

## Layer 1 robustness note (Round 19)

The substring check had a degenerate input the verbatim rule did not guard: an empty or whitespace-only `original_zitat`. `str.find("")` returns 0, not -1, so an empty quote counted as found at position 0 and the argument was marked `argument_verified = True` with no evidence at all. This is the cleanest fabrication case this control exists to catch, and a non-adversarial one too: any model that legitimately returns no quote for an argument was falsely verified. Reproduced: `'' -> find=0 -> is_verified=True -> BRIEFING_READY`.

The fix closes the bypass in both layers. The verification site (triage/service.py) guards the search on a non-empty quote, `is_verified = bool(zitat) and clean_text.find(zitat) != -1` after the existing strip, so the empty string can no longer be "found". The LLM-facing schema (triage/llm_schema.py) rejects an empty or whitespace-only `original_zitat` at construction, `min_length=1` plus a blank-rejecting validator, excluding the degenerate quote at the trust boundary; the find-logic guard is the backstop for any path that bypasses the schema. An empty quote now yields `argument_verified = False` and takes the `ZITAT_NICHT_VERIFIZIERT` path (ADR-028); it never reaches `BRIEFING_READY`.