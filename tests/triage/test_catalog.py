"""Unit tests for TriageService."""

import uuid

from citizen_objections_rag.core.entities import ExtrahiertesArgument
from citizen_objections_rag.core.statuses import EinwendungsTyp
from citizen_objections_rag.triage.service import TriageService


class FakeLLMClient:
    """Fake LLMClient for testing. Returns fixed string."""

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        return "[]"


SAMPLE_TEXT = (
    "Ein vorhabenbezogener Bebauungsplan, der von dieser "
    "Darstellung des Flächennutzungsplans abweicht. "
    "Die Öffentlichkeit wurde über grundlegende "
    "Planänderungen nicht frühzeitig unterrichtet."
)


class TestTriageServiceStub:
    def test_should_return_two_arguments_for_sample_text(self) -> None:
        # Given a TriageService with stubbed LLM
        service = TriageService(llm=FakeLLMClient())

        # When triage is called with sample text containing two arguments
        result = service.triage(SAMPLE_TEXT)

        # Then two arguments are returned
        assert len(result.extracted_arguments) == 2

    def test_should_return_typ2_when_any_argument_is_typ2(self) -> None:
        # Given a TriageService with stubbed LLM
        service = TriageService(llm=FakeLLMClient())

        # When triage is called with a TYP_2 document
        result = service.triage(SAMPLE_TEXT)

        # Then the document-level type is TYP_2
        assert result.einwendungs_typ == EinwendungsTyp.TYP_2


class TestArgumentVerification:
    def test_should_mark_argument_verified_when_zitat_found_in_source(
        self,
    ) -> None:
        # Given a TriageService and a text that contains the stub zitate
        service = TriageService(llm=FakeLLMClient())

        # When triage is called with text containing both zitate
        result = service.triage(SAMPLE_TEXT)

        # Then all arguments are verified
        assert all(a.argument_verified for a in result.extracted_arguments)

    def test_should_mark_argument_unverified_when_zitat_not_in_source(
        self,
    ) -> None:
        # Given a TriageService and a text that does not contain the stub zitate
        service = TriageService(llm=FakeLLMClient())
        unrelated_text = "Kurzer Text ohne die Zitate."

        # When triage is called with unrelated text
        result = service.triage(unrelated_text)

        # Then all arguments are unverified
        assert all(not a.argument_verified for a in result.extracted_arguments)


class TestClassifyTyp:
    def test_should_return_typ1_for_empty_argument_list(self) -> None:
        # Given a TriageService
        service = TriageService(llm=FakeLLMClient())

        # When classify_typ is called with no arguments
        result = service._classify_typ([])

        # Then TYP_1 is returned
        assert result == EinwendungsTyp.TYP_1

    def test_should_return_typ1_when_all_arguments_are_typ1(self) -> None:
        # Given a list of TYP_1 arguments
        service = TriageService(llm=FakeLLMClient())
        arguments = [
            ExtrahiertesArgument(
                argument_id=str(uuid.uuid4()),
                argument_text="Informeller Einwand ohne Rechtsbezug.",
                original_zitat="Einwand",
                catalog_id="C-001",
                einwendungs_typ=EinwendungsTyp.TYP_1,
            )
        ]

        # When classify_typ is called
        result = service._classify_typ(arguments)

        # Then TYP_1 is returned
        assert result == EinwendungsTyp.TYP_1

    def test_should_return_typ2_when_any_argument_is_typ2(self) -> None:
        # Given a mixed list with one TYP_2 argument
        service = TriageService(llm=FakeLLMClient())
        arguments = [
            ExtrahiertesArgument(
                argument_id=str(uuid.uuid4()),
                argument_text="Informeller Einwand.",
                original_zitat="Einwand",
                catalog_id="C-001",
                einwendungs_typ=EinwendungsTyp.TYP_1,
            ),
            ExtrahiertesArgument(
                argument_id=str(uuid.uuid4()),
                argument_text="Juristischer Einwand mit § 8 BauGB.",
                original_zitat="§ 8 BauGB",
                catalog_id="C-005",
                einwendungs_typ=EinwendungsTyp.TYP_2,
            ),
        ]

        # When classify_typ is called
        result = service._classify_typ(arguments)

        # Then TYP_2 is returned
        assert result == EinwendungsTyp.TYP_2
