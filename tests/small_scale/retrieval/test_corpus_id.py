"""Behaviour tests for the content-based corpus identifier (ADR-028).

The corpus id must identify the legal content of the loaded corpus: the same
paragraphs in any load order give the same id, while any change to a
paragraph's text, its canonical key, or the set of paragraphs gives a
different id. Small tests: pure hashing over in-memory entities, no XML.
"""

from __future__ import annotations

from app.retrieval.entities import GesetzParagraph, LoadedCorpus
from app.retrieval.gesetz_xml_loader import compute_corpus_id
from app.retrieval.service import NormRetrievalService


def _paragraph(
    key: str = "§ 9 BauGB", text: str = "Im Bebauungsplan ..."
) -> GesetzParagraph:
    """Build a corpus paragraph with the fields the id is computed from."""
    section, gesetz = key.rsplit(" ", 1)
    return GesetzParagraph(
        gesetz=gesetz,
        paragraph=section,
        canonical_key=key,
        title="",
        text=text,
    )


_CORPUS = [
    _paragraph("§ 9 BauGB", "Im Bebauungsplan können festgesetzt werden ..."),
    _paragraph("§ 1 BauGB", "Aufgabe der Bauleitplanung ist es ..."),
    _paragraph("§ 50 BImSchG", "Bei raumbedeutsamen Planungen ..."),
]


def test_corpus_id_is_independent_of_load_order():
    # Given: the same paragraphs in two different orders
    reversed_corpus = list(reversed(_CORPUS))

    # When: the id is computed over both
    # Then: it is identical, so file iteration order cannot change provenance
    assert compute_corpus_id(_CORPUS) == compute_corpus_id(reversed_corpus)


def test_corpus_id_changes_on_a_one_character_text_change():
    # Given: a corpus whose first paragraph text differs by one character
    amended = [
        _paragraph("§ 9 BauGB", "Im Bebauungsplan können festgesetzt werden ..!"),
        *_CORPUS[1:],
    ]

    # When/Then: a pure text amendment changes the id
    assert compute_corpus_id(_CORPUS) != compute_corpus_id(amended)


def test_corpus_id_changes_on_a_canonical_key_change():
    # Given: a corpus where one paragraph carries a different canonical key
    rekeyed = [
        _paragraph("§ 9a BauGB", "Im Bebauungsplan können festgesetzt werden ..."),
        *_CORPUS[1:],
    ]

    # When/Then: the keys are part of the identity, not only the texts
    assert compute_corpus_id(_CORPUS) != compute_corpus_id(rekeyed)


def test_corpus_id_changes_when_a_paragraph_is_removed():
    # Given: the same corpus with one paragraph missing
    truncated = _CORPUS[:-1]

    # When/Then: a missing or corrupt paragraph is detectable
    assert compute_corpus_id(_CORPUS) != compute_corpus_id(truncated)


def test_service_source_revision_is_the_loaded_corpus_hash():
    # Given: a retrieval service built from a loaded corpus and its hash
    corpus_hash = compute_corpus_id(_CORPUS)
    service = NormRetrievalService(
        LoadedCorpus(paragraphs=_CORPUS, corpus_id=corpus_hash)
    )

    # When/Then: the protocol's source_revision is exactly the corpus hash, so
    # the generalized contract term still carries the corpus identity (ADR-028,
    # M2): the term generalized, the value did not change.
    assert service.source_revision == corpus_hash
