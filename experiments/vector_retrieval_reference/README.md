# Vector retrieval: evaluated, rejected, kept as reference

This directory holds the vector-search implementation the Retrieval bounded
context evaluated against the production exact-match resolver and deliberately
rejected. It is reference code, not part of the production path.

- `e5_embedder.py`: multilingual-e5-large wrapper (the `Embedder` interface and
  its implementation) producing L2-normalised passage and query embeddings.
- `faiss_norm_index.py`: a faiss inner-product index over paragraph embeddings
  with Gesetz-filtered top-k search.

## Why it is here and not in production

Production resolves canonical norm citations by exact dictionary lookup on the
paragraph-level key (`src/app/retrieval/service.py`). Measured against the 25
unique citations in the Phase A ground truth, exact-match resolved 25/25 and the
vector fallback resolved 0; worse, the fallback returned a confident-wrong match
(`§ 999 WHG` to `§ 105 WHG` at cosine 0.801) on an out-of-corpus probe, the worst
failure mode in a Behörde context. The full decision, the per-Gesetz breakdown,
and the rejected alternatives are in [ADR-021](../../docs/decisions/adr-021-exact-match-norm-resolution.md)
and `docs/RETRIEVAL_EVAL_RESULTS.md`.

These modules lived in `src/app/retrieval/` through Round 17. Round 20 (M2) moved
them here so the production package no longer carries torch, faiss, and
sentence-transformers for code that never runs, and so the context's name
matches what it does: exact lookup. The faiss exploration spikes that informed
the evaluation are the sibling `../faiss_retreival_spikes/`.

## Running it

The heavy dependencies are an opt-in extra, not production dependencies:

```
pip install -e .[vector-experiments]
```

`faiss_norm_index.py` imports `GesetzParagraph` from the production context
(`app.retrieval.entities`), the one type it shares with the live path, so the
app package must be importable.

## Reversibility

The decision is reversible (ADR-021). If production data later shows genuine
drift that exact-match misses, the hybrid path is reinstated from here with a
confidence floor calibrated well above the observed 0.801 false-positive level;
the calibration data does not yet exist because no real citation has needed the
fallback.
