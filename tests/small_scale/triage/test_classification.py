"""Unit tests for Triage bounded context: Classification"""

import uuid

from app.core.entities import ExtrahiertesArgument
from app.core.statuses import EinwendungsTyp
from app.triage.classification import classify_einwendungs_typ


class TestClassifyEinwendungsTyp:
    def test_should_return_typ1_for_empty_argument_list(self) -> None:
        # Given an empty argument list
        # When classify_einwendungs_typ is called
        result = classify_einwendungs_typ([])

        # Then TYP_1 is returned
        assert result == EinwendungsTyp.TYP_1

    def test_should_return_typ1_when_all_arguments_are_typ1(self) -> None:
        # Given a list of TYP_1 arguments
        arguments = [
            ExtrahiertesArgument(
                argument_id=str(uuid.uuid4()),
                argument_text="Informeller Einwand ohne Rechtsbezug.",
                original_zitat="Einwand",
                catalog_id="baugb",
                einwendungs_typ=EinwendungsTyp.TYP_1,
            )
        ]

        # When classify_einwendungs_typ is called
        result = classify_einwendungs_typ(arguments)

        # Then TYP_1 is returned
        assert result == EinwendungsTyp.TYP_1

    def test_should_return_typ2_when_any_argument_is_typ2(self) -> None:
        # Given a mixed list with one TYP_2 argument
        arguments = [
            ExtrahiertesArgument(
                argument_id=str(uuid.uuid4()),
                argument_text="Informeller Einwand.",
                original_zitat="Einwand",
                catalog_id="baugb",
                einwendungs_typ=EinwendungsTyp.TYP_1,
            ),
            ExtrahiertesArgument(
                argument_id=str(uuid.uuid4()),
                argument_text="Juristischer Einwand mit § 8 BauGB.",
                original_zitat="§ 8 BauGB",
                catalog_id="enwg",
                einwendungs_typ=EinwendungsTyp.TYP_2,
            ),
        ]

        # When classify_einwendungs_typ is called
        result = classify_einwendungs_typ(arguments)

        # Then TYP_2 is returned
        assert result == EinwendungsTyp.TYP_2
