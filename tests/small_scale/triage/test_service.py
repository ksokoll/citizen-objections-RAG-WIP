"""Unit tests for Triage bounded context: Service.

These tests drive `TriageService` through the real LLM code path by
configuring `FakeLLMClient.parse_response` with an explicit
`LLMTriageOutput`. The previous stub-implying naming
(`TestTriageServiceStub`) is replaced with behaviour-oriented names that
describe what each block verifies.
"""

import pytest
from pydantic import BaseModel

from app.core import EinwendungsTyp
from app.core.failures import LLMError, LLMParseError, TriageError
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
                        catalog_id="baugb",
                        einwendungs_typ=EinwendungsTyp.TYP_2,
                        argument_text="Widerspruch zum Flächennutzungsplan",
                        original_zitat=quote_one,
                    ),
                    LLMArgument(
                        catalog_id="baugb",
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
                        catalog_id="baugb",
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
                        catalog_id="baugb",
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
                        catalog_id="baugb",
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


class TestPromptDataFencing:
    """The Einwendung travels as fenced data with a precedence rule (S3)."""

    def test_prompt_wraps_the_einwendung_in_delimiters_with_precedence(
        self,
    ) -> None:
        # Given a triage service over a recording fake LLM
        fake_llm = FakeLLMClient(parse_response=LLMTriageOutput(argumente=[]))
        service = TriageService(llm=fake_llm)
        einwendung = "Der Plan verstößt gegen § 1 Abs. 7 BauGB."

        # When triage runs
        service.triage(einwendung)

        # Then the prompt fences the document between the start and end
        # markers (rindex: the preamble names the markers once before the
        # actual fence) and states the precedence rule before the fence
        prompt = fake_llm.parse_calls[0]["prompt"]
        start = prompt.rindex("<<<EINWENDUNG_START>>>")
        end = prompt.rindex("<<<EINWENDUNG_ENDE>>>")
        assert start < prompt.index(einwendung) < end
        assert "werden nicht befolgt" in prompt[:start]


class TestContradictionCheck:
    """Norms present with an empty argument list is a flagged contradiction (S3)."""

    def test_norms_present_with_empty_arguments_sets_the_flag(self) -> None:
        # Given a document citing a norm and an LLM returning no arguments
        fake_llm = FakeLLMClient(parse_response=LLMTriageOutput(argumente=[]))
        service = TriageService(llm=fake_llm)

        # When triage runs over a text with a deterministic norm citation
        result = service.triage("Der Plan verstößt gegen § 1 Abs. 7 BauGB.")

        # Then the contradiction is flagged on the result
        assert result.contradiction_detected is True

    def test_no_norms_with_empty_arguments_is_not_a_contradiction(self) -> None:
        # Given a text without norm citations and an LLM returning no arguments
        fake_llm = FakeLLMClient(parse_response=LLMTriageOutput(argumente=[]))
        service = TriageService(llm=fake_llm)

        # When triage runs (a legitimate TYP_1 no-substance document)
        result = service.triage("Ich bin einfach dagegen.")

        # Then no contradiction is flagged
        assert result.contradiction_detected is False

    def test_norms_present_with_arguments_is_not_a_contradiction(self) -> None:
        # Given a text citing a norm and an LLM extracting an argument from it
        quote = "Der Plan verstößt gegen § 1 Abs. 7 BauGB."
        fake_llm = FakeLLMClient(
            parse_response=LLMTriageOutput(
                argumente=[
                    LLMArgument(
                        catalog_id="baugb",
                        einwendungs_typ=EinwendungsTyp.TYP_2,
                        argument_text="Verstoß gegen das Abwägungsgebot",
                        original_zitat=quote,
                    ),
                ]
            )
        )
        service = TriageService(llm=fake_llm)

        # When triage runs
        result = service.triage(quote)

        # Then no contradiction is flagged
        assert result.contradiction_detected is False


class _RaisingLLMClient:
    """LLMClientProtocol double whose parse raises a configured failure.

    Models the documented seam contract: concrete clients translate every
    provider failure into LLMError or LLMParseError before it leaves the
    client (core/failures.py).
    """

    def __init__(self, error: Exception) -> None:
        self._error = error

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        raise self._error

    def parse(
        self,
        prompt: str,
        response_format: type[BaseModel],
        system_prompt: str = "",
    ) -> BaseModel:
        raise self._error


class TestContextBoundaryTranslation:
    """Infrastructure failures never leave the Triage context untranslated (S1)."""

    @pytest.mark.parametrize(
        "seam_error",
        [
            LLMError("provider failed: fragment of citizen input leaked here"),
            LLMParseError("schema mismatch: fragment of citizen input leaked here"),
        ],
        ids=["llm_error", "llm_parse_error"],
    )
    def test_should_translate_llm_failure_into_triage_error(
        self, seam_error: Exception
    ) -> None:
        # Given an LLM client that raises the documented seam failure class
        service = TriageService(llm=_RaisingLLMClient(seam_error))

        # When triage is called, then TriageError leaves the boundary, with
        # the original failure chained and its message (potential input
        # fragments) absent from the TriageError's own message
        with pytest.raises(TriageError) as exc_info:
            service.triage("Beliebiger Einwendungstext.")

        assert exc_info.value.__cause__ is seam_error
        assert type(seam_error).__name__ in str(exc_info.value)
        assert "fragment of citizen input" not in str(exc_info.value)
