# tests/external/test_triage_smoke.py
"""Smoke tests of the Triage pipeline against the real OpenAI API.

Not part of the default test suite because each run incurs API cost
and is non-deterministic. Invoke explicitly via:

    pytest tests/external/ -m external --run-external

Requires OPENAI_API_KEY in the environment.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services.llm.openai_client import OpenAIClient
from app.triage.service import TriageService

pytestmark = pytest.mark.external


@pytest.fixture
def triage_service() -> TriageService:
    """Real TriageService wired with a real OpenAI client."""
    if "OPENAI_API_KEY" not in os.environ:
        pytest.skip("OPENAI_API_KEY not set, skipping external smoke test")
    return TriageService(llm=OpenAIClient())


class TestTriageSmokeAgainstRealLLM:
    def test_should_extract_naturschutz_arguments_for_einspruch_14(
        self, triage_service: TriageService
    ) -> None:
        # Given the NABU naturschutz objection text
        text = Path(
            "experiments/extraction_evaluation/data/typ2/einspruch_14.txt"
        ).read_text(encoding="utf-8")

        # When triaging via the real OpenAI pipeline
        result = triage_service.triage(text)
        arguments = result.extracted_arguments

        # Then at least one C-004 argument is extracted with valid norms
        assert len(arguments) >= 1
        catalog_ids = {arg.catalog_id for arg in arguments}
        assert "C-004" in catalog_ids
        all_norms = [n for arg in arguments for n in arg.zitierte_normen]
        assert any("BNatSchG" in n for n in all_norms)

    def test_should_return_empty_for_typ1_einspruch_05(
        self, triage_service: TriageService
    ) -> None:
        # Given a TYP_1 informal citizen letter
        text = Path(
            "experiments/extraction_evaluation/data/typ1/einspruch_05.txt"
        ).read_text(encoding="utf-8")

        # When triaging via the real OpenAI pipeline
        result = triage_service.triage(text)

        # Then no arguments are extracted (pre-check filters TYP_1)
        assert result.extracted_arguments == []
