"""Protocols for the Retrieval bounded context.

Holds the abstract interface the Coordinator depends on to resolve norm
citations. The Retriever protocol lives here, with the context that owns its
return type (NormWithSource) and its provenance semantics, rather than in the
shared kernel: the briefing provenance the Coordinator stamps is
retrieval-specific knowledge, so it is context-owned (ADR-028, K1). The
Coordinator imports this protocol from retrieval directly, which is its role
as composition root.
"""

from __future__ import annotations

from typing import Protocol

from app.retrieval.entities import NormWithSource


class Retriever(Protocol):
    """Resolves canonical norm citations to their source Gesetzestext.

    Implemented by the Retrieval context's NormRetrievalService. The
    Coordinator depends on this Protocol rather than the concrete service,
    so tests can substitute a fake without a statute corpus.

    The retriever owns the source identity: source_revision is the content
    identifier of whatever source the implementation actually resolves
    against, and the Coordinator reads the provenance it stamps into briefings
    from here rather than taking a free string parameter that could lie
    (ADR-028). The term is deliberately the neutral source_revision, not
    corpus_id: a future non-corpus retriever (a database snapshot, an API
    revision) can honor the contract without the term cementing a corpus-hash
    semantics it could not satisfy (M2). The current corpus-based
    implementation returns its SHA-256 corpus hash as the source_revision.
    """

    @property
    def source_revision(self) -> str:
        """Content identifier of the source this retriever resolves against."""
        ...

    def resolve(self, citations: list[str]) -> list[NormWithSource]:
        """Resolve canonical norm citations to their source Gesetzestext."""
        ...
