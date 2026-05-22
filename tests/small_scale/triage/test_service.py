"""Unit tests for Triage bounded context: Service"""

from citizen_objections_rag.core.statuses import EinwendungsTyp
from citizen_objections_rag.triage.classification import classify_einwendungs_typ
from citizen_objections_rag.triage.service import TriageService


class FakeLLMClient:
    """Fake LLMClient for testing. Returns fixed string."""

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        return "[]"


class TestTriageServiceStub:
    def test_should_return_two_arguments_for_matching_text(self) -> None:
        # Given a TriageService with stubbed LLM and text containing both
        # stub zitate
        service = TriageService(llm=FakeLLMClient())
        text = (
            "Ein vorhabenbezogener Bebauungsplan, der von dieser "
            "Darstellung des Flächennutzungsplans abweicht. "
            "Die Öffentlichkeit wurde über grundlegende "
            "Planänderungen nicht frühzeitig unterrichtet."
        )

        # When triage is called
        result = service.triage(text)

        # Then two arguments are returned
        assert len(result.extracted_arguments) == 2

    def test_should_return_typ2_when_stub_contains_typ2_argument(self) -> None:
        # Given a TriageService with stubbed LLM
        service = TriageService(llm=FakeLLMClient())
        text = (
            "Ein vorhabenbezogener Bebauungsplan, der von dieser "
            "Darstellung des Flächennutzungsplans abweicht."
        )

        # When triage is called
        result = service.triage(text)

        # Then document-level type is TYP_2
        assert (
            classify_einwendungs_typ(result.extracted_arguments) == EinwendungsTyp.TYP_2
        )


class TestArgumentVerification:
    def test_should_mark_argument_verified_when_zitat_in_source(self) -> None:
        # Given a TriageService and text that contains both stub zitate
        service = TriageService(llm=FakeLLMClient())
        text = (
            "Ein vorhabenbezogener Bebauungsplan, der von dieser "
            "Darstellung des Flächennutzungsplans abweicht. "
            "Die Öffentlichkeit wurde über grundlegende "
            "Planänderungen nicht frühzeitig unterrichtet."
        )

        # When triage is called
        result = service.triage(text)

        # Then all arguments are verified
        assert all(a.argument_verified for a in result.extracted_arguments)

    def test_should_mark_argument_unverified_when_zitat_not_in_source(
        self,
    ) -> None:
        # Given a TriageService and text that does not contain the stub zitate
        service = TriageService(llm=FakeLLMClient())
        unrelated_text = "Kurzer Text ohne die Zitate."

        # When triage is called
        result = service.triage(unrelated_text)

        # Then all arguments are unverified
        assert all(not a.argument_verified for a in result.extracted_arguments)
