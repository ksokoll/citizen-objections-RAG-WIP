"""FAISS spike: BauGB-Paragraphen-Embedding + juristische Argumentsuche.

Step 1 - Fünf BauGB/BauNVO-Paragraphen als hardcodierte Strings
Step 2 - FAISS-Index aus Paragraphen-Embeddings bauen
Step 3 - Similarity-Search mit simulierten argument_text-Werten
"""

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# Step 1 - BauGB/BauNVO-Paragraphen
# ---------------------------------------------------------------------------

PARAGRAPHEN: list[tuple[str, str]] = [
    (
        "baugb_§1_abs1",
        "§ 1 Abs. 1 BauGB – Aufgabe der Bauleitplanung: Es ist Aufgabe der "
        "Bauleitplanung, die bauliche und sonstige Nutzung der Grundstücke in "
        "der Gemeinde nach Maßgabe dieses Gesetzbuchs vorzubereiten und zu leiten. "
        "Bauleitpläne sind der Flächennutzungsplan (vorbereitender Bauleitplan) und "
        "der Bebauungsplan (verbindlicher Bauleitplan). Die Gemeinde hat die "
        "Bauleitpläne aufzustellen, sobald und soweit es für die städtebauliche "
        "Entwicklung und Ordnung erforderlich ist.",
    ),
    (
        "baugb_§3_abs1",
        "§ 3 Abs. 1 BauGB – Beteiligung der Öffentlichkeit: Die Öffentlichkeit ist "
        "möglichst frühzeitig über die allgemeinen Ziele und Zwecke der Planung, "
        "sich wesentlich unterscheidende Lösungen, die für die Neugestaltung oder "
        "Entwicklung eines Gebiets in Betracht kommen, und die voraussichtlichen "
        "Auswirkungen der Planung öffentlich zu unterrichten; ihr ist Gelegenheit "
        "zur Äußerung und Erörterung zu geben.",
    ),
    (
        "baugb_§8_abs2",
        "§ 8 Abs. 2 BauGB – Entwicklungsgebot: Bebauungspläne sind aus dem "
        "Flächennutzungsplan zu entwickeln. Aus dem Flächennutzungsplan können "
        "Bebauungspläne entwickelt werden, auch wenn der Flächennutzungsplan "
        "Aufstellungsbeschlüsse oder Darstellungen enthält, die die "
        "Bebauungsplanfläche umfassen. Im Parallelverfahren können "
        "Flächennutzungsplan und Bebauungsplan gleichzeitig aufgestellt werden.",
    ),
    (
        "baunvo_§11",
        "§ 11 BauNVO – Sonstige Sondergebiete (SO): Als sonstige Sondergebiete sind "
        "solche Gebiete darzustellen und festzusetzen, die sich von den Baugebieten "
        "nach den §§ 2 bis 10 wesentlich unterscheiden. Für sonstige Sondergebiete "
        "sind die Zweckbestimmung und die Art der Nutzung darzustellen und "
        "festzusetzen. Als sonstige Sondergebiete kommen insbesondere in Betracht: "
        "Gebiete für den Fremdenverkehr, Ladengebiete, Einkaufszentren und großflächige "
        "Handelsbetriebe, Messe-, Ausstellungs- und Kongressgebiete.",
    ),
    (
        "baugb_§34",
        "§ 34 BauGB – Zulässigkeit von Vorhaben innerhalb der im Zusammenhang bebauten "
        "Ortsteile: Innerhalb der im Zusammenhang bebauten Ortsteile ist ein Vorhaben "
        "zulässig, wenn es sich nach Art und Maß der baulichen Nutzung, der Bauweise "
        "und der Grundstücksfläche, die überbaut werden soll, in die Eigenart der "
        "näheren Umgebung einfügt und die Erschließung gesichert ist. Die Anforderungen "
        "an gesunde Wohn- und Arbeitsverhältnisse müssen gewahrt bleiben; das "
        "Ortsbild darf nicht beeinträchtigt werden.",
    ),
]

paragraph_ids = [pid for pid, _ in PARAGRAPHEN]
paragraph_texts = [text for _, text in PARAGRAPHEN]

# ---------------------------------------------------------------------------
# Step 2 - Modell laden, embedden, FAISS-Index bauen
# ---------------------------------------------------------------------------

model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
paragraph_embeddings = model.encode(
    paragraph_texts, convert_to_numpy=True, show_progress_bar=True
)

print(
    f"Embeddings: shape={paragraph_embeddings.shape}, dtype={paragraph_embeddings.dtype}"
)
print("First embedding (excerpt):", paragraph_embeddings[0, :6])

# IndexFlatIP expects float32 and unit-normalised vectors for cosine similarity.
vectors: np.ndarray = paragraph_embeddings.astype(np.float32)
faiss.normalize_L2(vectors)

dim = vectors.shape[1]
index = faiss.IndexFlatIP(dim)  # inner product == cosine on L2-normalised vectors
index.add(vectors)

print(f"\nFAISS index built: ntotal={index.ntotal}, d={index.d}")

# ---------------------------------------------------------------------------
# Step 3 - Simulierte argument_text-Queries (normalisierte juristische Argumente)
# ---------------------------------------------------------------------------

TEST_QUERIES: list[tuple[str, str]] = [
    (
        "Q-A",
        "Widerspruch zum Flächennutzungsplan gemäß Parallelverfahren: "
        "Der Bebauungsplan wurde nicht ordnungsgemäß aus dem Flächennutzungsplan "
        "entwickelt; das Entwicklungsgebot ist verletzt.",
    ),
    (
        "Q-B",
        "Fehlerhafte Abwägung der weinbaulichen Belange: Die Gemeinde hat die "
        "landwirtschaftlichen und weinbaulichen Interessen der Anlieger bei der "
        "Aufstellung des Bauleitplans nicht hinreichend berücksichtigt.",
    ),
    (
        "Q-C",
        "Fehlende Bürgerbeteiligung bei wesentlichen Planänderungen: Die Öffentlichkeit "
        "wurde über grundlegende Planänderungen nicht frühzeitig unterrichtet; "
        "eine erneute Auslegung hätte durchgeführt werden müssen.",
    ),
]

query_texts = [text for _, text in TEST_QUERIES]
query_vecs: np.ndarray = model.encode(query_texts, convert_to_numpy=True).astype(
    np.float32
)
faiss.normalize_L2(query_vecs)

# Retrieve top-3 paragraph matches per query
k = 3
distances, indices = index.search(query_vecs, k)

print()
for (qid, qtext), dists, idxs in zip(TEST_QUERIES, distances, indices):
    gap = dists[0] - dists[1]
    print(f'Query {qid}: "{qtext[:70]}..."  (gap #1-#2: {gap:.4f})')
    for rank, (idx, score) in enumerate(zip(idxs, dists), start=1):
        pid, ptext = PARAGRAPHEN[idx]
        print(f'  #{rank}  {pid}  score={score:.4f}  "{ptext[:70]}..."')
    print()
