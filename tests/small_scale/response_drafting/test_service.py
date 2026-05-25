"""Unit tests for ResponseDrafting bounded context."""

import uuid

from app.core.entities import (
    ExtrahiertesArgument,
    RetrievedChunk,
)
from app.core.results import TriageResult
from app.core.statuses import (
    AbwaegungsStatus,
    EinwendungsTyp,
    WuerdigungsStatus,
)
from app.response_drafting.service import ResponseDraftingService


class FakeLLMClient:
    """Fake LLMClient returning a fixed Würdigung string."""

    FIXED_RESPONSE = "Skeleton-Würdigung: Dies ist ein Platzhalter."

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        return self.FIXED_RESPONSE


class FakeRetriever:
    """Fake Retriever returning an empty chunk list."""

    def retrieve(
        self, query: str, partition: str, top_k: int = 5
    ) -> list[RetrievedChunk]:
        return []


def make_argument(
    catalog_id: str | None = "C-005",
    argument_verified: bool = True,
    einwendungs_typ: EinwendungsTyp = EinwendungsTyp.TYP_2,
) -> ExtrahiertesArgument:
    return ExtrahiertesArgument(
        argument_id=str(uuid.uuid4()),
        argument_text="Widerspruch zum Flächennutzungsplan.",
        original_zitat="Bebauungsplan weicht vom Flächennutzungsplan ab.",
        catalog_id=catalog_id,
        einwendungs_typ=einwendungs_typ,
        argument_verified=argument_verified,
    )


class TestResponseDraftingServiceSkeleton:
    def test_should_return_draft_status(self) -> None:
        # Given a ResponseDraftingService with stubs and one verified argument
        service = ResponseDraftingService(
            llm=FakeLLMClient(),
            retriever=FakeRetriever(),
            model_version="skeleton-v0.1",
        )
        triage_result = TriageResult(
            einwendungs_typ=EinwendungsTyp.TYP_2,
            extracted_arguments=[make_argument()],
        )

        # When draft is called
        result = service.draft(triage_result, "Einwendungstext.", "doc-001")

        # Then status is DRAFT
        assert result.status == AbwaegungsStatus.DRAFT

    def test_should_set_all_reproducibility_fields(self) -> None:
        # Given a ResponseDraftingService
        service = ResponseDraftingService(
            llm=FakeLLMClient(),
            retriever=FakeRetriever(),
            model_version="skeleton-v0.1",
        )
        triage_result = TriageResult(
            einwendungs_typ=EinwendungsTyp.TYP_2,
            extracted_arguments=[make_argument()],
        )

        # When draft is called
        result = service.draft(triage_result, "Einwendungstext.", "doc-001")

        # Then all reproducibility fields are non-empty
        assert result.model_version == "skeleton-v0.1"
        assert result.prompt_version
        assert result.retrieval_config_hash

    def test_should_skip_argument_with_no_catalog_id(self) -> None:
        # Given a TriageResult with one unmatched argument (catalog_id=None)
        service = ResponseDraftingService(
            llm=FakeLLMClient(),
            retriever=FakeRetriever(),
            model_version="skeleton-v0.1",
        )
        triage_result = TriageResult(
            einwendungs_typ=EinwendungsTyp.TYP_2,
            extracted_arguments=[make_argument(catalog_id=None)],
        )

        # When draft is called
        result = service.draft(triage_result, "Einwendungstext.", "doc-001")

        # Then no arguments are processed
        assert result.argumente == []

    def test_should_skip_unverified_argument(self) -> None:
        # Given a TriageResult with one unverified argument
        service = ResponseDraftingService(
            llm=FakeLLMClient(),
            retriever=FakeRetriever(),
            model_version="skeleton-v0.1",
        )
        triage_result = TriageResult(
            einwendungs_typ=EinwendungsTyp.TYP_2,
            extracted_arguments=[make_argument(argument_verified=False)],
        )

        # When draft is called
        result = service.draft(triage_result, "Einwendungstext.", "doc-001")

        # Then no arguments are processed
        assert result.argumente == []

    def test_should_set_wuerdigungs_status_generiert_for_verified_argument(
        self,
    ) -> None:
        # Given a TriageResult with one verified argument
        service = ResponseDraftingService(
            llm=FakeLLMClient(),
            retriever=FakeRetriever(),
            model_version="skeleton-v0.1",
        )
        triage_result = TriageResult(
            einwendungs_typ=EinwendungsTyp.TYP_2,
            extracted_arguments=[make_argument()],
        )

        # When draft is called
        result = service.draft(triage_result, "Einwendungstext.", "doc-001")

        # Then the argument has status GENERIERT
        assert result.argumente[0].wuerdigungs_status == WuerdigungsStatus.GENERIERT

    def test_should_set_fixed_stub_wuerdigung_text(self) -> None:
        # Given a ResponseDraftingService with FakeLLMClient
        service = ResponseDraftingService(
            llm=FakeLLMClient(),
            retriever=FakeRetriever(),
            model_version="skeleton-v0.1",
        )
        triage_result = TriageResult(
            einwendungs_typ=EinwendungsTyp.TYP_2,
            extracted_arguments=[make_argument()],
        )

        # When draft is called
        result = service.draft(triage_result, "Einwendungstext.", "doc-001")

        # Then rechtliche_wuerdigung is the stub response
        assert result.argumente[0].rechtliche_wuerdigung == FakeLLMClient.FIXED_RESPONSE

    def test_should_return_kein_treffer_when_no_arguments(self) -> None:
        # Given a TriageResult with no arguments
        service = ResponseDraftingService(
            llm=FakeLLMClient(),
            retriever=FakeRetriever(),
            model_version="skeleton-v0.1",
        )
        triage_result = TriageResult(
            einwendungs_typ=EinwendungsTyp.TYP_1,
            extracted_arguments=[],
        )

        # When draft is called
        result = service.draft(triage_result, "Einwendungstext.", "doc-001")

        # Then wuerdigungs_status is KEIN_TREFFER
        assert result.wuerdigungs_status == WuerdigungsStatus.KEIN_TREFFER
