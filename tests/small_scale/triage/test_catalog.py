"""Unit tests for TriageService."""

from citizen_objections_rag.triage.service import TriageService


class FakeLLMClient:
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
        service = TriageService(llm=FakeLLMClient())
        result = service.triage(SAMPLE_TEXT)
        assert len(result.extracted_arguments) == 2


class TestArgumentVerification:
    def test_should_mark_argument_verified_when_zitat_found_in_source(
        self,
    ) -> None:
        service = TriageService(llm=FakeLLMClient())
        result = service.triage(SAMPLE_TEXT)
        assert all(a.argument_verified for a in result.extracted_arguments)

    def test_should_mark_argument_unverified_when_zitat_not_in_source(
        self,
    ) -> None:
        service = TriageService(llm=FakeLLMClient())
        result = service.triage("Kurzer Text ohne die Zitate.")
        assert all(not a.argument_verified for a in result.extracted_arguments)
