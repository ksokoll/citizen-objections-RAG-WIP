"""FAISS spike C: Hybrid Retrieval BM25 + Dense + RRF (ADR-003 Validation).

Spike C: Testet ob BM25 + Dense + Reciprocal Rank Fusion die Retrieval-
Diskrimination wiederherstellt die Dense-only (Spike v2, Spike B) nicht
erreicht. Gleiche Queries, gleiche Chunks wie Spike B.

Hypothese: BM25 matcht exakte Rechtsbegriffe (Flächennutzungsplan,
Abwägung, Bürgerbeteiligung) und setzt damit die korrekten Paragraphen
auf Platz 1, wo Dense allein versagt.
"""

import re
import xml.etree.ElementTree as ET
from collections import defaultdict

import faiss
import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# Step 1 - BauGB-XML parsen (identisch zu Spike B)
# ---------------------------------------------------------------------------


def parse_baugb(path: str) -> list[tuple[str, str]]:
    """Extrahiert (chunk_id, absatztext) aus dem BauGB-XML.

    Args:
        path: Pfad zur XML-Datei.

    Returns:
        Liste von (chunk_id, text)-Tupeln, ein Chunk pro Absatz.
    """
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


chunks = parse_baugb("XML/Baugesetzbuch.xml")
chunk_ids = [cid for cid, _ in chunks]
chunk_texts = [text for _, text in chunks]
print(f"Chunks geladen: {len(chunks)}")

# ---------------------------------------------------------------------------
# Step 2 - BM25 Index
# ---------------------------------------------------------------------------

tokenized = [text.lower().split() for text in chunk_texts]
bm25 = BM25Okapi(tokenized)
print("BM25 index built.")

# ---------------------------------------------------------------------------
# Step 3 - Dense Index (identisch zu Spike B)
# ---------------------------------------------------------------------------

model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
print(f"Embedde {len(chunks)} Chunks...")
embeddings = model.encode(
    chunk_texts, convert_to_numpy=True, show_progress_bar=True, batch_size=32
)
vectors = embeddings.astype(np.float32)
faiss.normalize_L2(vectors)
dim = vectors.shape[1]
index = faiss.IndexFlatIP(dim)
index.add(vectors)
print(f"FAISS index built: ntotal={index.ntotal}, d={index.d}")

# ---------------------------------------------------------------------------
# Step 4 - Reciprocal Rank Fusion
# ---------------------------------------------------------------------------


def rrf(rankings: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion über mehrere Ranking-Listen.

    Args:
        rankings: Liste von Ranking-Listen (jeweils sortierte Indizes).
        k: RRF-Konstante, Standard 60.

    Returns:
        Liste von (index, rrf_score) sortiert absteigend nach Score.
    """
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, idx in enumerate(ranking, start=1):
            scores[idx] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# Step 5 - Queries
# ---------------------------------------------------------------------------

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

top_n = 50  # Kandidaten pro Retriever vor RRF

query_texts = [t for _, t in TEST_QUERIES]
query_vecs = model.encode(query_texts, convert_to_numpy=True).astype(np.float32)
faiss.normalize_L2(query_vecs)

_, dense_indices = index.search(query_vecs, top_n)

print()
for (qid, qtext), d_idxs, query_text in zip(TEST_QUERIES, dense_indices, query_texts):
    # BM25 Top-N
    bm25_scores = bm25.get_scores(query_text.lower().split())
    bm25_ranking = list(np.argsort(bm25_scores)[::-1][:top_n])

    # Dense Top-N
    dense_ranking = list(d_idxs)

    # RRF merge
    fused = rrf([bm25_ranking, dense_ranking])[:3]

    print(f'Query {qid}: "{qtext[:70]}..."')
    for rank, (idx, score) in enumerate(fused, start=1):
        cid = chunk_ids[idx]
        preview = chunk_texts[idx][:70]
        print(f'  #{rank}  {cid}  rrf={score:.4f}  "{preview}..."')
    print()

# ---------------------------------------------------------------------------
# Diagnose: Wo landet §1 Abs. 7 für Q-B in Top-20?
# ---------------------------------------------------------------------------

q_b_text = "Fehlerhafte Abwägung der weinbaulichen und landwirtschaftlichen Belange bei der Aufstellung des Bauleitplans."

bm25_scores = bm25.get_scores(q_b_text.lower().split())
bm25_ranking = list(np.argsort(bm25_scores)[::-1][:20])

q_b_vec = model.encode([q_b_text], convert_to_numpy=True).astype(np.float32)
faiss.normalize_L2(q_b_vec)
_, d_idxs = index.search(q_b_vec, 20)

fused_20 = rrf([bm25_ranking, list(d_idxs[0])])[:20]

print("Q-B Top-20 (Hybrid RRF):")
for rank, (idx, score) in enumerate(fused_20, start=1):
    cid = chunk_ids[idx]
    marker = " <-- ZIEL" if cid == "baugb_§1_abs7" else ""
    print(f"  #{rank:2d}  {cid}  rrf={score:.4f}{marker}")
