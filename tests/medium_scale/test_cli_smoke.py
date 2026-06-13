"""End-to-end smoke for the CLI process command (python -m app process).

The first non-fixture wiring: real statute corpus, real Presidio masker,
real exact-match retrieval, real audit store. Only the Triage LLM is faked
through the documented CLI seam (_build_triage_llm), so the smoke runs
without a network call. Asserts the ADR-028 delivery contract on stdout:
parseable JSON, corpus_id, ISO-8601 UTC created_at, and the startup_config
provenance at the sink.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pytest
from tests.conftest import FakeLLMClient

import app.__main__ as cli
from app.core import EinwendungsTyp
from app.observability.logging_config import LOG_FILENAME
from app.observability_registry import STARTUP_CONFIG
from app.triage.llm_schema import LLMArgument, LLMTriageOutput

_XML_DIR = Path(__file__).parents[2] / "data" / "XML"

SAMPLE_EINWENDUNG = (
    "Sehr geehrte Damen und Herren, "
    "ein vorhabenbezogener Bebauungsplan, der von dieser Darstellung des "
    "Flächennutzungsplans abweicht, ist nicht zulässig. "
    "Die Öffentlichkeit wurde über grundlegende Planänderungen nicht "
    "frühzeitig unterrichtet."
)

_TRIAGE_OUTPUT = LLMTriageOutput(
    argumente=[
        LLMArgument(
            catalog_id="baugb",
            einwendungs_typ=EinwendungsTyp.TYP_2,
            argument_text="Widerspruch zum Flächennutzungsplan",
            original_zitat=(
                "ein vorhabenbezogener Bebauungsplan, der von dieser "
                "Darstellung des Flächennutzungsplans abweicht"
            ),
        ),
    ]
)


def test_process_prints_parseable_briefing_json_with_provenance(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process emits the serialized delivery contract and records its toolset.

    Given the production wiring with a faked Triage LLM, when process runs on
    one sample document, then stdout is parseable briefing JSON whose
    corpus_id is the SHA-256 of the loaded corpus and whose created_at is
    ISO-8601 UTC, the exit code is 0, and the sink carries one
    startup_config event with the same corpus_id and a git sha.
    """
    monkeypatch.setattr(
        cli,
        "_build_triage_llm",
        lambda: FakeLLMClient(parse_response=_TRIAGE_OUTPUT),
    )
    document = tmp_path / "einwendung.txt"
    document.write_text(SAMPLE_EINWENDUNG, encoding="utf-8")
    log_dir = tmp_path / "logs"

    exit_code = cli.main(
        [
            "--log-dir",
            str(log_dir),
            "--log-format",
            "json",
            "process",
            str(document),
            "--xml-dir",
            str(_XML_DIR),
            "--raw-store",
            str(tmp_path / "raw"),
            "--audit-log",
            str(tmp_path / "audit.jsonl"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err

    briefing = json.loads(captured.out)
    assert len(briefing["corpus_id"]) == 64
    int(briefing["corpus_id"], 16)
    created_at = datetime.fromisoformat(briefing["created_at"])
    assert created_at.tzinfo is not None
    assert created_at.utcoffset().total_seconds() == 0
    assert briefing["document_id"]
    assert len(briefing["entries"]) == 1
    assert briefing["entries"][0]["argument_id"]

    for handler in logging.getLogger().handlers:
        handler.flush()
    sink_lines = [
        json.loads(line)
        for line in (log_dir / LOG_FILENAME).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    startups = [line for line in sink_lines if line["event"] == STARTUP_CONFIG]
    assert len(startups) == 1
    assert startups[0]["corpus_id"] == briefing["corpus_id"]
    assert startups[0]["git_sha"]
    assert startups[0]["model_id"] == cli.TRIAGE_MODEL_ID
