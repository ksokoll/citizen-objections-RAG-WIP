"""FAISS spike: catalog embedding + similarity search.

Step 1 - Embed 5 Catalog entries
Step 2 - Build FAISS index and index the embeddings
Step 3 - Perform similarity search
"""

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# Step 1 - Catalog Entries
# ---------------------------------------------------------------------------

CATALOG_ENTRIES: list[tuple[str, str]] = [
    (
        "C-001",
        "Lärmschutz: Einwendung gegen unzumutbare Lärmimmissionen durch das "
        "Vorhaben im Wohn- und Mischgebiet (§ 41 BImSchG, DIN 18005).",
    ),
    (
        "C-002",
        "Verkehr und Erschließung: Einwendung gegen die Beeinträchtigung der "
        "Verkehrssicherheit und unzureichende Erschließungsqualität durch das "
        "geplante Bauvorhaben.",
    ),
    (
        "C-003",
        "Naturschutz und Grünflächen: Einwendung wegen Eingriffs in "
        "schutzwürdige Biotope, Grünflächen oder Ausgleichsflächen "
        "(§ 14 BNatSchG).",
    ),
    (
        "C-004",
        "Luftqualität und Emissionen: Einwendung gegen Schadstoff- und "
        "Feinstaubbelastung durch gewerbliche oder industrielle Nutzung "
        "(39. BImSchV, TA Luft).",
    ),
    (
        "C-005",
        "Stadtbild und Ortsgestaltung: Einwendung gegen eine Beeinträchtigung "
        "des Orts- und Landschaftsbildes sowie des baukulturellen Erbes "
        "(§ 35 BauGB).",
    ),
]

catalog_ids = [cid for cid, _ in CATALOG_ENTRIES]
catalog_texts = [text for _, text in CATALOG_ENTRIES]

# ---------------------------------------------------------------------------
# Step 2- load model and embed
# ---------------------------------------------------------------------------

model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
catalog_embeddings = model.encode(
    catalog_texts, convert_to_numpy=True, show_progress_bar=True
)

print(f"Embeddings: shape={catalog_embeddings.shape}, dtype={catalog_embeddings.dtype}")
print("First embedding (excerpt):", catalog_embeddings[0, :6])

# ---------------------------------------------------------------------------
# Step 3 - Build FAISS index
# ---------------------------------------------------------------------------

# IndexFlatIP expects float32 and unit-normalised vectors for cosine similarity.
vectors: np.ndarray = catalog_embeddings.astype(np.float32)
faiss.normalize_L2(vectors)

dim = vectors.shape[1]
index = faiss.IndexFlatIP(dim)  # inner product == cosine on L2-normalised vectors
index.add(vectors)

print(f"\nFAISS index built: ntotal={index.ntotal}, d={index.d}")

# ---------------------------------------------------------------------------
# Step 4 - Query test objections and inspect cosine similarity scores
# ---------------------------------------------------------------------------

TEST_QUERIES: list[tuple[str, str]] = [
    (
        "Q-A",
        "Der Baulärm während der Bauphase ist unerträglich. Wir wohnen direkt "
        "neben dem geplanten Gewerbegebiet und befürchten dauerhaften Lärm.",
    ),
    (
        "Q-B",
        "Die Zufahrtsstraße ist bereits jetzt überlastet. Ein weiteres "
        "Einkaufszentrum wird den Verkehr im Kreuzungsbereich kollabieren lassen.",
    ),
    (
        "Q-C",
        "Das Bauvorhaben zerstört eine wertvolle Feuchtwiese, die als Biotop "
        "für seltene Amphibienarten dient.",
    ),
    (
        "Q-D",
        "Der geplante Schornstein wird Feinstaub und Stickoxide direkt in "
        "unser Wohnviertel emittieren.",
    ),
]

query_texts = [text for _, text in TEST_QUERIES]
query_vecs: np.ndarray = model.encode(query_texts, convert_to_numpy=True).astype(
    np.float32
)
faiss.normalize_L2(query_vecs)

# Retrieve top-3 catalog matches per query
k = 3
distances, indices = index.search(query_vecs, k)

print()
for (qid, qtext), dists, idxs in zip(TEST_QUERIES, distances, indices):
    gap = dists[0] - dists[1]
    print(f'Query {qid}: "{qtext[:60]}..."  (gap #1-#2: {gap:.4f})')
    for rank, (idx, score) in enumerate(zip(idxs, dists), start=1):
        cid, ctext = CATALOG_ENTRIES[idx]
        print(f'  #{rank}  {cid}  score={score:.4f}  "{ctext[:60]}..."')
    print()
