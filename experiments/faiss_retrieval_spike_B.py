"""FAISS spike B: BauGB-Absatz-Chunking + Paragraphen-Retrieval.

Spike B: Absatz-granulares Chunking statt Paragraph-granular.
Ein Chunk pro <P>-Element (Absatz), nicht pro <norm> (Paragraph).
Testet ob ADR-004-konforme Chunking-Granularität die Retrieval-
Diskrimination gegenüber Spike v2 verbessert.
"""

import re
import xml.etree.ElementTree as ET

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# Step 1 - BauGB-XML parsen: ein Chunk pro Absatz (<P>)
# ---------------------------------------------------------------------------


def parse_baugb(path: str) -> list[tuple[str, str]]:
    """Extrahiert (chunk_id, absatztext) aus dem BauGB-XML.

    Ein Chunk entspricht einem <P>-Element innerhalb von <Content>.
    chunk_id kodiert Paragraph und Absatznummer, z.B. baugb_§1_abs7.
    Jeder Chunk wird mit dem Paragraphen-Header präfigiert damit
    der Embedder den Kontext kennt.

    Args:
        path: Pfad zur XML-Datei.

    Returns:
        Liste von (chunk_id, text)-Tupeln.
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

            # Absatznummer aus "(1)", "(2)", ... extrahieren
            match = re.match(r"^\((\d+)\)", p_text)
            chunk_id = f"{base_id}_abs{match.group(1)}" if match else base_id

            result.append((chunk_id, f"{enbez} BauGB – {p_text}"))

    return result


chunks = parse_baugb("XML/Baugesetzbuch.xml")
print(f"Chunks geladen: {len(chunks)}")
print(f"Beispiel: {chunks[0][0]}: {chunks[0][1][:80]}...")

# Prüfe ob §1 Abs. 7 (Abwägungsgebot) als eigener Chunk vorhanden ist
abs7_chunks = [(cid, t) for cid, t in chunks if cid == "baugb_§1_abs7"]
print(f"\n§1 Abs. 7 Chunks gefunden: {len(abs7_chunks)}")
if abs7_chunks:
    print(f"  {abs7_chunks[0][1][:120]}...")

# ---------------------------------------------------------------------------
# Step 2 - Embeddings + FAISS-Index
# ---------------------------------------------------------------------------

chunk_ids = [cid for cid, _ in chunks]
chunk_texts = [text for _, text in chunks]

model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
print(f"\nEmbedde {len(chunks)} Absatz-Chunks...")
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
        cid = chunk_ids[idx]
        preview = chunk_texts[idx][:70]
        print(f'  #{rank}  {cid}  score={score:.4f}  "{preview}..."')
    print()
