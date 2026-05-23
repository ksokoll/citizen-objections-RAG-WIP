"""Hybrid retrieval service: BM25 + FAISS + cross-encoder reranking.

Implements RetrieverProtocol for the ResponseDrafting BC.
Supports multiple corpus partitions, one per catalog cluster.
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer

from citizen_objections_rag.core.entities import RetrievedChunk


@dataclass(frozen=True)
class CorpusIndex:
    """In-memory hybrid index for a single corpus partition.

    Attributes:
        partition: Partition key (e.g. 'baugb', 'whg', 'bnatschg').
        gesetz: Display name for the law (e.g. 'BauGB').
        chunk_ids: Stable chunk identifiers, aligned with chunk_texts.
        chunk_texts: Chunk text content, aligned with chunk_ids.
        bm25: BM25 sparse index.
        faiss_index: FAISS dense index (IndexFlatIP on normalized vectors).
    """

    partition: str
    gesetz: str
    chunk_ids: list[str]
    chunk_texts: list[str]
    bm25: BM25Okapi
    faiss_index: faiss.Index


class HybridRetrievalService:
    """Retrieves top-K legal chunks per argument from partitioned corpora.

    Each catalog cluster maps to one corpus partition. Retrieval combines
    sparse and dense via RRF, then reranks with a cross-encoder.

    Args:
        corpus_dir: Directory containing one XML file per partition.
        embedder_model: SentenceTransformer model identifier.
        reranker_model: CrossEncoder model identifier.
        candidate_top_n: Hybrid stage candidate count before reranking.
        rerank_top_k: Final chunks returned per query.
    """

    def __init__(
        self,
        corpus_dir: Path,
        embedder_model: str = "paraphrase-multilingual-MiniLM-L12-v2",
        reranker_model: str = "BAAI/bge-reranker-v2-m3",
        candidate_top_n: int = 20,
        rerank_top_k: int = 5,
    ) -> None:
        self._candidate_top_n = candidate_top_n
        self._rerank_top_k = rerank_top_k
        self._embedder = SentenceTransformer(embedder_model)
        self._reranker = CrossEncoder(reranker_model)
        self._indices: dict[str, CorpusIndex] = self._load_corpora(corpus_dir)

    def retrieve(
        self,
        query: str,
        partition: str,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve top-K reranked chunks for a single argument query.

        Args:
            query: Argument text from ExtrahiertesArgument.argument_text.
            partition: Corpus partition (from KatalogEintrag.corpus_partition).
            top_k: Override the configured rerank_top_k.

        Returns:
            Chunks sorted by reranker score, highest first.

        Raises:
            ValueError: If partition is not loaded.
        """
        if partition not in self._indices:
            raise ValueError(
                f"Unknown partition '{partition}'. "
                f"Available: {sorted(self._indices.keys())}"
            )

        k = top_k if top_k is not None else self._rerank_top_k
        corpus = self._indices[partition]

        candidate_indices = self._hybrid_candidates(query, corpus)
        reranked = self._rerank(query, candidate_indices, corpus, k)

        return [
            RetrievedChunk(
                chunk_id=corpus.chunk_ids[idx],
                paragraph_id=corpus.chunk_ids[idx],
                gesetz=corpus.gesetz,
                text=corpus.chunk_texts[idx],
                score=float(score),
            )
            for idx, score in reranked
        ]

    # ----- Hybrid stage ---------------------------------------------------

    def _hybrid_candidates(self, query: str, corpus: CorpusIndex) -> list[int]:
        """Sparse + dense retrieval, fused via RRF."""
        bm25_scores = corpus.bm25.get_scores(query.lower().split())
        bm25_ranking = list(np.argsort(bm25_scores)[::-1][: self._candidate_top_n])

        query_vec = self._embedder.encode([query], convert_to_numpy=True).astype(
            np.float32
        )
        faiss.normalize_L2(query_vec)
        _, dense_indices = corpus.faiss_index.search(query_vec, self._candidate_top_n)
        dense_ranking = list(dense_indices[0])

        fused = _rrf([bm25_ranking, dense_ranking])[: self._candidate_top_n]
        return [idx for idx, _ in fused]

    # ----- Rerank stage ---------------------------------------------------

    def _rerank(
        self,
        query: str,
        candidate_indices: list[int],
        corpus: CorpusIndex,
        top_k: int,
    ) -> list[tuple[int, float]]:
        """Cross-encoder reranking, return top-K (index, score) pairs."""
        candidate_texts = [corpus.chunk_texts[i] for i in candidate_indices]
        pairs = [(query, t) for t in candidate_texts]
        scores = self._reranker.predict(pairs)
        return sorted(zip(candidate_indices, scores), key=lambda x: x[1], reverse=True)[
            :top_k
        ]

    # ----- Corpus loading -------------------------------------------------

    def _load_corpora(self, corpus_dir: Path) -> dict[str, CorpusIndex]:
        """Discover and index all XML corpora in the directory.

        Convention: filename without extension is the partition key.
        E.g. 'baugb.xml' -> partition 'baugb'.
        """
        # Implementation: iterate corpus_dir.glob("*.xml"),
        # call parser per file, build CorpusIndex per partition.
        ...


def _rrf(rankings: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion of multiple ranking lists."""
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, idx in enumerate(ranking, start=1):
            scores[idx] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
