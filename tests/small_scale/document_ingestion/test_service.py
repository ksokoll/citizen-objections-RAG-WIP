"""Unit tests for DocumentIngestion bounded context."""

from pathlib import Path

import pytest

from citizen_objections_rag.core.failures import IngestionError
from citizen_objections_rag.document_ingestion.service import DocumentIngestionService


class TestDocumentIngestionService:
    def test_should_return_ingestion_result_for_valid_text(
        self, tmp_path: Path
    ) -> None:
        # Given a DocumentIngestionService with a temp store path
        service = DocumentIngestionService(raw_store_path=tmp_path)
        raw_text = "Eine Einwendung gegen das Bauvorhaben."

        # When ingest is called
        result = service.ingest(raw_text)

        # Then a valid IngestionResult is returned
        assert result.document_id
        assert result.clean_text == raw_text
        assert result.raw_document_path

    def test_should_assign_unique_document_id_per_call(self, tmp_path: Path) -> None:
        # Given a DocumentIngestionService
        service = DocumentIngestionService(raw_store_path=tmp_path)

        # When ingest is called twice
        result_a = service.ingest("Text A")
        result_b = service.ingest("Text B")

        # Then each result has a unique document_id
        assert result_a.document_id != result_b.document_id

    def test_should_raise_ingestion_error_for_empty_text(self, tmp_path: Path) -> None:
        # Given a DocumentIngestionService
        service = DocumentIngestionService(raw_store_path=tmp_path)

        # When ingest is called with empty string
        # Then IngestionError is raised
        with pytest.raises(IngestionError):
            service.ingest("")

    def test_should_raise_ingestion_error_for_whitespace_only_text(
        self, tmp_path: Path
    ) -> None:
        # Given a DocumentIngestionService
        service = DocumentIngestionService(raw_store_path=tmp_path)

        # When ingest is called with whitespace only
        # Then IngestionError is raised
        with pytest.raises(IngestionError):
            service.ingest("   ")

    def test_should_write_raw_document_to_store(self, tmp_path: Path) -> None:
        # Given a DocumentIngestionService
        service = DocumentIngestionService(raw_store_path=tmp_path)
        raw_text = "Eine Einwendung gegen das Bauvorhaben."

        # When ingest is called
        result = service.ingest(raw_text)

        # Then raw document is written to store path
        stored_path = Path(result.raw_document_path)
        assert stored_path.exists()
        assert stored_path.read_text(encoding="utf-8") == raw_text

    def test_should_pass_through_clean_text_in_skeleton(self, tmp_path: Path) -> None:
        # Given a DocumentIngestionService (skeleton: no PII masking)
        service = DocumentIngestionService(raw_store_path=tmp_path)
        raw_text = "Text mit Name: Dr. Klaus Bertram."

        # When ingest is called
        result = service.ingest(raw_text)

        # Then clean_text equals raw_text (pass-through)
        assert result.clean_text == raw_text
