"""Unit tests for TriageService against the catalog.

Verifies the end-to-end Triage path: an LLM-produced `LLMTriageOutput`
is mapped to internal `ExtrahiertesArgument` instances and the
original-zitat substring check (ADR-006 Layer 1) controls the
`argument_verified` flag.
"""

from app.core import EinwendungsTyp
from app.triage.llm_schema import LLMArgument, LLMTriageOutput
from app.triage.service import TriageService
from tests.conftest import FakeLLMClient

SAMPLE_TEXT = (
    "Ein vorhabenbezogener Bebauungsplan, der von dieser "
    "Darstellung des Flächennutzungsplans abweicht. "
    "Die Öffentlichkeit wurde über grundlegende "
    "Planänderungen nicht frühzeitig unterrichtet."
)

QUOTE_FLAECHENNUTZUNGSPLAN = (
    "Ein vorhabenbezogener Bebauungsplan, der von dieser "
    "Darstellung des Flächennutzungsplans abweicht."
)

QUOTE_OEFFENTLICHKEIT = (
    "Die Öffentlichkeit wurde über grundlegende "
    "Planänderungen nicht frühzeitig unterrichtet."
)


def _build_two_argument_output() -> LLMTriageOutput:
    return LLMTriageOutput(
        argumente=[
            LLMArgument(
                catalog_id="baugb",
                einwendungs_typ=EinwendungsTyp.TYP_2,
                argument_text="Widerspruch zum Flächennutzungsplan",
                original_zitat=QUOTE_FLAECHENNUTZUNGSPLAN,
            ),
            LLMArgument(
                catalog_id="baugb",
                einwendungs_typ=EinwendungsTyp.TYP_2,
                argument_text="Fehlende frühzeitige Beteiligung",
                original_zitat=QUOTE_OEFFENTLICHKEIT,
            ),
        ]
    )


class TestTriageWithValidLLMOutput:
    def test_should_return_two_arguments_for_sample_text(self) -> None:
        # Given a fake LLM returning two arguments whose zitate are in the source
        fake_llm = FakeLLMClient(parse_response=_build_two_argument_output())
        service = TriageService(llm=fake_llm)

        # When triage is called
        result = service.triage(SAMPLE_TEXT)

        # Then two arguments are returned
        assert len(result.extracted_arguments) == 2


class TestArgumentVerification:
    def test_should_mark_argument_verified_when_zitat_found_in_source(
        self,
    ) -> None:
        # Given a fake LLM whose zitate are both substrings of the source
        fake_llm = FakeLLMClient(parse_response=_build_two_argument_output())
        service = TriageService(llm=fake_llm)

        # When triage is called
        result = service.triage(SAMPLE_TEXT)

        # Then all arguments are verified
        assert all(a.argument_verified for a in result.extracted_arguments)

    def test_should_mark_argument_unverified_when_zitat_not_in_source(
        self,
    ) -> None:
        # Given a fake LLM returning a zitat that is NOT a substring of the source
        fake_llm = FakeLLMClient(
            parse_response=LLMTriageOutput(
                argumente=[
                    LLMArgument(
                        catalog_id="baugb",
                        einwendungs_typ=EinwendungsTyp.TYP_2,
                        argument_text="Widerspruch zum Flächennutzungsplan",
                        original_zitat=QUOTE_FLAECHENNUTZUNGSPLAN,
                    ),
                ]
            )
        )
        service = TriageService(llm=fake_llm)

        # When triage is called against unrelated text
        result = service.triage("Kurzer Text ohne die Zitate.")

        # Then all arguments are unverified and carry no norms
        assert all(not a.argument_verified for a in result.extracted_arguments)
        assert all(a.zitierte_normen == [] for a in result.extracted_arguments)
