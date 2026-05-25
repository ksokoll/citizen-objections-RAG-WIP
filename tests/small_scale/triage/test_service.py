"""Unit tests for Triage bounded context: Service.

These tests drive `TriageService` through the real LLM code path by
configuring `FakeLLMClient.parse_response` with an explicit
`LLMTriageOutput`. The previous stub-implying naming
(`TestTriageServiceStub`) is replaced with behaviour-oriented names that
describe what each block verifies.
"""

from app.core.statuses import EinwendungsTyp
from app.triage.classification import classify_einwendungs_typ
from app.triage.llm_schema import LLMArgument, LLMTriageOutput
from app.triage.service import TriageService
from tests.conftest import FakeLLMClient


class TestArgumentExtraction:
    """TriageService should return exactly the arguments the LLM produced."""

    def test_should_return_two_arguments_for_matching_text(self) -> None:
        # Given a fake LLM configured to return two arguments and a source
        # text containing both original_zitat substrings
        quote_one = (
            "Ein vorhabenbezogener Bebauungsplan, der von dieser "
            "Darstellung des Flächennutzungsplans abweicht."
        )
        quote_two = (
            "Die Öffentlichkeit wurde über grundlegende "
            "Planänderungen nicht frühzeitig unterrichtet."
        )
        text = f"{quote_one} {quote_two}"
        fake_llm = FakeLLMClient(
            parse_response=LLMTriageOutput(
                argumente=[
                    LLMArgument(
                        catalog_id="C-001",
                        einwendungs_typ=EinwendungsTyp.TYP_2,
                        argument_text="Widerspruch zum Flächennutzungsplan",
                        original_zitat=quote_one,
                    ),
                    LLMArgument(
                        catalog_id="C-007",
                        einwendungs_typ=EinwendungsTyp.TYP_2,
                        argument_text="Fehlende frühzeitige Beteiligung",
                        original_zitat=quote_two,
                    ),
                ]
            )
        )
        service = TriageService(llm=fake_llm)

        # When triage is called
        result = service.triage(text)

        # Then two arguments are returned
        assert len(result.extracted_arguments) == 2

    def test_should_return_typ2_when_llm_returns_typ2_argument(self) -> None:
        # Given a fake LLM configured to return a single TYP_2 argument
        quote = (
            "Ein vorhabenbezogener Bebauungsplan, der von dieser "
            "Darstellung des Flächennutzungsplans abweicht."
        )
        fake_llm = FakeLLMClient(
            parse_response=LLMTriageOutput(
                argumente=[
                    LLMArgument(
                        catalog_id="C-001",
                        einwendungs_typ=EinwendungsTyp.TYP_2,
                        argument_text="Widerspruch zum Flächennutzungsplan",
                        original_zitat=quote,
                    ),
                ]
            )
        )
        service = TriageService(llm=fake_llm)

        # When triage is called
        result = service.triage(quote)

        # Then document-level type is TYP_2
        assert (
            classify_einwendungs_typ(result.extracted_arguments) == EinwendungsTyp.TYP_2
        )


class TestArgumentVerification:
    def test_should_mark_argument_verified_when_zitat_in_source(self) -> None:
        # Given a fake LLM whose original_zitat is a substring of the source
        quote = (
            "Ein vorhabenbezogener Bebauungsplan, der von dieser "
            "Darstellung des Flächennutzungsplans abweicht."
        )
        text = f"Vorbemerkung. {quote} Weiterer Text."
        fake_llm = FakeLLMClient(
            parse_response=LLMTriageOutput(
                argumente=[
                    LLMArgument(
                        catalog_id="C-001",
                        einwendungs_typ=EinwendungsTyp.TYP_2,
                        argument_text="Widerspruch zum Flächennutzungsplan",
                        original_zitat=quote,
                    ),
                ]
            )
        )
        service = TriageService(llm=fake_llm)

        # When triage is called
        result = service.triage(text)

        # Then all arguments are verified
        assert all(a.argument_verified for a in result.extracted_arguments)

    def test_should_mark_argument_unverified_when_zitat_not_in_source(
        self,
    ) -> None:
        # Given a fake LLM whose original_zitat is NOT a substring of the source
        unrelated_text = "Kurzer Text ohne die Zitate."
        fake_llm = FakeLLMClient(
            parse_response=LLMTriageOutput(
                argumente=[
                    LLMArgument(
                        catalog_id="C-001",
                        einwendungs_typ=EinwendungsTyp.TYP_2,
                        argument_text="Widerspruch zum Flächennutzungsplan",
                        original_zitat="Ein vom LLM erfundenes Zitat",
                    ),
                ]
            )
        )
        service = TriageService(llm=fake_llm)

        # When triage is called
        result = service.triage(unrelated_text)

        # Then all arguments are unverified and carry no norms
        assert all(not a.argument_verified for a in result.extracted_arguments)
        assert all(a.zitierte_normen == [] for a in result.extracted_arguments)
