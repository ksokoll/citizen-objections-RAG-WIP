"""FAISS spike D: Cross-Encoder Reranking über Hybrid RRF Kandidaten.

Spike D: BAAI/bge-reranker-v2-m3 rerankt die Top-20 Hybrid-RRF-Kandidaten
aus Spike C. Test: landen §1 Abs. 7 (Q-B) und §8 Abs. 2 (Q-A) auf Rang 1?
"""

import re
import xml.etree.ElementTree as ET
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

import faiss
import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer

# ---------------------------------------------------------------------------
# Step 1 - Parse + Index (identisch zu Spike C)
# ---------------------------------------------------------------------------


def parse_baugb(path: str) -> list[tuple[str, str]]:
    tree = ET.parse(path)
    root = tree.getroot()
    result = []
    for norm in root.iter("norm"):
        enbez_el = norm.find(".//enbez")
        content_el = norm.find(".//Content")
        if enbez_el is None or content_el is None:
            continue
        enbez = (enbez_el.text or "").strip()
        if not enbez.startswith("§"):
            continue
        base_id = "baugb_" + enbez.lower().replace(" ", "").replace(".", "_")
        for p_el in content_el.iter("P"):
            p_text = " ".join(t.strip() for t in p_el.itertext() if t.strip())
            if not p_text:
                continue
            match = re.match(r"^\((\d+)\)", p_text)
            chunk_id = f"{base_id}_abs{match.group(1)}" if match else base_id
            result.append((chunk_id, f"{enbez} BauGB – {p_text}"))
    return result


def rrf(rankings: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, idx in enumerate(ranking, start=1):
            scores[idx] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


chunks = parse_baugb("XML/Baugesetzbuch.xml")
chunk_ids = [cid for cid, _ in chunks]
chunk_texts = [text for _, text in chunks]
print(f"Chunks: {len(chunks)}")

tokenized = [text.lower().split() for text in chunk_texts]
bm25 = BM25Okapi(tokenized)

embedder = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
embeddings = embedder.encode(
    chunk_texts, convert_to_numpy=True, show_progress_bar=True, batch_size=32
)
vectors = embeddings.astype(np.float32)
faiss.normalize_L2(vectors)
index = faiss.IndexFlatIP(vectors.shape[1])
index.add(vectors)
print(f"FAISS index built: ntotal={index.ntotal}")

# ---------------------------------------------------------------------------
# Step 2 - Cross-Encoder laden
# ---------------------------------------------------------------------------

print("\nLade Cross-Encoder BAAI/bge-reranker-v2-m3...")
reranker = CrossEncoder("BAAI/bge-reranker-v2-m3")
print("Cross-Encoder geladen.")

# ---------------------------------------------------------------------------
# Step 3 - Hybrid RRF Top-20 + Reranking
# ---------------------------------------------------------------------------

TARGETS = {
    "Q-A": ["baugb_§8_abs2"],
    "Q-B": ["baugb_§1_abs7", "baugb_§1_abs6"],
    "Q-C": ["baugb_§3_abs1"],
}

TEST_QUERIES = [
    (
        "Q-A",
        "Widerspruch zum Flächennutzungsplan: Bebauungsplan nicht ordnungsgemäß "
        "aus dem Flächennutzungsplan entwickelt, Entwicklungsgebot verletzt.",
    ),
    (
        "Q-B",
        "Fehlerhafte Abwägung der weinbaulichen und landwirtschaftlichen Belange "
        "bei der Aufstellung des Bauleitplans.",
    ),
    (
        "Q-C",
        "Fehlende Bürgerbeteiligung: Öffentlichkeit nicht frühzeitig über "
        "wesentliche Planänderungen unterrichtet.",
    ),
]

top_n = 20
query_vecs = embedder.encode(
    [t for _, t in TEST_QUERIES], convert_to_numpy=True
).astype(np.float32)
faiss.normalize_L2(query_vecs)
_, dense_indices = index.search(query_vecs, top_n)

print()
for (qid, qtext), d_idxs in zip(TEST_QUERIES, dense_indices):
    bm25_scores = bm25.get_scores(qtext.lower().split())
    bm25_ranking = list(np.argsort(bm25_scores)[::-1][:top_n])
    fused = rrf([bm25_ranking, list(d_idxs)])[:top_n]

    candidate_indices = [idx for idx, _ in fused]
    candidate_texts = [chunk_texts[i] for i in candidate_indices]

    # Reranking
    pairs = [(qtext, t) for t in candidate_texts]
    rerank_scores = reranker.predict(pairs)
    reranked = sorted(
        zip(candidate_indices, rerank_scores), key=lambda x: x[1], reverse=True
    )[:5]

    print(f'Query {qid}: "{qtext[:70]}..."')
    targets = TARGETS[qid]
    for rank, (idx, score) in enumerate(reranked, start=1):
        cid = chunk_ids[idx]
        marker = " <-- ZIEL" if cid in targets else ""
        preview = chunk_texts[idx][:65]
        print(f'  #{rank}  {cid}  rerank={score:.4f}  "{preview}...{marker}"')
    print()
