"""Unit tests for DocumentIngestion bounded context."""

from pathlib import Path

import pytest
from tests.conftest import FakePiiMasker

from app.core.failures import IngestionError
from app.document_ingestion.service import (
    MAX_RAW_TEXT_CHARS,
    DocumentIngestionService,
)


class TestDocumentIngestionService:
    def test_should_return_ingestion_result_for_valid_text(
        self, tmp_path: Path
    ) -> None:
        # Given a DocumentIngestionService with a pass-through masker
        service = DocumentIngestionService(
            raw_store_path=tmp_path, masker=FakePiiMasker()
        )
        raw_text = "Eine Einwendung gegen das Bauvorhaben."

        # When ingest is called
        result = service.ingest(raw_text)

        # Then a valid IngestionResult is returned
        assert result.document_id
        assert result.clean_text == raw_text
        assert result.raw_document_path

    def test_should_assign_unique_document_id_per_call(self, tmp_path: Path) -> None:
        # Given a DocumentIngestionService
        service = DocumentIngestionService(
            raw_store_path=tmp_path, masker=FakePiiMasker()
        )

        # When ingest is called twice
        result_a = service.ingest("Text A")
        result_b = service.ingest("Text B")

        # Then each result has a unique document_id
        assert result_a.document_id != result_b.document_id

    def test_should_raise_ingestion_error_for_empty_text(self, tmp_path: Path) -> None:
        # Given a DocumentIngestionService
        service = DocumentIngestionService(
            raw_store_path=tmp_path, masker=FakePiiMasker()
        )

        # When ingest is called with empty string
        # Then IngestionError is raised
        with pytest.raises(IngestionError):
            service.ingest("")

    def test_should_raise_ingestion_error_for_whitespace_only_text(
        self, tmp_path: Path
    ) -> None:
        # Given a DocumentIngestionService
        service = DocumentIngestionService(
            raw_store_path=tmp_path, masker=FakePiiMasker()
        )

        # When ingest is called with whitespace only
        # Then IngestionError is raised
        with pytest.raises(IngestionError):
            service.ingest("   ")

    def test_should_write_raw_unmasked_document_to_store(self, tmp_path: Path) -> None:
        # Given a service whose masker would replace a name
        service = DocumentIngestionService(
            raw_store_path=tmp_path,
            masker=FakePiiMasker(replacements={"Klaus Bertram": "[NAME]"}),
        )
        raw_text = "Einwendung von Klaus Bertram gegen das Vorhaben."

        # When ingest is called
        result = service.ingest(raw_text)

        # Then the stored raw document is the original unmasked text
        stored_path = Path(result.raw_document_path)
        assert stored_path.exists()
        assert stored_path.read_text(encoding="utf-8") == raw_text

    def test_should_mask_clean_text_via_masker(self, tmp_path: Path) -> None:
        # Given a service whose masker replaces a name with a placeholder
        masker = FakePiiMasker(replacements={"Klaus Bertram": "[NAME]"})
        service = DocumentIngestionService(raw_store_path=tmp_path, masker=masker)
        raw_text = "Einwendung von Klaus Bertram gegen das Vorhaben."

        # When ingest is called
        result = service.ingest(raw_text)

        # Then clean_text is the masked text, not the raw text
        assert result.clean_text == "Einwendung von [NAME] gegen das Vorhaben."
        assert "Klaus Bertram" not in result.clean_text

    def test_should_call_masker_with_raw_text(self, tmp_path: Path) -> None:
        # Given a service with a recording masker
        masker = FakePiiMasker()
        service = DocumentIngestionService(raw_store_path=tmp_path, masker=masker)
        raw_text = "Eine Einwendung gegen das Bauvorhaben."

        # When ingest is called
        service.ingest(raw_text)

        # Then the masker was called once with the raw text
        assert masker.mask_calls == [raw_text]

    def test_should_carry_entity_counts_into_result(self, tmp_path: Path) -> None:
        # Given a masker that masks one name occurrence
        masker = FakePiiMasker(replacements={"Klaus Bertram": "[NAME]"})
        service = DocumentIngestionService(raw_store_path=tmp_path, masker=masker)
        raw_text = "Einwendung von Klaus Bertram."

        # When ingest is called
        result = service.ingest(raw_text)

        # Then the result carries the masker's entity counts
        assert result.entity_counts == {"NAME": 1}

    def test_should_return_empty_entity_counts_when_no_pii(
        self, tmp_path: Path
    ) -> None:
        # Given a pass-through masker (no replacements)
        service = DocumentIngestionService(
            raw_store_path=tmp_path, masker=FakePiiMasker()
        )
        raw_text = "Eine Einwendung ohne personenbezogene Daten."

        # When ingest is called
        result = service.ingest(raw_text)

        # Then entity_counts is empty
        assert result.entity_counts == {}


class TestRawTextLengthBound:
    def test_should_raise_ingestion_error_for_text_over_length_limit(
        self, tmp_path: Path
    ) -> None:
        # Given a service and an input one character over the bound
        service = DocumentIngestionService(
            raw_store_path=tmp_path, masker=FakePiiMasker()
        )
        over_limit = "a" * (MAX_RAW_TEXT_CHARS + 1)

        # When ingest is called with over-limit text
        # Then IngestionError is raised (input validation at the edge)
        with pytest.raises(IngestionError):
            service.ingest(over_limit)

    def test_should_accept_text_at_length_limit(self, tmp_path: Path) -> None:
        # Given a service and an input exactly at the bound
        service = DocumentIngestionService(
            raw_store_path=tmp_path, masker=FakePiiMasker()
        )
        at_limit = "a" * MAX_RAW_TEXT_CHARS

        # When ingest is called with text at the limit
        result = service.ingest(at_limit)

        # Then it passes (the bound is inclusive)
        assert result.clean_text == at_limit
