"""FAISS spike v2: BauGB-Volltext-Parsing + Paragraphen-Retrieval.

Lädt das vollständige BauGB-XML, extrahiert alle Paragraphen,
baut einen FAISS-Index und testet Retrieval mit simulierten
argument_text-Queries aus ExtrahiertesArgument.
"""

import xml.etree.ElementTree as ET

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# Step 1 - BauGB-XML parsen
# ---------------------------------------------------------------------------


def parse_baugb(path: str) -> list[tuple[str, str]]:
    """Extrahiert (paragraph_id, volltext) aus dem BauGB-XML.

    Args:
        path: Pfad zur XML-Datei.

    Returns:
        Liste von (paragraph_id, text)-Tupeln.
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

        # Volltext aus allen Textnodes unterhalb Content
        full_text = " ".join(t.strip() for t in content_el.itertext() if t.strip())

        # Kanonische paragraph_id: "§ 8 Abs. 2" → "baugb_§8_abs2"
        paragraph_id = "baugb_" + enbez.lower().replace(" ", "").replace(".", "_")

        result.append((paragraph_id, f"{enbez} BauGB – {full_text}"))

    return result


paragraphen = parse_baugb("XML/Baugesetzbuch.xml")
print(f"Paragraphen geladen: {len(paragraphen)}")
print(f"Beispiel: {paragraphen[0][0]}: {paragraphen[0][1][:80]}...")

# ---------------------------------------------------------------------------
# Step 2 - Embeddings + FAISS-Index
# ---------------------------------------------------------------------------

paragraph_ids = [pid for pid, _ in paragraphen]
paragraph_texts = [text for _, text in paragraphen]

model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
print("Embedde 294 Paragraphen...")
embeddings = model.encode(
    paragraph_texts, convert_to_numpy=True, show_progress_bar=True, batch_size=32
)

vectors = embeddings.astype(np.float32)
faiss.normalize_L2(vectors)

dim = vectors.shape[1]
index = faiss.IndexFlatIP(dim)
index.add(vectors)
print(f"FAISS index built: ntotal={index.ntotal}, d={index.d}")

# ---------------------------------------------------------------------------
# Step 3 - Queries (simulierte argument_text-Werte)
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

query_texts = [t for _, t in TEST_QUERIES]
query_vecs = model.encode(query_texts, convert_to_numpy=True).astype(np.float32)
faiss.normalize_L2(query_vecs)

k = 3
distances, indices = index.search(query_vecs, k)

print()
for (qid, qtext), dists, idxs in zip(TEST_QUERIES, distances, indices):
    gap = dists[0] - dists[1]
    print(f'Query {qid}: "{qtext[:70]}..."  (gap: {gap:.4f})')
    for rank, (idx, score) in enumerate(zip(idxs, dists), start=1):
        pid = paragraph_ids[idx]
        preview = paragraph_texts[idx][:70]
        print(f'  #{rank}  {pid}  score={score:.4f}  "{preview}..."')
    print()
